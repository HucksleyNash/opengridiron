from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..dependencies import current_owner
from ..models import DraftPick, League, Player
from ..services.nflverse_draft import MODEL_VERSION, sync_projection_ranges
from ..services.yahoo import sync_leagues
from ..services.yahoo_scraper import (
    YahooScraperRateLimited,
    sync_scraped_draft_results,
    sync_scraped_league,
    sync_scraped_leagues,
)
from .diagnostics import diagnostics_payload
from .errors import DraftDomainError
from .events import session_mutation
from .exposure import exposure_payload
from .imports import ensure_legacy_projection_snapshot
from .inputs import MAX_INPUT_BYTES, commit_input, preview_input
from .mock import advance_opponent_pick
from .models import (
    DraftEvent,
    DraftRankingSnapshot,
    DraftRecommendationSnapshot,
    DraftSession,
    ProjectionSnapshotRow,
)
from .ranking_sources import (
    RANKING_CACHE_TTL,
    bind_ranking_to_session,
    create_ranking_from_scrape,
    latest_reusable_ranking,
    latest_scrape_snapshot,
)
from .reconciliation import (
    change_source_mode,
    conflicts_payload,
    reconcile_observations,
    resolve_conflict,
)
from .reducer import snake_team_slot
from .schemas import (
    DraftActionRequest,
    DraftArchiveRequest,
    DraftConflictResolution,
    DraftEventRequest,
    DraftInputBinding,
    DraftInputCommit,
    DraftOpponentPickRequest,
    DraftPreferenceUpdate,
    DraftRankingSyncRequest,
    DraftSessionCreate,
    DraftSetupUpdate,
    DraftSimulationRequest,
    DraftSourceModeUpdate,
    DraftYahooObservation,
    DraftYahooSyncRequest,
)
from .session import (
    append_pick_event,
    bind_input_snapshots,
    board_payload,
    create_session,
    event_payload,
    get_session,
    latest_recommendation_payload,
    preferences_payload,
    replace_preferences,
    session_payload,
    set_session_archived,
    transition_session,
    update_setup,
)
from .simulation import enqueue_simulation, read_computation

Db = Annotated[Session, Depends(get_db)]
router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(current_owner)],
    tags=["draft-suite"],
)
_sync_locks: dict[int, asyncio.Lock] = {}
_ranking_sync_locks: dict[int, asyncio.Lock] = {}


