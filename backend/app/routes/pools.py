from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies import current_owner
from ..models import Game, Pool, PoolEntry, PoolPick
from ..pool_errors import PoolDomainError
from ..schemas import (
    GameOut,
    GameResultUpdate,
    PoolCreate,
    PoolEntryCreate,
    PoolEntryOut,
    PoolOut,
    PoolOverviewOut,
    PoolRules,
    PoolWeekOut,
    SleeperPoolInfo,
    SleeperPoolRequest,
    WeeklyCardUpdate,
    WeeklyCardUpdateOut,
)
from ..services.pool_analysis import (
    PoolAnalysisRequest,
    PoolApplyRequest,
    analyze_pool,
    apply_pool_analysis,
    refresh_pool_sources,
)
from ..services.pool_outcomes import settle_season, standings
from ..services.pool_strategy import season_strategy
from ..services.pool_week import get_pool_week, pool_overview, save_weekly_card
from ..services.sleeper_pools import pool_source, preview_pool, save_import

Db = Annotated[Session, Depends(get_db)]
router = APIRouter(prefix="/api/v1", dependencies=[Depends(current_owner)])


def pool_out(pool: Pool) -> PoolOut:
    return PoolOut(
        id=pool.id,
        name=pool.name,
        pool_type=pool.pool_type,
        season=pool.season,
        rules=PoolRules.model_validate(json.loads(pool.rules_json)),
        entry_count=len(pool.entries),
        sleeper=pool_source(pool),
    )


@router.post("/integrations/sleeper/pools/preview", response_model=SleeperPoolInfo)
async def preview_sleeper_pool(payload: SleeperPoolRequest) -> SleeperPoolInfo:
    return await preview_pool(payload)


@router.post("/integrations/sleeper/pools/import", response_model=PoolOut)
async def import_sleeper_pool(payload: SleeperPoolRequest, db: Db) -> PoolOut:
    info = await preview_pool(payload)
    return pool_out(save_import(db, info))


@router.post("/pools/{pool_id}/sleeper/refresh", response_model=PoolOut)
async def refresh_sleeper_pool(pool_id: int, db: Db) -> PoolOut:
    pool = db.get(Pool, pool_id)
    source = pool_source(pool) if pool else None
    if source is None:
        raise PoolDomainError(
            status_code=404,
            code="sleeper_pool_not_found",
            message="This pool is not linked to Sleeper.",
        )
    payload = SleeperPoolRequest(url=source.url, username=source.username)
    db.rollback()  # Release the read transaction while waiting on Sleeper.
    info = await preview_pool(payload)
    return pool_out(save_import(db, info, expected_pool_id=pool_id))


@router.get("/pools/overview", response_model=PoolOverviewOut)
def overview(db: Db, include_inactive: bool = Query(default=False)) -> dict[str, object]:
    return pool_overview(db, include_inactive=include_inactive)


@router.get("/pools", response_model=list[PoolOut])
def list_pools(db: Db) -> list[PoolOut]:
    return [pool_out(pool) for pool in db.query(Pool).order_by(Pool.name).all()]


@router.get("/pools/{pool_id}/standings")
def read_standings(pool_id: int, db: Db) -> dict:
    pool = db.get(Pool, pool_id)
    if pool is None:
        raise PoolDomainError(
            status_code=404, code="pool_not_found", message="The pool was not found."
        )
    return standings(db, pool)


@router.get("/pools/{pool_id}/strategy")
def read_strategy(
    pool_id: int,
    db: Db,
    start_week: int = Query(default=1, ge=1, le=18),
    overlap_penalty: float = Query(default=0.15, ge=0, le=1),
) -> dict:
    pool = db.get(Pool, pool_id)
    if pool is None:
        raise PoolDomainError(
            status_code=404, code="pool_not_found", message="The pool was not found."
        )
    return season_strategy(db, pool, start_week, overlap_penalty)


@router.put("/games/{game_id}/result", response_model=GameOut)
def record_game_result(game_id: int, payload: GameResultUpdate, db: Db) -> Game:
    game = db.get(Game, game_id)
    if game is None:
        raise PoolDomainError(
            status_code=404, code="game_not_found", message="The game was not found."
        )
    if game.source == "nflverse":
        raise PoolDomainError(
            status_code=409,
            code="source_managed_result",
            message="Refresh nflverse to update this source-managed result.",
        )
    game.home_score, game.away_score = payload.home_score, payload.away_score
    game.completed = payload.home_score is not None
    settle_season(db, game.season)
    db.commit()
    return game


@router.post("/pools", response_model=PoolOut, status_code=201)
def create_pool(payload: PoolCreate, db: Db) -> PoolOut:
    pool = Pool(
        name=payload.name,
        pool_type=payload.pool_type,
        season=payload.season,
        rules_json=payload.rules.model_dump_json(),
    )
    db.add(pool)
    db.commit()
    db.refresh(pool)
    return pool_out(pool)


