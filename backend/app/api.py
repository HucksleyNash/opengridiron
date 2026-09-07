from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from sqlalchemy import bindparam, case, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from .config import settings
from .db import get_db
from .dependencies import current_owner
from .draft.models import DraftSession
from .draft.session import project_live_roster
from .models import (
    Alert,
    AnalysisProvider,
    AnalysisRun,
    DataSnapshot,
    DraftPick,
    Game,
    IdentityMap,
    League,
    NewsItem,
    NewsSource,
    Owner,
    Player,
    Pool,
    PoolEntry,
    PoolPick,
    PushSubscription,
    SecretSetting,
)
from .schemas import (
    AnalysisRequest,
    AnalysisResult,
    AnalysisRunOut,
    DraftPickCreate,
    DraftPickOut,
    GameCreate,
    GameOut,
    LeagueCreate,
    LeagueMyTeamUpdate,
    LeagueOut,
    LineupAssignment,
    LineupRecommendation,
    LoginRequest,
    NewsItemOut,
    NewsSourceCreate,
    NewsSourceOut,
    OwnerSetup,
    PlayerCreate,
    PlayerOut,
    PlayerSynopsisOut,
    PoolOut,
    PoolPickOut,
    PoolRules,
    ProviderCreate,
    ProviderModelsRequest,
    ProviderOut,
    ProviderUpdate,
    PushSubscriptionCreate,
    Recommendation,
    TradeRequest,
    YahooScraperSettings,
    YahooSettings,
)
from .security import (
    create_session,
    encrypt_secret,
    hash_password,
    new_csrf_token,
    verify_password,
)
from .services.backups import create_backup, list_backups, restore_backup
from .services.decision import (
    confidence_recommendations,
    evaluate_trade,
    optimize_lineup,
    rank_waivers,
    survivor_recommendations,
)
from .services.football_sources import (
    SourceRequest,
    source_evidence,
    source_summary,
)
from .services.football_sources import (
    refresh_source as refresh_football_source,
)
from .services.league_queries import waiver_page
from .services.modeling import (
    brier_score,
    list_model_artifacts,
    team_win_probability,
    train_projection_candidate,
)
from .services.news import fetch_source
from .services.nflverse import nfl_season_for_date
from .services.nflverse import sync_rosters as sync_nflverse_rosters
from .services.nflverse import sync_schedule as sync_nflverse_schedule
from .services.player_synopsis import player_reports
from .services.projection_context import (
    context_for,
    import_payload,
    public_ros_value,
    record_import_context,
)
from .services.projections import calibrated_interval, score_projection
from .services.providers import (
    adapter_for,
    analysis_context,
    discover_provider_models,
    run_analysis,
    safe_provider_error,
)
from .services.push import push_enabled, send_notification
from .services.yahoo import authorization_url, exchange_code, save_yahoo_settings, sync_leagues
from .services.yahoo_scraper import (
    save_scraper_settings,
    scraper_status,
    sync_scraped_league,
    sync_scraped_leagues,
)

Db = Annotated[Session, Depends(get_db)]


def _league_team_setup(db: Session, league: League) -> tuple[list[str], str | None]:
    if not league.yahoo_key:
        return [], None
    snapshot = (
        db.query(DataSnapshot)
        .filter(
            DataSnapshot.source == "yahoo_scrape",
            DataSnapshot.source_id == league.yahoo_key,
        )
        .order_by(DataSnapshot.id.desc())
        .first()
    )
    if not snapshot or not snapshot.payload_json:
        return [], None
    try:
        payload = json.loads(snapshot.payload_json)
    except (json.JSONDecodeError, AttributeError):
        return [], None

    draft_order = payload.get("draft_order", []) if isinstance(payload, dict) else []
    ordered_names: list[str] = []
    for team in draft_order if isinstance(draft_order, list) else []:
        name = " ".join(str(team.get("team_name") or "").split()) if isinstance(team, dict) else ""
        if name:
            ordered_names.append(name)
    if 8 <= len(ordered_names) <= 16:
        return ordered_names, "yahoo_draft_order"

    teams = payload.get("teams", []) if isinstance(payload, dict) else []
    names: list[str] = []
    for team in teams if isinstance(teams, list) else []:
        name = " ".join(str(team.get("name") or "").split()) if isinstance(team, dict) else ""
        if name:
            names.append(name)
    return names, "yahoo_team_id" if names else None


def _league_out(db: Session, league: League) -> LeagueOut:
    team_names, team_order_source = _league_team_setup(db, league)
    return LeagueOut(
        id=league.id,
        name=league.name,
        season=league.season,
        source=league.source,
        yahoo_key=league.yahoo_key,
        scoring=json.loads(league.scoring_json),
        roster_slots=json.loads(league.roster_slots_json),
        faab_budget=league.faab_budget,
        player_count=db.query(Player.id).filter(Player.league_id == league.id).count(),
        team_names=team_names,
        team_order_source=team_order_source,
        my_team_name=league.my_team_name,
    )


def _player_out(player: Player) -> PlayerOut:
    return PlayerOut(
        id=player.id,
        league_id=player.league_id,
        source_id=player.source_id,
        name=player.name,
        pro_team=player.pro_team,
        position=player.position,
        status=player.status,
        ownership=player.ownership,
        rostered_by=player.rostered_by,
        current_slot=player.current_slot,
        projected_points=player.projected_points,
        floor=player.floor,
        ceiling=player.ceiling,
        ros_value=public_ros_value(player),
        risk=player.risk,
        projection=context_for(player),
        evidence=json.loads(player.evidence_json),
    )


def _pool_out(pool: Pool) -> PoolOut:
    return PoolOut(
        id=pool.id,
        name=pool.name,
        pool_type=pool.pool_type,
        season=pool.season,
        rules=PoolRules.model_validate(json.loads(pool.rules_json)),
        entry_count=len(pool.entries),
    )