async def _sync_live_rankings(
    db: Session, session: DraftSession, payload: DraftRankingSyncRequest
) -> dict[str, object]:
    # Live timing guidance needs rankings, but must retain the session's frozen
    # projections and cannot use the mock setup binder (which resets status).
    league = db.get(League, session.league_id)
    ranking = (
        None
        if payload.refresh
        else db.scalar(
            select(DraftRankingSnapshot)
            .where(
                DraftRankingSnapshot.league_id == session.league_id,
                DraftRankingSnapshot.status == "ready",
                DraftRankingSnapshot.canonical_coverage >= 0.9,
                DraftRankingSnapshot.retrieved_at >= datetime.now(UTC) - RANKING_CACHE_TTL,
            )
            .order_by(DraftRankingSnapshot.retrieved_at.desc(), DraftRankingSnapshot.id.desc())
            .limit(1)
        )
    )
    if ranking is None:
        source = latest_scrape_snapshot(db, league)
        source_time = source.retrieved_at if source else None
        if source_time is not None and source_time.tzinfo is None:
            source_time = source_time.replace(tzinfo=UTC)
        if (
            payload.refresh
            or source_time is None
            or datetime.now(UTC) - source_time > RANKING_CACHE_TTL
        ):
            try:
                await sync_scraped_league(db, session.league_id)
            except YahooScraperRateLimited as exc:
                raise DraftDomainError(
                    429,
                    "yahoo_rate_limited",
                    str(exc),
                    {"retry_after_seconds": exc.retry_after_seconds},
                ) from exc
            except Exception as exc:
                raise DraftDomainError(422, "ranking_sync_failed", str(exc)[:300]) from exc
            source = latest_scrape_snapshot(db, league)
        if source is None:
            raise DraftDomainError(
                422,
                "ranking_sync_failed",
                "No Yahoo player rankings are available for this league.",
            )
        ranking = create_ranking_from_scrape(db, session.league_id, source)
        retrieved = ranking.retrieved_at
        if retrieved.tzinfo is None:
            retrieved = retrieved.replace(tzinfo=UTC)
        if (
            ranking.status != "ready"
            or ranking.canonical_coverage < 0.9
            or datetime.now(UTC) - retrieved > RANKING_CACHE_TTL
        ):
            raise DraftDomainError(
                422, "ranking_incomplete", "Yahoo rankings are incomplete or stale. Retry the sync."
            )
        # Commit the immutable input before the coordinator releases the read
        # transaction and rechecks the draft sequence under its write lock.
        db.commit()
    ranking_id = ranking.id
    session_id = session.id
    with session_mutation(db, session_id):
        session = get_session(db, session_id)
        if session.current_sequence != payload.expected_sequence:
            raise DraftDomainError(
                409,
                "draft_conflict",
                "The draft changed while rankings were being synchronized.",
                {"current_sequence": session.current_sequence},
            )
        if session.status not in {"LIVE", "PAUSED"}:
            raise DraftDomainError(409, "session_state", "This draft is no longer active.")
        if session.ranking_snapshot_id != ranking_id:
            session = bind_input_snapshots(
                db,
                session_id,
                DraftInputBinding(
                    expected_sequence=payload.expected_sequence, ranking_snapshot_id=ranking_id
                ),
            )
        ranking = db.get(DraftRankingSnapshot, ranking_id)
        return {
            "status": "ready",
            "ranking_snapshot_id": ranking.id,
            "row_count": ranking.row_count,
            "coverage": ranking.canonical_coverage,
            "source": ranking.source,
            "retrieved_at": ranking.retrieved_at,
            "session": session_payload(db, session),
        }


@router.get("/leagues/{league_id}/draft-sessions")
def list_draft_sessions(league_id: int, db: Db) -> list[dict[str, object]]:
    sessions = list(
        db.scalars(
            select(DraftSession)
            .where(DraftSession.league_id == league_id)
            .order_by(DraftSession.created_at.desc(), DraftSession.id.desc())
        )
    )
    return [session_payload(db, session) for session in sessions]


@router.post("/leagues/{league_id}/draft-sessions", status_code=201)
def new_draft_session(league_id: int, payload: DraftSessionCreate, db: Db) -> dict[str, object]:
    return session_payload(db, create_session(db, league_id, payload))