@router.put("/pools/{pool_id}", response_model=PoolOut)
def update_pool(pool_id: int, payload: PoolCreate, db: Db) -> PoolOut:
    pool = db.get(Pool, pool_id)
    if pool is None:
        raise PoolDomainError(
            status_code=404, code="pool_not_found", message="The pool was not found."
        )
    has_picks = db.query(PoolPick.id).join(PoolEntry).filter(PoolEntry.pool_id == pool_id).first()
    rules_changed = PoolRules.model_validate_json(pool.rules_json) != payload.rules
    if has_picks and (
        rules_changed or pool.pool_type != payload.pool_type or pool.season != payload.season
    ):
        raise PoolDomainError(
            status_code=409,
            code="pool_rules_locked",
            message=(
                "Pool rules and season are fixed once picks are saved, so historical grading "
                "stays consistent. Create a new pool for different rules."
            ),
        )
    if pool.sleeper_league_id and (
        rules_changed or pool.pool_type != payload.pool_type or pool.season != payload.season
    ):
        raise PoolDomainError(
            status_code=409,
            code="sleeper_managed_rules",
            message="Refresh Sleeper details to update this pool's rules and season.",
        )
    pool.name = payload.name
    pool.pool_type = payload.pool_type
    pool.season = payload.season
    pool.rules_json = payload.rules.model_dump_json()
    db.commit()
    return pool_out(pool)


@router.delete("/pools/{pool_id}", status_code=204)
def delete_pool(pool_id: int, db: Db) -> Response:
    # Foreign keys cascade to entries, weekly cards, and picks, not shared NFL games.
    result = db.execute(delete(Pool).where(Pool.id == pool_id))
    if result.rowcount == 0:
        db.rollback()
        raise PoolDomainError(
            status_code=404, code="pool_not_found", message="The pool was not found."
        )
    db.commit()
    return Response(status_code=204)


@router.post("/pools/{pool_id}/entries", response_model=PoolEntryOut, status_code=201)
def create_entry(pool_id: int, payload: PoolEntryCreate, db: Db) -> PoolEntry:
    if db.get(Pool, pool_id) is None:
        raise PoolDomainError(
            status_code=404, code="pool_not_found", message="The pool was not found."
        )
    entry = PoolEntry(pool_id=pool_id, name=payload.name)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.get("/pools/{pool_id}/entries", response_model=list[PoolEntryOut])
def list_entries(pool_id: int, db: Db) -> list[PoolEntry]:
    if db.get(Pool, pool_id) is None:
        raise PoolDomainError(
            status_code=404, code="pool_not_found", message="The pool was not found."
        )
    return db.query(PoolEntry).filter(PoolEntry.pool_id == pool_id).order_by(PoolEntry.name).all()


@router.get("/pools/{pool_id}/weeks/{week}", response_model=PoolWeekOut)
def read_pool_week(
    pool_id: int,
    week: int,
    db: Db,
    entry_id: int = Query(),
) -> dict[str, object]:
    if week < 1 or week > 30:
        raise PoolDomainError(
            status_code=422, code="invalid_week", message="Week must be between 1 and 30."
        )
    return get_pool_week(db, pool_id=pool_id, entry_id=entry_id, week=week)


@router.put(
    "/entries/{entry_id}/weeks/{week}/picks",
    response_model=WeeklyCardUpdateOut,
)
def update_weekly_card(
    entry_id: int,
    week: int,
    payload: WeeklyCardUpdate,
    db: Db,
) -> dict[str, object]:
    if week < 1 or week > 30:
        raise PoolDomainError(
            status_code=422, code="invalid_week", message="Week must be between 1 and 30."
        )
    return save_weekly_card(db, entry_id=entry_id, week=week, payload=payload)


@router.post("/pools/{pool_id}/weeks/{week}/check-in")
async def check_in_pool(
    pool_id: int,
    db: Db,
    week: int,
    entry_id: int = Query(),
    force: bool = False,
) -> dict:
    if not 1 <= week <= 30:
        raise PoolDomainError(status_code=422, code="invalid_week", message="Invalid pool week.")
    workspace = get_pool_week(db, pool_id=pool_id, entry_id=entry_id, week=week)
    return await refresh_pool_sources(db, workspace["pool"]["season"], force=force)


@router.post("/pools/{pool_id}/weeks/{week}/analysis")
async def analyze_pool_week(
    pool_id: int,
    week: int,
    payload: PoolAnalysisRequest,
    db: Db,
    entry_id: int = Query(),
) -> dict:
    if not 1 <= week <= 30:
        raise PoolDomainError(status_code=422, code="invalid_week", message="Invalid pool week.")
    return await analyze_pool(db, pool_id, entry_id, week, payload.provider_id)


@router.post("/pools/{pool_id}/weeks/{week}/analysis/apply", response_model=WeeklyCardUpdateOut)
async def apply_pool_week_analysis(
    pool_id: int,
    week: int,
    payload: PoolApplyRequest,
    db: Db,
    entry_id: int = Query(),
) -> dict:
    if not 1 <= week <= 30:
        raise PoolDomainError(status_code=422, code="invalid_week", message="Invalid pool week.")
    return await apply_pool_analysis(db, pool_id, entry_id, week, payload.run_id)