def _provider_out(db: Session, provider: AnalysisProvider) -> ProviderOut:
    has_api_key = False
    if provider.api_key_setting:
        has_api_key = db.get(SecretSetting, provider.api_key_setting) is not None
    elif provider.provider_type in {"openai", "codex"}:
        has_api_key = settings.openai_api_key is not None
    elif provider.provider_type == "anthropic":
        has_api_key = settings.anthropic_api_key is not None
    return ProviderOut(
        id=provider.id,
        name=provider.name,
        provider_type=provider.provider_type,
        model=provider.model,
        base_url=provider.base_url,
        enabled=provider.enabled,
        task_defaults=json.loads(provider.task_defaults_json),
        has_api_key=has_api_key,
    )


public_router = APIRouter(prefix="/api/v1")
router = APIRouter(prefix="/api/v1", dependencies=[Depends(current_owner)])


@public_router.get("/onboarding/status")
def onboarding_status(db: Db) -> dict[str, Any]:
    owner = db.query(Owner).first()
    return {
        "configured": owner is not None,
        "auth_required": settings.auth_required,
        "environment": settings.app_env,
        "timezone": settings.timezone,
        "yahoo_access_url": "https://sports.yahoo.com/developer/access/",
        "capabilities": {"draft_suite": settings.draft_suite_enabled},
    }


@public_router.post("/onboarding/setup", status_code=201)
def setup_owner(payload: OwnerSetup, response: Response, db: Db) -> dict[str, str]:
    if db.query(Owner).first():
        raise HTTPException(409, "Owner is already configured")
    owner = Owner(username=payload.username, password_hash=hash_password(payload.password))
    db.add(owner)
    db.commit()
    db.refresh(owner)
    if settings.auth_required:
        csrf = new_csrf_token()
        response.set_cookie(
            "session", create_session(owner.id), httponly=True, secure=True, samesite="lax"
        )
        response.set_cookie("csrf_token", csrf, secure=True, samesite="lax")
    return {"status": "configured"}


@public_router.post("/auth/login")
def login(payload: LoginRequest, response: Response, db: Db) -> dict[str, str]:
    owner = db.query(Owner).filter(Owner.username == payload.username).one_or_none()
    if not owner or not verify_password(owner.password_hash, payload.password):
        raise HTTPException(401, "Invalid username or password")
    csrf = new_csrf_token()
    response.set_cookie(
        "session",
        create_session(owner.id),
        httponly=True,
        secure=settings.auth_required,
        samesite="lax",
    )
    response.set_cookie("csrf_token", csrf, secure=settings.auth_required, samesite="lax")
    return {"status": "authenticated", "csrf_token": csrf}