@router.post("/leagues/{league_id}/draft-rankings/sync")
async def sync_draft_rankings(
    league_id: int, payload: DraftRankingSyncRequest, db: Db
) -> dict[str, object]:
    session = get_session(db, payload.session_id)
    if session.league_id != league_id:
        raise DraftDomainError(
            404, "draft_session_not_found", "That draft session does not belong to this league."
        )
    if session.current_sequence != payload.expected_sequence:
        raise DraftDomainError(
            409,
            "draft_conflict",
            "The draft changed while rankings were being synchronized.",
            {"current_sequence": session.current_sequence},
        )
    live_sync = session.kind == "live" and session.status in {"LIVE", "PAUSED"}
    if session.status not in {"SETUP", "READY"} and not live_sync:
        raise DraftDomainError(
            409,
            "session_state",
            "Rankings can be synchronized only before the draft starts.",
            {"status": session.status},
        )
    lock = _ranking_sync_locks.setdefault(league_id, asyncio.Lock())
    if lock.locked():
        return {"status": "already_running", "session": session_payload(db, session)}
    async with lock:
        if live_sync:
            return await _sync_live_rankings(db, session, payload)
        league = db.get(League, league_id)
        if league is None:
            raise DraftDomainError(404, "league_not_found", "The league does not exist.")
        has_ranges = bool(
            db.query(Player.id)
            .filter(
                Player.league_id == league.id,
                Player.evidence_json.like(f'%"model_version": "{MODEL_VERSION}"%'),
            )
            .first()
        )
        if not has_ranges:
            await sync_projection_ranges(db, league)
        projection = ensure_legacy_projection_snapshot(db, league)
        db.flush()
        session.projection_snapshot_id = projection.id if projection else None
        has_projection_signal = bool(
            projection
            and db.scalar(
                select(ProjectionSnapshotRow.id)
                .where(
                    ProjectionSnapshotRow.snapshot_id == projection.id,
                    ProjectionSnapshotRow.projected_points > 0,
                )
                .limit(1)
            )
        )
        reusable = (
            latest_reusable_ranking(db, league_id, session)
            if not payload.refresh and has_projection_signal
            else None
        )
        if reusable is None:
            source = latest_scrape_snapshot(db, league)
            source_is_fresh = (
                source is not None
                and (
                    datetime.now(UTC)
                    - (
                        source.retrieved_at.replace(tzinfo=UTC)
                        if source.retrieved_at.tzinfo is None
                        else source.retrieved_at.astimezone(UTC)
                    )
                )
                <= RANKING_CACHE_TTL
            )
            if payload.refresh or not has_projection_signal or not source_is_fresh:
                try:
                    await sync_scraped_leagues(db)
                except Exception as exc:
                    raise DraftDomainError(422, "ranking_sync_failed", str(exc)[:300]) from exc
                source = latest_scrape_snapshot(db, league)
                projection = ensure_legacy_projection_snapshot(db, league)
                db.flush()
                session.projection_snapshot_id = projection.id if projection else None
            if source is None:
                raise DraftDomainError(
                    422,
                    "ranking_sync_failed",
                    "No complete Yahoo player snapshot is available for this league.",
                )
            reusable = create_ranking_from_scrape(db, league_id, source)
        projection_has_signal = bool(
            projection
            and db.scalar(
                select(ProjectionSnapshotRow.id)
                .where(
                    ProjectionSnapshotRow.snapshot_id == projection.id,
                    ProjectionSnapshotRow.projected_points > 0,
                )
                .limit(1)
            )
        )
        if not projection_has_signal:
            raise DraftDomainError(
                422,
                "projection_sync_failed",
                "Yahoo rankings loaded, but Yahoo season projections were empty.",
            )
        bind_ranking_to_session(db, session, reusable)
        return {
            "status": "ready",
            "ranking_snapshot_id": reusable.id,
            "row_count": reusable.row_count,
            "coverage": reusable.canonical_coverage,
            "source": reusable.source,
            "retrieved_at": reusable.retrieved_at,
            "session": session_payload(db, session),
        }


@router.get("/draft-sessions/{session_id}")
def read_draft_session(session_id: int, db: Db) -> dict[str, object]:
    return session_payload(db, get_session(db, session_id))


@router.put("/draft-sessions/{session_id}/archive")
def archive_draft_session(
    session_id: int, payload: DraftArchiveRequest, db: Db
) -> dict[str, object]:
    return session_payload(db, set_session_archived(db, session_id, payload))


@router.put("/draft-sessions/{session_id}/setup")
def replace_draft_setup(session_id: int, payload: DraftSetupUpdate, db: Db) -> dict[str, object]:
    return session_payload(db, update_setup(db, session_id, payload))


@router.post("/leagues/{league_id}/draft-inputs/preview")
async def preview_draft_input(
    league_id: int,
    db: Db,
    file: UploadFile = File(),
    input_type: Literal["projection", "ranking", "combined"] = Query(default="combined"),
) -> dict[str, object]:
    content = await file.read(MAX_INPUT_BYTES + 1)
    return preview_input(
        db,
        league_id,
        input_type=input_type,
        filename=file.filename or "draft-input.csv",
        content=content,
    )


