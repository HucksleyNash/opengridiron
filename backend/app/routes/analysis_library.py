"""Searchable conversation summaries and independently addressable saved answers."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session, joinedload, load_only

from ..db import get_db
from ..dependencies import current_owner
from ..models import AnalysisRun, League, Pool, PoolEntry
from ..schemas import AnalysisRunOut

router = APIRouter(prefix="/api/v1/analysis", dependencies=[Depends(current_owner)])
Db = Annotated[Session, Depends(get_db)]
SCOPE_KEYS = ("league_id", "pool_id", "pool_entry_id", "draft_session_id", "team_name", "week")


def _object(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _json(column, path: str):
    return func.json_extract(case((func.json_valid(column), column), else_="{}"), path)


def _catalog(db: Session, search: str = "") -> dict[int, dict[str, Any]]:
    # Extract only scope and a bounded preview in SQLite. Large evidence dossiers
    # and complete model outputs must never become the library response payload.
    dossier = AnalysisRun.input_dossier_json
    match = (
        or_(
            func.lower(AnalysisRun.question).contains(search.lower(), autoescape=True),
            func.lower(AnalysisRun.output_json).contains(search.lower(), autoescape=True),
            func.lower(AnalysisRun.error).contains(search.lower(), autoescape=True),
        )
        if search
        else True
    )
    rows = db.query(
        AnalysisRun.id,
        AnalysisRun.parent_run_id,
        AnalysisRun.league_report_id,
        AnalysisRun.task,
        AnalysisRun.status,
        AnalysisRun.created_at,
        func.substr(AnalysisRun.question, 1, 240).label("question"),
        func.substr(_json(AnalysisRun.output_json, "$.summary"), 1, 240).label("preview"),
        func.substr(AnalysisRun.error, 1, 240).label("error"),
        _json(dossier, "$.data_access.scope").label("scope"),
        _json(dossier, "$.weekly_report.league_id").label("report_league"),
        _json(dossier, "$.league.id").label("league"),
        _json(dossier, "$.pool.id").label("pool"),
        _json(dossier, "$.weekly_report.team_name").label("report_team"),
        _json(dossier, "$.league.my_team_name").label("team"),
        _json(dossier, "$.weekly_report.week").label("report_week"),
        case((match, True), else_=False).label("matches"),
    ).all()
    leagues = dict(db.query(League.id, League.name).all())
    pools = dict(db.query(Pool.id, Pool.name).all())
    entries = dict(db.query(PoolEntry.id, PoolEntry.name).all())
    result = {}
    for row in rows:
        scope = {key: value for key, value in _object(row.scope).items() if key in SCOPE_KEYS}
        for key, value in {
            "league_id": row.report_league or row.league,
            "pool_id": row.pool,
            "team_name": row.report_team or row.team,
            "week": row.report_week,
        }.items():
            if scope.get(key) is None and value is not None:
                scope[key] = value
        for key, names in (("league", leagues), ("pool", pools), ("pool_entry", entries)):
            identifier = scope.get(f"{key}_id")
            if isinstance(identifier, (str, int)) and str(identifier).isdigit():
                scope[f"{key}_id"] = int(identifier)
                if int(identifier) in names:
                    scope[f"{key}_name"] = names[int(identifier)]
        result[row.id] = {
            "id": row.id,
            "parent_run_id": row.parent_run_id,
            "league_report_id": row.league_report_id,
            "task": row.task,
            "question": row.question or "",
            "status": row.status,
            "created_at": row.created_at,
            "context": scope,
            "preview": row.preview or row.error or "Analysis is still processing.",
            "matches": row.matches,
        }
    return result


def _root(identifier: int, rows: dict[int, dict]) -> int:
    path = set()
    current = identifier
    while current in rows and current not in path:
        path.add(current)
        parent = rows[current]["parent_run_id"]
        if parent not in rows:
            return current
        current = parent
    # Corrupt legacy cycles remain addressable without hanging or merging peers.
    return min(path)


def _title(row: dict) -> str:
    question = row["question"].strip()
    generated = (
        not question
        or row["task"] != "chat"
        or question.startswith(
            (
                "Analyze this pool entry",
                "Explain this weekly_report",
                "Explain the best draft choices",
            )
        )
    )
    if not generated:
        return question[:140]
    scope = row["context"]
    kind = (
        "Pool analysis"
        if scope.get("pool_id")
        else (
            "Draft review"
            if scope.get("draft_session_id")
            else (
                "League report"
                if row["league_report_id"]
                else "League review"
                if scope.get("league_id")
                else "Saved analysis"
            )
        )
    )
    week = f"Week {scope['week']} · " if scope.get("week") else ""
    name = scope.get("pool_name") or scope.get("league_name")
    return f"{week}{kind}{f' · {name}' if name else ''}"


@router.get("/library")
def library(
    db: Db,
    q: str = Query(default="", max_length=300),
    scope: str = Query(default="", max_length=50),
    week: int | None = Query(default=None, ge=1, le=18),
    status: Literal["completed", "failed", "queued", "running"] | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict:
    rows = _catalog(db, q.strip())
    groups: dict[int, list[dict]] = {}
    for identifier, row in rows.items():
        groups.setdefault(_root(identifier, rows), []).append(row)
    items = []
    for root_id, turns in groups.items():
        turns.sort(key=lambda row: (row["created_at"], row["id"]), reverse=True)
        root = rows[root_id]
        matches = []
        for row in turns:
            context = row["context"]
            scope_match = not scope or scope in (
                f"league:{context.get('league_id')}",
                f"pool:{context.get('pool_id')}",
            )
            week_match = week is None or str(context.get("week")) == str(week)
            text = " ".join(str(value) for value in context.values()) + " " + _title(root)
            search_match = (
                not q.strip() or row["matches"] or q.strip().casefold() in text.casefold()
            )
            if (
                scope_match
                and week_match
                and search_match
                and (not status or row["status"] == status)
            ):
                matches.append(row)
        if not matches:
            continue
        selected = matches[0]
        items.append(
            {
                "id": selected["id"],
                "root_id": root_id,
                "title": _title(root),
                "preview": selected["preview"],
                "context": selected["context"],
                "status": selected["status"],
                "created_at": root["created_at"],
                "updated_at": turns[0]["created_at"],
                "reply_count": len(turns) - 1,
                "latest_id": turns[0]["id"],
            }
        )
    items.sort(key=lambda item: (item["updated_at"], item["latest_id"]), reverse=True)
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "run_count": len(rows),
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < len(items),
    }


@router.get("/runs/{run_id}")
def conversation(run_id: int, db: Db) -> dict:
    rows = _catalog(db)
    if run_id not in rows:
        raise HTTPException(404, "This saved analysis is unavailable. Choose another from history.")
    path = []
    current = run_id
    while current in rows and current not in path:
        path.append(current)
        current = rows[current]["parent_run_id"]
    incomplete = current is not None
    root_id = _root(run_id, rows)
    records = {
        run.id: run
        for run in db.query(AnalysisRun)
        .options(
            joinedload(AnalysisRun.provider),
            load_only(
                *[
                    getattr(AnalysisRun, key)
                    for key in (
                        "id",
                        "task",
                        "question",
                        "model",
                        "status",
                        "output_json",
                        "error",
                        "created_at",
                        "completed_at",
                        "parent_run_id",
                        "league_report_id",
                        "provider_id",
                    )
                ]
            ),
        )
        .filter(AnalysisRun.id.in_(path))
        .all()
    }
    turns = []
    for identifier in reversed(path):
        run = records[identifier]
        turns.append(
            AnalysisRunOut(
                id=run.id,
                task=run.task,
                question=run.question,
                provider=run.provider.name if run.provider else None,
                model=run.model,
                status=run.status,
                output=_object(run.output_json) if run.output_json else None,
                error=run.error,
                created_at=run.created_at,
                completed_at=run.completed_at,
                parent_run_id=run.parent_run_id,
                league_report_id=run.league_report_id,
                context=rows[identifier]["context"],
            )
        )
    members = [row for row in rows.values() if _root(row["id"], rows) == root_id]
    parents = {row["parent_run_id"] for row in members}
    branches = [
        {
            "id": row["id"],
            "title": _title(row),
            "status": row["status"],
            "updated_at": row["created_at"],
        }
        for row in members
        if row["id"] not in parents and row["id"] != run_id
    ]
    branches.sort(key=lambda row: (row["updated_at"], row["id"]), reverse=True)
    return {
        "selected_id": run_id,
        "root_id": root_id,
        "title": _title(rows[root_id]),
        "context": rows[run_id]["context"],
        "turns": turns,
        "branches": branches,
        "lineage_incomplete": incomplete,
    }