@public_router.post("/auth/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie("session")
    response.delete_cookie("csrf_token")
    return {"status": "signed_out"}


@router.get("/system/health")
def health(db: Db) -> dict[str, Any]:
    return {
        "status": "ok",
        "environment": settings.app_env,
        "auth_required": settings.auth_required,
        "database": "ok" if db.query(func.count(League.id)).scalar() is not None else "error",
        "time": datetime.now(UTC).isoformat(),
    }


@router.get("/dashboard")
def dashboard(db: Db) -> dict[str, Any]:
    active_draft = (
        db.query(DraftSession)
        .filter(
            DraftSession.archived_at.is_(None),
            DraftSession.status.in_(["LIVE", "PAUSED", "READY"]),
        )
        .order_by(
            case((DraftSession.kind == "live", 0), else_=1),
            case({"LIVE": 0, "PAUSED": 1, "READY": 2}, value=DraftSession.status),
            DraftSession.created_at.desc(),
            DraftSession.id.desc(),
        )
        .first()
    )
    return {
        "active_draft": (
            {
                "id": active_draft.id,
                "league_id": active_draft.league_id,
                "kind": active_draft.kind,
                "status": active_draft.status,
            }
            if active_draft
            else None
        ),
        "leagues": [
            _league_out(db, league).model_dump()
            for league in db.query(League).order_by(League.name).all()
        ],
        "pools": [
            _pool_out(pool).model_dump() for pool in db.query(Pool).order_by(Pool.name).all()
        ],
        "alerts": [
            {
                "id": alert.id,
                "title": alert.title,
                "message": alert.message,
                "severity": alert.severity,
                "url": alert.url,
                "read": alert.read,
                "created_at": alert.created_at,
            }
            for alert in db.query(Alert).order_by(Alert.created_at.desc()).limit(20)
        ],
        "snapshots": [
            {
                "id": snapshot.id,
                "source": snapshot.source,
                "source_id": snapshot.source_id,
                "retrieved_at": snapshot.retrieved_at,
                "status": snapshot.status,
            }
            for snapshot in db.query(DataSnapshot)
            .order_by(DataSnapshot.retrieved_at.desc())
            .limit(12)
        ],
        "news_sources": [
            {
                "id": source.id,
                "name": source.name,
                "enabled": source.enabled,
                "official": source.official,
                "last_fetched_at": source.last_fetched_at,
            }
            for source in db.query(NewsSource)
            .filter(NewsSource.enabled.is_(True))
            .order_by(NewsSource.last_fetched_at.desc(), NewsSource.name)
            .all()
        ],
        "analysis_runs": [
            {
                "id": run.id,
                "task": run.task,
                "model": run.model,
                "status": run.status,
                "created_at": run.created_at,
            }
            for run in db.query(AnalysisRun).order_by(AnalysisRun.created_at.desc()).limit(10)
        ],
    }


@router.get("/leagues", response_model=list[LeagueOut])
def list_leagues(db: Db) -> list[LeagueOut]:
    return [_league_out(db, league) for league in db.query(League).order_by(League.name).all()]


@router.post("/leagues", response_model=LeagueOut, status_code=201)
def create_league(payload: LeagueCreate, db: Db) -> LeagueOut:
    league = League(
        name=payload.name,
        season=payload.season,
        scoring_json=json.dumps(payload.scoring),
        roster_slots_json=json.dumps(payload.roster_slots),
        faab_budget=payload.faab_budget,
    )
    db.add(league)
    db.commit()
    db.refresh(league)
    return _league_out(db, league)


@router.get("/leagues/{league_id}", response_model=LeagueOut)
def get_league(league_id: int, db: Db) -> LeagueOut:
    league = db.get(League, league_id)
    if not league:
        raise HTTPException(404, "League not found")
    return _league_out(db, league)


@router.put("/leagues/{league_id}", response_model=LeagueOut)
def update_league(league_id: int, payload: LeagueCreate, db: Db) -> LeagueOut:
    league = db.get(League, league_id)
    if not league:
        raise HTTPException(404, "League not found")
    league.name = payload.name
    league.season = payload.season
    league.scoring_json = json.dumps(payload.scoring)
    league.roster_slots_json = json.dumps(payload.roster_slots)
    league.faab_budget = payload.faab_budget
    db.commit()
    return _league_out(db, league)


@router.put("/leagues/{league_id}/my-team", response_model=LeagueOut)
def update_my_team(league_id: int, payload: LeagueMyTeamUpdate, db: Db) -> LeagueOut:
    league = db.get(League, league_id)
    if not league:
        raise HTTPException(404, "League not found")
    name = payload.my_team_name
    if name is not None:
        imported_names, _ = _league_team_setup(db, league)
        roster_exists = (
            db.query(Player.id)
            .filter(Player.league_id == league_id, Player.rostered_by == name)
            .first()
        )
        if name not in imported_names and not roster_exists:
            raise HTTPException(422, "Choose a team from this league's imported teams or rosters.")
    league.my_team_name = name
    db.commit()
    return _league_out(db, league)


@router.post("/leagues/{league_id}/sync/draft-roster")
def sync_completed_draft_roster(league_id: int, db: Db) -> dict[str, object]:
    if not db.get(League, league_id):
        raise HTTPException(404, "League not found")
    session = (
        db.query(DraftSession)
        .filter(
            DraftSession.league_id == league_id,
            DraftSession.kind == "live",
            DraftSession.status == "COMPLETE",
        )
        .order_by(DraftSession.completed_at.desc(), DraftSession.id.desc())
        .first()
    )
    if session is None:
        raise HTTPException(409, "Complete a live draft before importing its roster")
    result = project_live_roster(db, session)
    db.commit()
    return result


@router.post("/leagues/{league_id}/sync/yahoo-scraper")
async def sync_league_from_yahoo_scraper(league_id: int, db: Db) -> dict[str, Any]:
    league = db.get(League, league_id)
    if not league:
        raise HTTPException(404, "League not found")
    if not league.yahoo_key:
        raise HTTPException(409, "This league is not linked to Yahoo")
    try:
        result = await sync_scraped_league(db, league_id)
    except Exception as exc:
        raise HTTPException(502, f"Yahoo scraper sync failed: {str(exc)[:300]}") from exc
    return {**result, "league_id": league_id}


@router.delete("/leagues/{league_id}", status_code=204)
def delete_league(league_id: int, db: Db) -> Response:
    league = db.get(League, league_id)
    if not league:
        raise HTTPException(404, "League not found")
    db.delete(league)
    db.commit()
    return Response(status_code=204)


@router.get("/leagues/{league_id}/players", response_model=list[PlayerOut])
def list_players(
    league_id: int,
    db: Db,
    ownership: str | None = None,
    position: str | None = None,
) -> list[PlayerOut]:
    query = db.query(Player).filter(Player.league_id == league_id)
    if ownership:
        query = query.filter(Player.ownership == ownership)
    if position:
        query = query.filter(Player.position == position.upper())
    return [
        _player_out(player) for player in query.order_by(Player.ros_value.desc(), Player.name).all()
    ]


@router.post("/leagues/{league_id}/players", response_model=PlayerOut, status_code=201)
def create_player(league_id: int, payload: PlayerCreate, db: Db) -> PlayerOut:
    if not db.get(League, league_id):
        raise HTTPException(404, "League not found")
    duplicate = (
        db.query(Player)
        .filter(
            Player.league_id == league_id,
            func.lower(Player.name) == payload.name.lower(),
            Player.pro_team == payload.pro_team,
            Player.position == payload.position,
        )
        .first()
    )
    if duplicate:
        raise HTTPException(409, "This player already exists in the selected league")
    player = Player(
        league_id=league_id,
        source_id=payload.source_id,
        name=payload.name,
        pro_team=payload.pro_team,
        position=payload.position,
        status=payload.status,
        ownership=payload.ownership,
        rostered_by=payload.rostered_by,
        current_slot=payload.current_slot,
        projected_points=payload.projected_points,
        floor=payload.floor,
        ceiling=payload.ceiling,
        ros_value=payload.ros_value if payload.ros_value is not None else 0,
        risk=payload.risk,
        evidence_json=json.dumps(payload.evidence),
    )
    record_import_context(player, payload, "Manual entry")
    db.add(player)
    db.commit()
    db.refresh(player)
    return _player_out(player)


@router.get("/players/directory")
def player_directory(db: Db) -> list[dict[str, object]]:
    """Lightweight identities for player mentions; do not fetch reports or projections."""
    rows = (
        db.query(
            Player.id,
            Player.league_id,
            Player.name,
            Player.pro_team,
            Player.position,
            League.name.label("league_name"),
        )
        .join(League, Player.league_id == League.id)
        .order_by(Player.name, Player.league_id, Player.id)
        .all()
    )
    return [dict(row._mapping) for row in rows]


@router.get("/players/{player_id}", response_model=PlayerOut)
def get_player(player_id: int, db: Db) -> PlayerOut:
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(404, "Player not found")
    return _player_out(player)


@router.get("/players/{player_id}/synopsis", response_model=PlayerSynopsisOut)
async def get_player_synopsis(player_id: int, db: Db, refresh: bool = False) -> PlayerSynopsisOut:
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(404, "Player not found")
    reports = await player_reports(db, player, refresh)
    return PlayerSynopsisOut(player=_player_out(player), **reports)


@router.put("/players/{player_id}", response_model=PlayerOut)
def update_player(player_id: int, payload: PlayerCreate, db: Db) -> PlayerOut:
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(404, "Player not found")
    for field in (
        "source_id",
        "name",
        "pro_team",
        "position",
        "status",
        "ownership",
        "rostered_by",
        "current_slot",
        "projected_points",
        "floor",
        "ceiling",
        "risk",
    ):
        setattr(player, field, getattr(payload, field))
    player.evidence_json = json.dumps(payload.evidence)
    player.ros_value = payload.ros_value if payload.ros_value is not None else 0
    record_import_context(player, payload, "Manual entry")
    db.commit()
    return _player_out(player)


@router.post("/leagues/{league_id}/players/import")
async def import_players(league_id: int, db: Db, file: UploadFile = File(...)) -> dict[str, int]:
    if not db.get(League, league_id):
        raise HTTPException(404, "League not found")
    content = (await file.read()).decode("utf-8-sig")
    if file.filename and file.filename.lower().endswith(".json"):
        rows = json.loads(content)
    else:
        rows = list(csv.DictReader(io.StringIO(content)))
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise HTTPException(422, "Import must contain an array of player objects")
    # Validate every row before writing any record: malformed imports are atomic.
    try:
        payloads = [import_payload(row) for row in rows]
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"Invalid player import: {exc}") from exc
    created = updated = 0
    for payload in payloads:
        source_id = payload.source_id
        player = (
            db.query(Player)
            .filter(Player.league_id == league_id, Player.source_id == source_id)
            .one_or_none()
        )
        values = payload.model_dump(exclude={"projection", "evidence", "source_id"})
        values["ros_value"] = payload.ros_value if payload.ros_value is not None else 0
        if player:
            for key, value in values.items():
                setattr(player, key, value)
            updated += 1
        else:
            player = Player(league_id=league_id, source_id=source_id, **values)
            db.add(player)
            created += 1
        record_import_context(player, payload, "Player file import")
    db.add(
        DataSnapshot(
            source="projection_import",
            source_id=file.filename,
            payload_json=json.dumps({"rows": len(rows)}),
        )
    )
    db.commit()
    return {"created": created, "updated": updated}