@router.post("/leagues/{league_id}/draft-inputs/previews/{preview_id}/commit")
def commit_draft_input(
    league_id: int,
    preview_id: str,
    payload: DraftInputCommit,
    db: Db,
) -> dict[str, object]:
    return commit_input(db, league_id, preview_id, payload)


@router.post("/draft-sessions/{session_id}/input-snapshots")
def update_draft_inputs(session_id: int, payload: DraftInputBinding, db: Db) -> dict[str, object]:
    return session_payload(db, bind_input_snapshots(db, session_id, payload))


@router.post("/draft-sessions/{session_id}/actions/{action}")
def draft_session_action(
    session_id: int,
    action: Literal["start", "pause", "resume", "complete", "abandon", "reopen"],
    payload: DraftActionRequest,
    db: Db,
) -> dict[str, object]:
    session, event = transition_session(db, session_id, action, payload)
    return {"session": session_payload(db, session), "event": event_payload(event)}


@router.get("/draft-sessions/{session_id}/events")
def list_draft_events(session_id: int, db: Db) -> list[dict[str, object]]:
    get_session(db, session_id)
    events = list(
        db.scalars(
            select(DraftEvent)
            .where(DraftEvent.session_id == session_id)
            .order_by(DraftEvent.sequence)
        )
    )
    return [event_payload(event) for event in events]


@router.post("/draft-sessions/{session_id}/events", status_code=201)
def record_draft_event(session_id: int, payload: DraftEventRequest, db: Db) -> dict[str, object]:
    session, event = append_pick_event(db, session_id, payload)
    return {"session": session_payload(db, session), "event": event_payload(event)}


@router.post("/draft-sessions/{session_id}/opponent-picks/next", status_code=201)
def record_automatic_opponent_pick(
    session_id: int, payload: DraftOpponentPickRequest, db: Db
) -> dict[str, object]:
    session, event = advance_opponent_pick(db, session_id, payload)
    return {
        "session": session_payload(db, session),
        "event": event_payload(event),
        "board": board_payload(db, session),
    }


@router.get("/draft-sessions/{session_id}/board")
def read_draft_board(session_id: int, db: Db) -> dict[str, object]:
    return board_payload(db, get_session(db, session_id))


@router.get("/draft-sessions/{session_id}/recommendations")
def read_draft_recommendations(session_id: int, db: Db) -> dict[str, object]:
    return latest_recommendation_payload(db, get_session(db, session_id))


@router.post("/draft-sessions/{session_id}/simulations")
def simulate_draft_choices(
    session_id: int, payload: DraftSimulationRequest, db: Db
) -> dict[str, object]:
    if not settings.draft_simulation_enabled:
        return {
            "status": "unavailable",
            "reason": "simulation_disabled",
            "results": [],
        }
    return enqueue_simulation(db, get_session(db, session_id), payload)


@router.get("/draft-sessions/{session_id}/computations/{run_id}")
def read_draft_computation(session_id: int, run_id: int, db: Db) -> dict[str, object]:
    get_session(db, session_id)
    return read_computation(db, session_id, run_id)


@router.get("/draft-sessions/{session_id}/exposure")
def read_draft_exposure(session_id: int, db: Db) -> dict[str, object]:
    return exposure_payload(db, get_session(db, session_id))


@router.get("/draft-sessions/{session_id}/diagnostics")
def read_draft_diagnostics(session_id: int, db: Db) -> dict[str, object]:
    return diagnostics_payload(db, get_session(db, session_id))


@router.get("/draft-sessions/{session_id}/preferences")
def read_draft_preferences(session_id: int, db: Db) -> dict[str, object]:
    return preferences_payload(db, get_session(db, session_id))


