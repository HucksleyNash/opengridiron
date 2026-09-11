from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies import current_owner
from ..models import AnalysisProvider, AnalysisRun
from ..services import injury_report as service
from ..services.job_locks import job_lock

router = APIRouter(prefix="/api/v1/injuries", dependencies=[Depends(current_owner)])
Db = Annotated[Session, Depends(get_db)]


class CheckStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: int = Field(ge=1)


def runs_for(db: Session, key: str):
    return (
        db.query(AnalysisRun)
        .filter(
            AnalysisRun.task == service.TASK,
            func.json_extract(AnalysisRun.input_dossier_json, "$.injury_key") == key,
        )
        .order_by(AnalysisRun.id.desc())
    )


async def row_or_404(db: Session, key: str, refresh: bool = False) -> dict:
    board = await service.injury_board(db, refresh)
    row = next((r for r in board["items"] if r["key"] == key), None)
    if row is None:
        raise HTTPException(404, "Player is no longer in this injury report. Refresh the list.")
    return row


@router.get("")
async def list_injuries(
    db: Db,
    refresh: bool = False,
    search: str = Query(default="", max_length=160),
    league_id: int | None = Query(default=None, ge=1),
    mine: bool = False,
    position: str = Query(default="", max_length=16),
    team: str = Query(default="", max_length=40),
    status: str = Query(default="", max_length=40),
) -> dict:
    result = await service.injury_board(db, refresh)
    all_rows = result["items"]
    result["facets"] = {
        "positions": sorted({r["position"] for r in all_rows if r["position"]}),
        "teams": sorted({r["team"] for r in all_rows if r["team"]}),
        "statuses": sorted({r["game_status"] for r in all_rows}),
    }
    filtered = []
    for row in all_rows:
        memberships = [
            m for m in row["memberships"] if league_id is None or m["league_id"] == league_id
        ]
        if (
            league_id is not None
            and not memberships
            or mine
            and not any(m["is_mine"] for m in memberships)
        ):
            continue
        if (
            position
            and row["position"] != position
            or team
            and row["team"] != team
            or status
            and row["game_status"] != status
        ):
            continue
        haystack = f"{row['name']} {row['team']} {row['position']} {row['injury']}".lower()
        if any(term not in haystack for term in search.lower().split()):
            continue
        filtered.append(row)
    result.update(
        items=filtered,
        total=len(filtered),
        available=len(all_rows),
        my_players=sum(r["is_mine"] for r in all_rows),
    )
    return result


@router.get("/checks/{run_id}")
def get_check(run_id: int, db: Db) -> dict:
    run = db.get(AnalysisRun, run_id)
    if not run or run.task != service.TASK:
        raise HTTPException(404, "Injury check not found")
    return service.check_out(run)


@router.get("/{key}/checks")
def check_history(key: str, db: Db) -> list[dict]:
    return [service.check_out(run) for run in runs_for(db, key).limit(10).all()]


@router.get("/{key}")
async def get_injury(key: str, db: Db, refresh: bool = False) -> dict:
    return await service.injury_detail(db, await row_or_404(db, key, refresh), refresh)


@router.post("/{key}/checks", status_code=202)
async def start_check(key: str, payload: CheckStart, background: BackgroundTasks, db: Db) -> dict:
    provider = db.get(AnalysisProvider, payload.provider_id)
    if not provider or not provider.enabled:
        raise HTTPException(422, "Select an enabled analysis provider in Settings.")
    row = await row_or_404(db, key)
    with job_lock(f"injury-start-{row['key']}") as acquired:
        if not acquired:
            raise HTTPException(409, "An injury check is being started. Refresh the saved checks.")
        active = runs_for(db, key).filter(AnalysisRun.status.in_(service.ACTIVE_CHECKS)).first()
        if active:
            return service.check_out(active)
        dossier = {"injury_key": key, "target_game": row["next_game"], "player": row}
        run = AnalysisRun(
            task=service.TASK,
            provider_id=provider.id,
            model=provider.model,
            status="queued",
            question=f"Injury check: {row['name']} ({row['team']})",
            prompt_version="injury-v1",
            schema_version="injury-v1",
            input_hash=key,
            input_dossier_json=json.dumps(dossier),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        result = service.check_out(run)
        background.add_task(service.execute_check, run.id)
        return result