@router.get("/leagues/{league_id}/lineup", response_model=LineupRecommendation)
def recommend_lineup(
    league_id: int,
    db: Db,
    mode: str = Query(default="balanced", pattern="^(floor|balanced|ceiling)$"),
    team_name: str | None = Query(default=None, max_length=160),
) -> LineupRecommendation:
    league = db.get(League, league_id)
    if not league:
        raise HTTPException(404, "League not found")
    normalized_team = " ".join(team_name.split()) if team_name else None
    roster_query = db.query(Player).filter(
        Player.league_id == league_id, Player.rostered_by.isnot(None), Player.rostered_by != ""
    )
    if normalized_team:
        roster_query = roster_query.filter(Player.rostered_by == normalized_team)
    roster = roster_query.all()
    result = optimize_lineup(roster, json.loads(league.roster_slots_json), mode)  # type: ignore[arg-type]
    return LineupRecommendation(
        mode=mode,  # type: ignore[arg-type]
        assignments=[
            LineupAssignment(slot=slot, player=_player_out(player), score=score)
            for slot, player, score in result.assignments
        ],
        projected_total=result.projected_total,
        current_total=result.current_total,
        projected_gain=round(result.projected_total - result.current_total, 2),
        unfilled_slots=result.unfilled_slots,
        data_as_of=datetime.now(UTC),
    )


@router.get("/leagues/{league_id}/waivers", response_model=list[Recommendation])
def recommend_waivers(
    league_id: int, db: Db, team_name: str | None = Query(default=None, max_length=160)
) -> list[Recommendation]:
    league = db.get(League, league_id)
    if not league:
        raise HTTPException(404, "League not found")
    return rank_waivers(
        league.players,
        json.loads(league.roster_slots_json),
        team_name=team_name or league.my_team_name,
        season=league.season,
    )


@router.get("/leagues/{league_id}/roster", response_model=list[PlayerOut])
def league_roster(league_id: int, db: Db) -> list[PlayerOut]:
    if not db.get(League, league_id):
        raise HTTPException(404, "League not found")
    return [
        _player_out(p).model_copy(update={"evidence": []})
        for p in db.query(Player)
        .filter(
            Player.league_id == league_id, Player.rostered_by.isnot(None), Player.rostered_by != ""
        )
        .order_by(Player.id)
        .all()
    ]


@router.get("/leagues/{league_id}/projection-leaders", response_model=list[PlayerOut])
def league_projection_leaders(league_id: int, db: Db) -> list[PlayerOut]:
    if not db.get(League, league_id):
        raise HTTPException(404, "League not found")
    return [
        _player_out(p).model_copy(update={"evidence": []})
        for p in db.query(Player)
        .filter(Player.league_id == league_id)
        .order_by(Player.projected_points.desc(), Player.id)
        .limit(10)
        .all()
    ]


@router.get("/leagues/{league_id}/waivers/page")
def paged_waivers(
    league_id: int,
    db: Db,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=50),
    search: str = Query(default="", max_length=160),
    role: str = Query(default="", max_length=16),
    team: str = Query(default="", max_length=8),
    status: str = Query(default="", max_length=40),
    availability: Literal["", "free-agent", "waivers"] = "",
    team_name: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    league = db.get(League, league_id)
    if not league:
        raise HTTPException(404, "League not found")
    return waiver_page(
        db,
        league,
        _player_out,
        offset=offset,
        limit=limit,
        search=search,
        role=role,
        team=team,
        status=status,
        availability=availability,
        team_name=team_name or league.my_team_name,
    )


@router.post("/leagues/{league_id}/trades/evaluate")
def trade_evaluation(league_id: int, payload: TradeRequest, db: Db) -> dict[str, object]:
    ids = set(payload.outgoing_player_ids + payload.incoming_player_ids)
    players = db.query(Player).filter(Player.league_id == league_id, Player.id.in_(ids)).all()
    by_id = {player.id: player for player in players}
    if len(by_id) != len(ids):
        raise HTTPException(400, "Every trade player must exist in the selected league")
    return evaluate_trade(
        [by_id[player_id] for player_id in payload.outgoing_player_ids],
        [by_id[player_id] for player_id in payload.incoming_player_ids],
    )