@router.put("/draft-sessions/{session_id}/preferences")
def update_draft_preferences(
    session_id: int, payload: DraftPreferenceUpdate, db: Db
) -> dict[str, object]:
    return replace_preferences(db, session_id, payload)


@router.get("/draft-sessions/{session_id}/replay")
def draft_replay(session_id: int, db: Db) -> dict[str, object]:
    session = get_session(db, session_id)
    events = list(
        db.scalars(
            select(DraftEvent)
            .where(DraftEvent.session_id == session.id)
            .order_by(DraftEvent.sequence)
        )
    )
    snapshot_ids = {
        event.recommendation_snapshot_id
        for event in events
        if event.recommendation_snapshot_id is not None
    }
    snapshots = (
        {
            snapshot.id: snapshot
            for snapshot in db.scalars(
                select(DraftRecommendationSnapshot).where(
                    DraftRecommendationSnapshot.id.in_(snapshot_ids)
                )
            )
        }
        if snapshot_ids
        else {}
    )
    decisions = []
    for event in events:
        if (
            event.type not in {"pick_recorded", "pick_replaced"}
            or not event.recommendation_snapshot_id
        ):
            continue
        snapshot = snapshots.get(event.recommendation_snapshot_id)
        candidates = json.loads(snapshot.candidates_json) if snapshot else []
        alternatives = json.loads(snapshot.alternatives_json) if snapshot else []
        decision_surface = [*candidates, *alternatives]
        chosen = next(
            (
                candidate
                for candidate in decision_surface
                if candidate["player_id"] == event.player_id
            ),
            None,
        )
        best_score = float(candidates[0]["score"]) if candidates else 0.0
        chosen_score = float(chosen["score"]) if chosen else 0.0
        quality = chosen_score / best_score if best_score > 0 else 1.0
        decisions.append(
            {
                "event": event_payload(event),
                "snapshot_id": snapshot.id if snapshot else None,
                "candidates": candidates,
                "chosen_candidate": chosen,
                "rank_at_time": (
                    next(
                        (
                            index
                            for index, candidate in enumerate(decision_surface, start=1)
                            if candidate["player_id"] == event.player_id
                        ),
                        None,
                    )
                ),
                "eligible_count": len(decision_surface),
                "decision_quality": round(quality, 4),
                "at_time": True,
            }
        )
    coaching: list[dict[str, object]] = []
    if len(decisions) >= 3:
        average_quality = sum(float(decision["decision_quality"]) for decision in decisions) / len(
            decisions
        )
        non_top = [
            decision
            for decision in decisions
            if decision["candidates"]
            and decision["candidates"][0]["name"] != decision["event"]["player_name"]
        ]
        if average_quality < 0.85 and non_top:
            coaching.append(
                {
                    "code": "compare_tradeoffs_before_confirming",
                    "observation": (
                        f"{len(non_top)} of {len(decisions)} linked owner picks differed "
                        "from the top deterministic option."
                    ),
                    "exercise": (
                        "Before the next mock, state the tradeoff for choices one and two "
                        "out loud before recording the pick."
                    ),
                    "evidence_event_ids": [decision["event"]["id"] for decision in non_top],
                    "version": "draft-coaching-v1",
                }
            )
    board = board_payload(db, session)
    owner_team = next((team for team in board["teams"] if team["is_owner"]), None)
    owner_roster = owner_team["roster"] if owner_team else []
    flex_positions = {"RB", "WR", "TE"}
    waiver_moves = []
    for addition in board["available_players"]:
        for drop in owner_roster:
            same_position = addition["position"] == drop.get("position")
            flex_compatible = (
                addition["position"] in flex_positions and drop.get("position") in flex_positions
            )
            if not same_position and not flex_compatible:
                continue
            improvement = float(addition["projected_points"]) - float(
                drop.get("projected_points") or 0
            )
            if improvement <= 0:
                continue
            waiver_moves.append(
                {
                    "add": addition,
                    "drop": drop,
                    "projected_point_gain": round(improvement, 2),
                    "reason": (
                        f"Adds {improvement:.1f} projected points at a compatible "
                        f"{addition['position']} roster spot."
                    ),
                }
            )
    waiver_moves.sort(
        key=lambda move: (
            -float(move["projected_point_gain"]),
            int(move["add"]["id"]),
            int(move["drop"]["player_id"]),
        )
    )
    return {
        "session_id": session.id,
        "status": session.status,
        "replay_generation": session.replay_generation,
        "decisions": decisions,
        "coaching": {
            "status": "ready" if coaching else "insufficient_history",
            "lane": session.kind,
            "findings": coaching,
            "minimum_linked_decisions": 3,
        },
        "waiver_priorities": board["available_players"][:5],
        "waiver_moves": waiver_moves[:5],
        "message": ("Replay uses only the recommendation snapshot stored with each owner pick."),
    }