@router.get("/leagues/{league_id}/draft", response_model=list[DraftPickOut])
def list_draft_picks(league_id: int, db: Db) -> list[DraftPick]:
    return (
        db.query(DraftPick)
        .filter(DraftPick.league_id == league_id)
        .order_by(DraftPick.overall)
        .all()
    )


@router.post("/leagues/{league_id}/draft", response_model=DraftPickOut, status_code=201)
def create_draft_pick(league_id: int, payload: DraftPickCreate, db: Db) -> DraftPick:
    pick = DraftPick(league_id=league_id, **payload.model_dump())
    db.add(pick)
    db.commit()
    db.refresh(pick)
    return pick


@router.get("/leagues/{league_id}/draft/recommendations", response_model=list[PlayerOut])
def draft_recommendations(
    league_id: int, db: Db, limit: int = Query(default=20, ge=1, le=100)
) -> list[PlayerOut]:
    drafted_ids = {
        row[0]
        for row in db.query(DraftPick.player_id).filter(DraftPick.league_id == league_id).all()
        if row[0]
    }
    query = db.query(Player).filter(Player.league_id == league_id)
    if drafted_ids:
        query = query.filter(~Player.id.in_(drafted_ids))
    return [_player_out(player) for player in query.order_by(Player.ros_value.desc()).limit(limit)]


@router.get("/games", response_model=list[GameOut])
def list_games(db: Db, season: int | None = None, week: int | None = None) -> list[Game]:
    query = db.query(Game)
    if season:
        query = query.filter(Game.season == season)
    if week:
        query = query.filter(Game.week == week)
    return query.order_by(Game.kickoff).all()


@router.post("/games", response_model=GameOut, status_code=201)
def create_game(payload: GameCreate, db: Db) -> Game:
    game = Game(
        **payload.model_dump(),
        win_probability_kind="manual",
        cover_probability_kind="manual",
    )
    db.add(game)
    db.commit()
    db.refresh(game)
    return game


@router.post("/sync/nflverse/schedule")
async def nflverse_schedule_sync(
    db: Db,
    season: int = Query(ge=2000, le=2100),
    trigger: Literal["missing", "scheduled", "retry"] = "retry",
) -> dict[str, int | str]:
    return await sync_nflverse_schedule(db, season, trigger=trigger)


@router.post("/sync/nflverse/rosters")
async def nflverse_roster_sync(
    db: Db, season: int = Query(ge=2000, le=2100)
) -> dict[str, int | str]:
    try:
        return await sync_nflverse_rosters(db, season)
    except Exception as exc:
        raise HTTPException(502, f"nflverse roster sync failed: {exc}") from exc


@router.post("/leagues/{league_id}/projections/score")
def score_raw_projection(league_id: int, payload: dict[str, Any], db: Db) -> dict[str, Any]:
    league = db.get(League, league_id)
    if not league:
        raise HTTPException(404, "League not found")
    raw_stats = payload.get("raw_stats")
    if not isinstance(raw_stats, dict):
        raise HTTPException(400, "raw_stats must be an object of numeric forecasts")
    try:
        score = score_projection(raw_stats, json.loads(league.scoring_json))
        floor, ceiling, sigma = calibrated_interval(
            score,
            str(payload.get("position") or "UNK"),
            int(payload.get("history_games") or 0),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"Invalid projection: {exc}") from exc
    return {
        "fantasy_score": score,
        "floor": floor,
        "ceiling": ceiling,
        "standard_error": sigma,
        "model_version": "baseline-2026.1",
        "scoring": json.loads(league.scoring_json),
    }


@router.post("/modeling/team-probability")
def calculate_team_probability(payload: dict[str, Any]) -> dict[str, float | str | bool]:
    try:
        return team_win_probability(**payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"Invalid probability inputs: {exc}") from exc


@router.post("/modeling/backtest/brier")
def calculate_brier(payload: dict[str, Any]) -> dict[str, float]:
    try:
        return {"brier_score": brier_score(payload["probabilities"], payload["outcomes"])}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"Invalid backtest inputs: {exc}") from exc


@router.post("/modeling/projections/train")
def train_projection(payload: dict[str, Any]) -> dict[str, Any]:
    records = payload.get("records")
    features = payload.get("features")
    if not isinstance(records, list) or not isinstance(features, list) or len(records) > 250_000:
        raise HTTPException(400, "Provide features and no more than 250,000 projection records")
    try:
        return train_projection_candidate(records, [str(value) for value in features])
    except (RuntimeError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/modeling/artifacts")
def model_artifacts() -> list[dict[str, Any]]:
    return [
        {"filename": path.name, "size": path.stat().st_size, "created_at": path.stat().st_mtime}
        for path in list_model_artifacts()
    ]


@router.get("/entries/{entry_id}/picks", response_model=list[PoolPickOut])
def list_picks(entry_id: int, db: Db) -> list[PoolPick]:
    return (
        db.query(PoolPick)
        .filter(PoolPick.entry_id == entry_id)
        .order_by(PoolPick.week, PoolPick.slot)
        .all()
    )


@router.get("/entries/{entry_id}/survivor-recommendations", response_model=list[Recommendation])
def survivor_picks(entry_id: int, week: int, db: Db) -> list[Recommendation]:
    entry = db.get(PoolEntry, entry_id)
    if not entry:
        raise HTTPException(404, "Pool entry not found")
    current = [
        game
        for game in db.query(Game).filter(Game.season == entry.pool.season, Game.week == week).all()
        if game.away_team.strip() and game.home_team.strip() and game.away_team != game.home_team
    ]
    future = [
        game
        for game in db.query(Game).filter(Game.season == entry.pool.season, Game.week > week).all()
        if game.away_team.strip() and game.home_team.strip() and game.away_team != game.home_team
    ]
    return survivor_recommendations(entry.pool, entry, current, week, future)


@router.get("/pools/{pool_id}/confidence-recommendations")
def confidence_picks(pool_id: int, week: int, db: Db) -> list[dict[str, object]]:
    pool = db.get(Pool, pool_id)
    if not pool:
        raise HTTPException(404, "Pool not found")
    games = [
        game
        for game in db.query(Game).filter(Game.season == pool.season, Game.week == week).all()
        if game.away_team.strip() and game.home_team.strip() and game.away_team != game.home_team
    ]
    return confidence_recommendations(pool, games)


@router.get("/news/sources", response_model=list[NewsSourceOut])
def list_news_sources(db: Db) -> list[NewsSource]:
    return db.query(NewsSource).order_by(NewsSource.name).all()


def _football_source_request(key: str, season: int | None, scoring_format: str, teams: int):
    try:
        return SourceRequest(
            key, season or nfl_season_for_date(datetime.now(UTC)), scoring_format, teams
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/data-sources")
def list_football_data_sources(
    db: Db, season: int | None = None, scoring_format: str = "ppr", teams: int = 12
) -> list[dict]:
    return [
        source_summary(
            source_evidence(db, _football_source_request(key, season, scoring_format, teams))
        )
        for key in ("sleeper", "ffc")
    ]


@router.post("/data-sources/{source_key}/fetch")
async def fetch_football_data_source(
    source_key: Literal["sleeper", "ffc"],
    db: Db,
    season: int | None = None,
    scoring_format: str = "ppr",
    teams: int = 12,
) -> dict:
    return await refresh_football_source(
        _football_source_request(source_key, season, scoring_format, teams)
    )


@router.post("/news/sources", response_model=NewsSourceOut, status_code=201)
def create_news_source(payload: NewsSourceCreate, db: Db) -> NewsSource:
    source = NewsSource(
        name=payload.name,
        url=str(payload.url),
        source_type=payload.source_type,
        official=payload.official,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.post("/news/sources/{source_id}/fetch")
async def fetch_news_source(source_id: int, db: Db) -> dict[str, int | str]:
    source = db.get(NewsSource, source_id)
    if not source:
        raise HTTPException(404, "News source not found")
    try:
        before_id = db.query(func.max(Alert.id)).scalar() or 0
        result = await fetch_source(db, source)
        for alert in db.query(Alert).filter(Alert.id > before_id).all():
            send_notification(db, alert.title, alert.message, alert.url)
        return result
    except Exception as exc:
        raise HTTPException(502, f"Source fetch failed: {exc}") from exc


@router.get("/news/items", response_model=list[NewsItemOut])
def list_news_items(
    db: Db, category: str | None = None, limit: int = Query(default=100, ge=1, le=500)
) -> list[NewsItem]:
    query = db.query(NewsItem)
    if category:
        query = query.filter(NewsItem.category == category)
    return (
        query.order_by(NewsItem.published_at.desc(), NewsItem.retrieved_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/alerts")
def list_alerts(db: Db, unread_only: bool = False) -> list[dict[str, Any]]:
    query = db.query(Alert)
    if unread_only:
        query = query.filter(Alert.read.is_(False))
    return [
        {
            "id": alert.id,
            "title": alert.title,
            "message": alert.message,
            "severity": alert.severity,
            "url": alert.url,
            "read": alert.read,
            "created_at": alert.created_at,
        }
        for alert in query.order_by(Alert.created_at.desc()).limit(100)
    ]


@router.post("/alerts/{alert_id}/read")
def mark_alert_read(alert_id: int, db: Db) -> dict[str, bool]:
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(404, "Alert not found")
    alert.read = True
    db.commit()
    return {"read": True}


@router.get("/identity")
def list_identity_maps(db: Db) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "canonical_name": row.canonical_name,
            "pro_team": row.pro_team,
            "position": row.position,
            "yahoo_key": row.yahoo_key,
            "gsis_id": row.gsis_id,
            "confidence": row.confidence,
            "manually_verified": row.manually_verified,
        }
        for row in db.query(IdentityMap)
        .order_by(IdentityMap.confidence, IdentityMap.canonical_name)
        .all()
    ]


@router.post("/integrations/yahoo/settings")
def configure_yahoo(payload: YahooSettings, db: Db) -> dict[str, str]:
    save_yahoo_settings(db, payload.client_id, payload.client_secret, payload.redirect_uri)
    return {"status": "configured"}


@router.get("/integrations/yahoo/start")
def start_yahoo(db: Db) -> dict[str, str]:
    try:
        return {"authorization_url": authorization_url(db)}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@public_router.get("/integrations/yahoo/callback")
async def yahoo_callback(code: str, state: str, db: Db) -> RedirectResponse:
    try:
        await exchange_code(db, code, state)
    except Exception as exc:
        return RedirectResponse(f"/settings?yahoo=error&message={str(exc)[:120]}")
    return RedirectResponse("/settings?yahoo=connected")


@router.post("/integrations/yahoo/sync")
async def yahoo_sync(db: Db) -> dict[str, int]:
    try:
        return await sync_leagues(db)
    except Exception as exc:
        raise HTTPException(502, f"Yahoo sync failed: {exc}") from exc


@router.get("/integrations/yahoo/scraper/status")
def yahoo_scraper_status(db: Db) -> dict[str, Any]:
    return scraper_status(db)


@router.post("/integrations/yahoo/scraper/settings")
def configure_yahoo_scraper(payload: YahooScraperSettings, db: Db) -> dict[str, Any]:
    try:
        save_scraper_settings(db, payload.league_urls, payload.cookie)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return scraper_status(db)


@router.post("/integrations/yahoo/scraper/sync")
async def yahoo_scraper_sync(db: Db) -> dict[str, Any]:
    try:
        return await sync_scraped_leagues(db)
    except Exception as exc:
        raise HTTPException(502, f"Yahoo scraper sync failed: {str(exc)[:300]}") from exc


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(db: Db) -> list[ProviderOut]:
    return [
        _provider_out(db, provider)
        for provider in db.query(AnalysisProvider).order_by(AnalysisProvider.name).all()
    ]


@router.post("/providers/models")
async def provider_models(payload: ProviderModelsRequest) -> dict[str, Any]:
    api_key = (payload.api_key or "").strip()
    if payload.provider_type == "codex":
        try:
            result = await _codex_runner_request(
                "POST",
                "/v1/models",
                timeout=30,
                json={"api_key": api_key or settings.openai_api_key},
            )
        except HTTPException:
            raise
        models = result.get("models")
        if not isinstance(models, list) or not all(isinstance(model, str) for model in models):
            raise HTTPException(502, "The Codex runner returned an invalid model list.")
        if not models:
            raise HTTPException(502, "Codex returned no available models.")
        return {"models": models}
    if not api_key:
        api_key = (
            settings.openai_api_key
            if payload.provider_type == "openai"
            else settings.anthropic_api_key
        ) or ""
    if not api_key:
        raise HTTPException(
            409,
            f"Enter an {payload.provider_type.title()} API key to load available models.",
        )
    try:
        models = await discover_provider_models(payload.provider_type, api_key)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        detail = (
            "The API key was rejected."
            if status in {401, 403}
            else f"The provider returned HTTP {status}."
        )
        raise HTTPException(
            502, f"Could not load {payload.provider_type.title()} models. {detail}"
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            502,
            f"Could not load {payload.provider_type.title()} models: {str(exc)[:300]}",
        ) from exc
    if not models:
        raise HTTPException(502, f"{payload.provider_type.title()} returned no available models.")
    return {"models": models}


async def _codex_runner_request(
    method: str,
    path: str,
    *,
    timeout: float = 10,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method,
                f"{settings.codex_runner_url.rstrip('/')}{path}",
                headers={"X-Runner-Token": settings.codex_runner_token},
                json=json,
            )
        if response.is_error:
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = None
            raise HTTPException(
                response.status_code,
                detail or "The Codex runner request failed.",
            )
        return response.json()
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"Codex runner request failed: {str(exc)[:300]}") from exc


async def _codex_auth_request(method: str) -> dict[str, Any]:
    return await _codex_runner_request(method, "/v1/auth/device")


@router.get("/providers/codex/auth")
async def codex_auth_status() -> dict[str, Any]:
    return await _codex_auth_request("GET")


@router.post("/providers/codex/auth")
async def codex_auth_start() -> dict[str, Any]:
    return await _codex_auth_request("POST")


@router.post("/providers", response_model=ProviderOut, status_code=201)
def create_provider(payload: ProviderCreate, db: Db) -> ProviderOut:
    setting_key = f"provider.{uuid4().hex}.api_key" if payload.api_key else None
    if payload.api_key and setting_key:
        db.add(SecretSetting(key=setting_key, encrypted_value=encrypt_secret(payload.api_key)))
    provider = AnalysisProvider(
        name=payload.name,
        provider_type=payload.provider_type,
        model=payload.model,
        base_url=payload.base_url,
        api_key_setting=setting_key,
        enabled=payload.enabled,
        task_defaults_json="[]",
    )
    db.add(provider)
    try:
        _assign_provider_defaults(db, provider, payload.task_defaults)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "A provider with this name already exists") from exc
    db.refresh(provider)
    return _provider_out(db, provider)


def _assign_provider_defaults(db: Session, provider: AnalysisProvider, tasks: list[str]) -> None:
    """SQLite serializes this write before defaults are reassigned in the same transaction."""
    tasks = list(dict.fromkeys(tasks))
    db.flush()
    if tasks:
        statement = text(
            "UPDATE analysis_providers SET task_defaults_json = "
            "(SELECT json_group_array(value) FROM json_each(task_defaults_json) "
            "WHERE value NOT IN :tasks)"
        ).bindparams(bindparam("tasks", expanding=True))
        db.execute(statement, {"tasks": tasks})
        db.expire_all()
    provider.task_defaults_json = json.dumps(tasks)
    db.flush()


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
def update_provider(provider_id: int, payload: ProviderUpdate, db: Db) -> ProviderOut:
    provider = db.get(AnalysisProvider, provider_id)
    if provider is None:
        raise HTTPException(404, "Provider not found")
    for key in ("name", "model", "base_url", "enabled"):
        if key in payload.model_fields_set:
            setattr(provider, key, getattr(payload, key))
    if "api_key" in payload.model_fields_set:
        stored = (
            db.get(SecretSetting, provider.api_key_setting) if provider.api_key_setting else None
        )
        if payload.api_key:
            if stored:
                stored.encrypted_value = encrypt_secret(payload.api_key)
            else:
                provider.api_key_setting = f"provider.{uuid4().hex}.api_key"
                db.add(
                    SecretSetting(
                        key=provider.api_key_setting,
                        encrypted_value=encrypt_secret(payload.api_key),
                    )
                )
        else:
            provider.api_key_setting = None
            if stored:
                db.delete(stored)
    try:
        if "task_defaults" in payload.model_fields_set:
            _assign_provider_defaults(db, provider, payload.task_defaults or [])
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "A provider with this name already exists") from exc
    db.refresh(provider)
    return _provider_out(db, provider)