@router.get("/draft-sessions/{session_id}/conflicts")
def list_draft_conflicts(session_id: int, db: Db) -> list[dict[str, object]]:
    get_session(db, session_id)
    return conflicts_payload(db, session_id)


@router.post("/draft-sessions/{session_id}/conflicts/{conflict_id}/resolve")
def resolve_draft_conflict(
    session_id: int,
    conflict_id: int,
    payload: DraftConflictResolution,
    db: Db,
) -> dict[str, object]:
    return resolve_conflict(db, get_session(db, session_id), conflict_id, payload)


@router.post("/draft-sessions/{session_id}/source-mode")
def update_draft_source_mode(
    session_id: int, payload: DraftSourceModeUpdate, db: Db
) -> dict[str, object]:
    return session_payload(db, change_source_mode(db, get_session(db, session_id), payload))


def _legacy_yahoo_observations(db: Session, session: DraftSession) -> list[DraftYahooObservation]:
    picks = list(
        db.scalars(
            select(DraftPick)
            .where(DraftPick.league_id == session.league_id)
            .order_by(DraftPick.overall)
        )
    )
    return [
        DraftYahooObservation(
            provider_key=f"draft-pick:{pick.id}:{pick.overall}",
            provider_revision=f"{pick.round}:{pick.team_name}:{pick.player_id}",
            overall_pick=pick.overall,
            team_slot=snake_team_slot(pick.overall, session.team_count),
            player_id=pick.player_id,
            player_name=pick.player_name,
        )
        for pick in picks
    ]


@router.post("/draft-sessions/{session_id}/sync/yahoo")
async def sync_draft_yahoo(
    session_id: int, payload: DraftYahooSyncRequest, db: Db
) -> dict[str, object]:
    session = get_session(db, session_id)
    if not settings.draft_yahoo_enabled:
        return {
            "session_id": session_id,
            "status": "disabled",
            "current_sequence": session.current_sequence,
        }
    lock = _sync_locks.setdefault(session_id, asyncio.Lock())
    if lock.locked():
        return {
            "session_id": session_id,
            "status": "already_running",
            "current_sequence": session.current_sequence,
        }
    async with lock:
        if payload.fixture_observations is not None:
            if settings.app_env != "test":
                raise ValueError("Fixture observations are accepted only in test mode")
            observations = payload.fixture_observations
        else:
            if "scrape" in session.source_mode:
                try:
                    await sync_scraped_draft_results(db, session.league_id)
                except YahooScraperRateLimited as exc:
                    raise DraftDomainError(
                        503,
                        "yahoo_rate_limited",
                        str(exc),
                        {"retry_after_seconds": exc.retry_after_seconds},
                    ) from exc
            elif "oauth" in session.source_mode:
                await sync_leagues(db)
            else:
                raise ValueError("Choose a Yahoo shadow source before synchronizing")
            db.refresh(session)
            observations = _legacy_yahoo_observations(db, session)
        return reconcile_observations(
            db,
            session,
            observations,
            expected_sequence=payload.expected_sequence,
        )