@router.delete("/providers/{provider_id}", status_code=204)
def delete_provider(provider_id: int, db: Db) -> Response:
    provider = db.get(AnalysisProvider, provider_id)
    if not provider:
        raise HTTPException(404, "Provider not found")

    stored_key = (
        db.get(SecretSetting, provider.api_key_setting) if provider.api_key_setting else None
    )
    db.delete(provider)
    if stored_key:
        db.delete(stored_key)
    db.commit()
    return Response(status_code=204)


@router.post("/providers/{provider_id}/health")
async def provider_health(provider_id: int, db: Db) -> dict[str, Any]:
    provider = db.get(AnalysisProvider, provider_id)
    if not provider:
        raise HTTPException(404, "Provider not found")
    try:
        return await adapter_for(db, provider).health()
    except Exception as exc:
        return {"ok": False, "error": safe_provider_error(exc)}


@router.post("/analysis", response_model=AnalysisResult)
async def analyze(payload: AnalysisRequest, db: Db) -> AnalysisResult:
    provider = db.get(AnalysisProvider, payload.provider_id) if payload.provider_id else None
    if payload.provider_id is not None and (provider is None or not provider.enabled):
        raise HTTPException(422, "Select an enabled analysis provider")
    if not provider:
        provider = next(
            (
                candidate
                for candidate in db.query(AnalysisProvider)
                .filter(AnalysisProvider.enabled.is_(True))
                .order_by(AnalysisProvider.id)
                .all()
                if payload.task in json.loads(candidate.task_defaults_json)
            ),
            None,
        )
    if not provider:
        raise HTTPException(409, "No enabled provider is configured for this task")
    context_options = {
        key: getattr(payload, key)
        for key in ("league_report_id", "parent_run_id", "pool_entry_id", "team_name", "week")
        if getattr(payload, key) is not None
    }
    run = await run_analysis(
        db,
        provider,
        payload.task,
        payload.question,
        payload.league_id,
        payload.pool_id,
        payload.draft_session_id,
        **context_options,
    )
    return AnalysisResult(
        run_id=run.id,
        provider=provider.name,
        model=run.model,
        status=run.status,
        output=json.loads(run.output_json) if run.output_json else None,
        error=run.error,
        parent_run_id=getattr(run, "parent_run_id", None),
        league_report_id=getattr(run, "league_report_id", None),
        context=analysis_context(run),
    )


@router.get("/analysis/runs", response_model=list[AnalysisRunOut])
def list_analysis_runs(db: Db) -> list[AnalysisRunOut]:
    return [
        AnalysisRunOut(
            id=run.id,
            task=run.task,
            question=run.question,
            provider=run.provider.name if run.provider else None,
            model=run.model,
            status=run.status,
            output=json.loads(run.output_json) if run.output_json else None,
            error=run.error,
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            created_at=run.created_at,
            completed_at=run.completed_at,
            parent_run_id=run.parent_run_id,
            league_report_id=run.league_report_id,
            context=analysis_context(run),
        )
        for run in db.query(AnalysisRun)
        .options(joinedload(AnalysisRun.provider))
        .order_by(AnalysisRun.created_at.desc())
        .limit(100)
    ]


@router.post("/notifications/subscribe", status_code=201)
def subscribe_push(payload: PushSubscriptionCreate, db: Db) -> dict[str, str]:
    endpoint_hash = hashlib.sha256(payload.endpoint.encode()).hexdigest()
    subscription = {"endpoint": payload.endpoint, "keys": payload.keys}
    row = (
        db.query(PushSubscription)
        .filter(PushSubscription.endpoint_hash == endpoint_hash)
        .one_or_none()
    )
    if row:
        row.subscription_json = json.dumps(subscription)
    else:
        db.add(
            PushSubscription(
                endpoint_hash=endpoint_hash, subscription_json=json.dumps(subscription)
            )
        )
    db.commit()
    return {"status": "subscribed"}


@router.get("/notifications/config")
def notification_config() -> dict[str, Any]:
    return {"enabled": push_enabled(), "vapid_public_key": settings.vapid_public_key}


@router.post("/notifications/test")
def test_notification(db: Db) -> dict[str, int]:
    return send_notification(
        db,
        "Open Gridiron is ready",
        "Browser notifications are working for this device.",
        "/news",
    )


@router.post("/backups")
def backup_now() -> dict[str, str]:
    try:
        path = create_backup()
    except Exception as exc:
        raise HTTPException(500, f"Backup failed: {exc}") from exc
    return {"filename": path.name}


@router.get("/backups")
def backups() -> list[dict[str, Any]]:
    return [
        {"filename": path.name, "size": path.stat().st_size, "created_at": path.stat().st_mtime}
        for path in list_backups()
    ]


@router.get("/backups/{filename}")
def download_backup(filename: str) -> FileResponse:
    if Path(filename).name != filename:
        raise HTTPException(400, "Invalid backup filename")
    path = settings.data_dir / "backups" / filename
    if not path.exists() or path not in list_backups():
        raise HTTPException(404, "Backup not found")
    return FileResponse(path, filename=filename, media_type="application/vnd.sqlite3")


@router.post("/backups/{filename}/restore")
def restore_managed_backup(filename: str) -> dict[str, str]:
    if Path(filename).name != filename:
        raise HTTPException(400, "Invalid backup filename")
    path = settings.data_dir / "backups" / filename
    if not path.exists() or path not in list_backups():
        raise HTTPException(404, "Backup not found")
    try:
        safety_copy = restore_backup(path)
    except Exception as exc:
        raise HTTPException(400, f"Restore failed: {exc}") from exc
    return {"status": "restored", "pre_restore_backup": safety_copy.name}


@router.get("/exports/user-data")
def export_user_data(db: Db) -> dict[str, Any]:
    return {
        "exported_at": datetime.now(UTC),
        "credentials_included": False,
        "leagues": [
            {
                **_league_out(db, league).model_dump(),
                "players": [_player_out(player).model_dump() for player in league.players],
                "draft_picks": [
                    {
                        "overall": pick.overall,
                        "round": pick.round,
                        "team_name": pick.team_name,
                        "player_id": pick.player_id,
                    }
                    for pick in league.draft_picks
                ],
            }
            for league in db.query(League).order_by(League.name).all()
        ],
        "pools": [
            {
                **_pool_out(pool).model_dump(),
                "entries": [
                    {
                        "name": entry.name,
                        "active": entry.active,
                        "picks": [
                            {
                                "week": pick.week,
                                "slot": pick.slot,
                                "team": pick.team,
                                "confidence": pick.confidence,
                                "result": pick.result,
                            }
                            for pick in entry.picks
                        ],
                    }
                    for entry in pool.entries
                ],
            }
            for pool in db.query(Pool).order_by(Pool.name).all()
        ],
        "games": [GameOut.model_validate(game).model_dump() for game in db.query(Game).all()],
    }


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    async def stream():
        counter = 0
        while not await request.is_disconnected():
            payload = {"counter": counter, "time": datetime.now(UTC).isoformat()}
            yield f"event: heartbeat\ndata: {json.dumps(payload)}\n\n"
            counter += 1
            await asyncio.sleep(15)

    return StreamingResponse(stream(), media_type="text/event-stream")
