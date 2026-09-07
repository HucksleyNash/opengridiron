from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..models import DataSnapshot, League, Player
from .bye_weeks import player_bye_week, team_bye_weeks
from .errors import DraftDomainError, not_found
from .events import coordinated_mutation
from .imports import ensure_legacy_projection_snapshot
from .models import (
    DraftBoardPreference,
    DraftEvent,
    DraftRankingSnapshot,
    DraftRecommendationSnapshot,
    DraftSession,
    DraftTeam,
    ProjectionSnapshot,
    ProjectionSnapshotRow,
)
from .recommendations import (
    ALGORITHM_VERSION,
    publish_recommendation_snapshot,
    recommendation_payload,
)
from .reducer import ReducedPick, next_owner_pick, reduce_picks, snake_round, snake_team_slot
from .schemas import (
    DraftActionRequest,
    DraftArchiveRequest,
    DraftEventRequest,
    DraftInputBinding,
    DraftPreferenceUpdate,
    DraftSessionCreate,
    DraftSetupUpdate,
)

SessionAction = Literal["start", "pause", "resume", "complete", "abandon", "reopen"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def opponent_mode(session: DraftSession) -> str:
    try:
        config = json.loads(session.format_config_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return "manual"
    mode = config.get("opponent_mode") if isinstance(config, dict) else None
    return "automatic" if mode == "automatic" else "manual"


def get_session(db: Session, session_id: int) -> DraftSession:
    session = db.get(DraftSession, session_id)
    if session is None:
        raise not_found("draft_session", session_id)
    return session


def _events(db: Session, session_id: int) -> list[DraftEvent]:
    return list(
        db.scalars(
            select(DraftEvent)
            .options(joinedload(DraftEvent.player))
            .where(DraftEvent.session_id == session_id)
            .order_by(DraftEvent.sequence)
        ).unique()
    )


def readiness_findings(db: Session, session: DraftSession) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if session.team_count < 8 or session.team_count > 16:
        findings.append({"code": "invalid_team_count", "message": "Use 8–16 teams."})
    if session.round_count < 1 or session.round_count > 30:
        findings.append({"code": "invalid_round_count", "message": "Use 1–30 rounds."})
    if session.owner_team_slot < 1 or session.owner_team_slot > session.team_count:
        findings.append({"code": "invalid_owner_slot", "message": "Choose the owner's slot."})
    teams = list(
        db.scalars(
            select(DraftTeam).where(DraftTeam.session_id == session.id).order_by(DraftTeam.slot)
        )
    )
    if len(teams) != session.team_count or len({team.slot for team in teams}) != session.team_count:
        findings.append(
            {"code": "incomplete_team_order", "message": "Name every team in draft order."}
        )
    if session.projection_snapshot_id is None:
        findings.append(
            {"code": "projection_required", "message": "Add at least one projected player."}
        )
    else:
        row_count = db.scalar(
            select(ProjectionSnapshotRow.id)
            .where(ProjectionSnapshotRow.snapshot_id == session.projection_snapshot_id)
            .limit(1)
        )
        if row_count is None:
            findings.append(
                {"code": "projection_empty", "message": "The bound projection has no players."}
            )
    if session.kind == "mock" and opponent_mode(session) == "automatic":
        from .ranking_sources import ranking_readiness_findings

        findings.extend(ranking_readiness_findings(db, session))
    return findings


def _team_payload(team: DraftTeam) -> dict[str, object]:
    return {
        "id": team.id,
        "slot": team.slot,
        "name": team.name,
        "is_owner": team.is_owner,
        "provider_team_key": team.provider_team_key,
    }


def session_payload(db: Session, session: DraftSession) -> dict[str, object]:
    teams = list(
        db.scalars(
            select(DraftTeam).where(DraftTeam.session_id == session.id).order_by(DraftTeam.slot)
        )
    )
    findings = readiness_findings(db, session)
    league = db.get(League, session.league_id)
    return {
        "id": session.id,
        "league_id": session.league_id,
        "league_name": league.name if league else "Unknown league",
        "kind": session.kind,
        "format": session.format,
        "status": session.status,
        "strategy_mode": session.strategy_mode,
        "team_count": session.team_count,
        "round_count": session.round_count,
        "owner_team_slot": session.owner_team_slot,
        "projection_snapshot_id": session.projection_snapshot_id,
        "ranking_snapshot_id": session.ranking_snapshot_id,
        "source_mode": session.source_mode,
        "opponent_mode": opponent_mode(session),
        "current_sequence": session.current_sequence,
        "preference_revision": session.preference_revision,
        "replay_generation": session.replay_generation,
        "teams": [_team_payload(team) for team in teams],
        "readiness": {"ready": not findings, "findings": findings},
        "created_at": session.created_at,
        "started_at": session.started_at,
        "completed_at": session.completed_at,
        "archived_at": session.archived_at,
    }


def project_live_roster(db: Session, session: DraftSession) -> dict[str, object]:
    """Project one completed live event stream into the mutable league roster view."""
    if session.kind != "live" or session.status != "COMPLETE":
        raise DraftDomainError(
            409,
            "live_draft_incomplete",
            "Only a completed live draft can update the league roster.",
            {"kind": session.kind, "status": session.status},
        )

    picks = reduce_picks(_events(db, session.id))
    teams = {
        team.slot: team
        for team in db.scalars(select(DraftTeam).where(DraftTeam.session_id == session.id))
    }
    players = {
        player.id: player
        for player in db.scalars(select(Player).where(Player.league_id == session.league_id))
    }
    total_picks = session.team_count * session.round_count
    full_board = len(picks) == total_picks

    # A full standard draft is authoritative for the initial roster state. Early
    # completion remains additive so an intentionally partial draft cannot erase
    # roster data that may already have come from Yahoo.
    if full_board:
        for player in players.values():
            player.ownership = "FA"
            player.rostered_by = None
            player.current_slot = None

    assigned = 0
    for pick in picks:
        player = players.get(pick.player_id)
        team = teams.get(pick.team_slot)
        if player is None or team is None:
            continue
        player.ownership = "TEAM"
        player.rostered_by = team.name
        player.current_slot = None
        assigned += 1

    owner_team = teams.get(session.owner_team_slot)
    result: dict[str, object] = {
        "status": "fresh",
        "source": "draft_session",
        "session_id": session.id,
        "players": assigned,
        "teams": len({pick.team_slot for pick in picks}),
        "full_board": full_board,
        "owner_team_name": owner_team.name if owner_team else None,
    }
    db.add(
        DataSnapshot(
            source="draft_session",
            source_id=f"league:{session.league_id}:draft-session:{session.id}",
            status="fresh" if full_board else "partial",
            payload_json=json.dumps(result, sort_keys=True),
        )
    )
    return result


def create_session(db: Session, league_id: int, payload: DraftSessionCreate) -> DraftSession:
    league = db.get(League, league_id)
    if league is None:
        raise not_found("league", league_id)
    snapshot = ensure_legacy_projection_snapshot(db, league)
    session = DraftSession(
        league_id=league.id,
        kind=payload.kind,
        format="snake",
        status="SETUP",
        strategy_mode=payload.strategy_mode,
        team_count=payload.team_count,
        round_count=payload.round_count,
        owner_team_slot=payload.owner_team_slot,
        projection_snapshot_id=snapshot.id if snapshot else None,
        source_mode=payload.source_mode,
        format_config_json=json.dumps({"opponent_mode": payload.opponent_mode}),
        scoring_snapshot_json=league.scoring_json,
        roster_slots_snapshot_json=league.roster_slots_json,
        provider_league_key=league.yahoo_key,
    )
    db.add(session)
    db.flush()
    names = payload.team_names or [f"Team {slot}" for slot in range(1, payload.team_count + 1)]
    names[payload.owner_team_slot - 1] = payload.owner_team_name
    for slot, name in enumerate(names, start=1):
        db.add(
            DraftTeam(
                session_id=session.id,
                slot=slot,
                name=" ".join(name.split()) or f"Team {slot}",
                is_owner=slot == payload.owner_team_slot,
            )
        )
    db.flush()
    if not readiness_findings(db, session):
        session.status = "READY"
    db.commit()
    db.refresh(session)
    return session


@coordinated_mutation
def update_setup(db: Session, session_id: int, payload: DraftSetupUpdate) -> DraftSession:
    session = get_session(db, session_id)
    if session.status not in {"SETUP", "READY"}:
        raise DraftDomainError(
            409,
            "session_state",
            "Draft setup is locked after the session starts.",
            {"status": session.status},
        )
    _check_sequence(session, payload.expected_sequence)
    session.team_count = payload.team_count
    session.round_count = payload.round_count
    session.owner_team_slot = payload.owner_team_slot
    db.execute(delete(DraftTeam).where(DraftTeam.session_id == session.id))
    for slot, name in enumerate(payload.team_names, start=1):
        db.add(
            DraftTeam(
                session_id=session.id,
                slot=slot,
                name=" ".join(name.split()) or f"Team {slot}",
                is_owner=slot == payload.owner_team_slot,
            )
        )
    db.flush()
    session.status = "READY" if not readiness_findings(db, session) else "SETUP"
    db.commit()
    db.refresh(session)
    return session


@coordinated_mutation
def bind_input_snapshots(db: Session, session_id: int, payload: DraftInputBinding) -> DraftSession:
    session = get_session(db, session_id)
    _check_sequence(session, payload.expected_sequence)
    if session.status in {"COMPLETE", "ABANDONED"}:
        raise DraftDomainError(
            409,
            "session_state",
            "Completed or abandoned drafts cannot change their input snapshots.",
            {"status": session.status},
        )
    if payload.projection_snapshot_id is not None:
        projection = db.get(ProjectionSnapshot, payload.projection_snapshot_id)
        if (
            projection is None
            or projection.league_id != session.league_id
            or projection.status != "ready"
        ):
            raise DraftDomainError(
                422, "projection_invalid", "Choose a ready projection from this league."
            )
        session.projection_snapshot_id = projection.id
    if payload.ranking_snapshot_id is not None:
        ranking = db.get(DraftRankingSnapshot, payload.ranking_snapshot_id)
        if ranking is None or ranking.league_id != session.league_id or ranking.status != "ready":
            raise DraftDomainError(
                422, "ranking_invalid", "Choose a ready ranking from this league."
            )
        session.ranking_snapshot_id = ranking.id
    if session.status in {"LIVE", "PAUSED"}:
        _add_event(
            db,
            session,
            event_type="input_snapshot_changed",
            idempotency_key=(
                f"input-snapshot:{session.current_sequence + 1}:"
                f"{session.projection_snapshot_id}:{session.ranking_snapshot_id}"
            ),
        )
    else:
        session.status = "READY" if not readiness_findings(db, session) else "SETUP"
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DraftDomainError(409, "draft_conflict", "The draft changed concurrently.") from exc
    db.refresh(session)
    if session.status in {"LIVE", "PAUSED"}:
        publish_recommendation_snapshot(db, session, reduce_picks(_events(db, session.id)))
    return session


def _check_sequence(session: DraftSession, expected_sequence: int) -> None:
    if session.current_sequence != expected_sequence:
        raise DraftDomainError(
            409,
            "draft_conflict",
            "The draft changed. Refresh and confirm the highlighted player again.",
            {
                "expected_sequence": expected_sequence,
                "current_sequence": session.current_sequence,
                "session_id": session.id,
            },
        )


@coordinated_mutation
def set_session_archived(
    db: Session, session_id: int, payload: DraftArchiveRequest
) -> DraftSession:
    session = get_session(db, session_id)
    _check_sequence(session, payload.expected_sequence)
    session.archived_at = _utcnow() if payload.archived else None
    db.commit()
    db.refresh(session)
    return session


def _existing_idempotent_event(
    db: Session, session_id: int, idempotency_key: str
) -> DraftEvent | None:
    return db.scalar(
        select(DraftEvent).where(
            DraftEvent.session_id == session_id,
            DraftEvent.idempotency_key == idempotency_key,
        )
    )


def _add_event(
    db: Session,
    session: DraftSession,
    *,
    event_type: str,
    idempotency_key: str,
    overall_pick: int | None = None,
    round_number: int | None = None,
    team_slot: int | None = None,
    player: Player | None = None,
    reason: str | None = None,
    supersedes_event_id: int | None = None,
    recommendation_snapshot_id: int | None = None,
    early: bool = False,
    source: str = "manual",
    metadata: dict[str, object] | None = None,
) -> DraftEvent:
    session.current_sequence += 1
    event = DraftEvent(
        session_id=session.id,
        sequence=session.current_sequence,
        type=event_type,
        overall_pick=overall_pick,
        round=round_number,
        team_slot=team_slot,
        player_id=player.id if player else None,
        athlete_id=player.athlete_id if player else None,
        source=source,
        idempotency_key=idempotency_key,
        reason=reason,
        supersedes_event_id=supersedes_event_id,
        recommendation_snapshot_id=recommendation_snapshot_id,
        early=early,
        metadata_json=json.dumps(metadata or {}, sort_keys=True),
    )
    db.add(event)
    return event


@coordinated_mutation
def transition_session(
    db: Session, session_id: int, action: SessionAction, payload: DraftActionRequest
) -> tuple[DraftSession, DraftEvent]:
    session = get_session(db, session_id)
    existing = _existing_idempotent_event(db, session.id, payload.idempotency_key)
    if existing is not None:
        return session, existing
    _check_sequence(session, payload.expected_sequence)
    transitions = {
        "start": ({"READY"}, "LIVE", "session_started"),
        "pause": ({"LIVE"}, "PAUSED", "session_paused"),
        "resume": ({"PAUSED"}, "LIVE", "session_resumed"),
        "complete": ({"LIVE", "PAUSED"}, "COMPLETE", "session_completed"),
        "abandon": ({"SETUP", "READY", "LIVE", "PAUSED"}, "ABANDONED", "session_abandoned"),
        "reopen": ({"COMPLETE"}, "LIVE", "session_reopened"),
    }
    allowed, new_status, event_type = transitions[action]
    if session.status not in allowed:
        raise DraftDomainError(
            409,
            "session_state",
            f"Cannot {action} a draft in {session.status} state.",
            {"status": session.status, "action": action},
        )
    if action == "start":
        findings = readiness_findings(db, session)
        if findings:
            raise DraftDomainError(
                422,
                "draft_not_ready",
                "Resolve setup findings before starting the draft.",
                {"findings": findings},
            )
    if action in {"reopen", "abandon"} and not payload.reason:
        raise DraftDomainError(
            422,
            "reason_required",
            f"A reason is required to {action} this draft.",
        )
    picks = reduce_picks(_events(db, session.id))
    total = session.team_count * session.round_count
    early = action == "complete" and len(picks) < total
    if early and not payload.reason:
        raise DraftDomainError(
            422,
            "reason_required",
            "A reason is required to complete a draft early.",
        )
    event = _add_event(
        db,
        session,
        event_type=event_type,
        idempotency_key=payload.idempotency_key,
        reason=payload.reason,
        early=early,
    )
    session.status = new_status
    if action == "start":
        session.started_at = _utcnow()
    if action == "complete":
        session.completed_at = _utcnow()
        if session.kind == "live":
            project_live_roster(db, session)
    if action == "reopen":
        session.completed_at = None
        session.replay_generation += 1
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DraftDomainError(409, "draft_conflict", "The draft changed concurrently.") from exc
    db.refresh(session)
    if session.status in {"LIVE", "PAUSED"}:
        publish_recommendation_snapshot(db, session, reduce_picks(_events(db, session.id)))
    return session, event


def _active_target(picks: list[ReducedPick], target_event_id: int) -> ReducedPick:
    target = next((pick for pick in picks if pick.event.id == target_event_id), None)
    if target is None:
        raise DraftDomainError(
            409,
            "pick_not_active",
            "That pick is no longer active. Refresh the board before correcting it.",
            {"target_event_id": target_event_id},
        )
    return target


def _validate_player(db: Session, session: DraftSession, player_id: int) -> Player:
    player = db.get(Player, player_id)
    if player is None or player.league_id != session.league_id:
        raise DraftDomainError(
            422,
            "player_not_in_league",
            "Choose a player from this draft's league player pool.",
            {"player_id": player_id},
        )
    if player.athlete_id is None:
        raise DraftDomainError(
            422,
            "player_identity_pending",
            "Resolve this player's canonical identity before drafting them.",
            {"player_id": player_id},
        )
    return player


def _validate_owner_snapshot(
    db: Session,
    session: DraftSession,
    recommendation_snapshot_id: int | None,
) -> None:
    if recommendation_snapshot_id is None:
        raise DraftDomainError(
            425,
            "advice_pending",
            "Fresh advice is required before recording the owner's pick.",
            {"retryable": True, "current_sequence": session.current_sequence},
        )
    snapshot = db.get(DraftRecommendationSnapshot, recommendation_snapshot_id)
    if (
        snapshot is None
        or snapshot.session_id != session.id
        or snapshot.session_sequence != session.current_sequence
        or snapshot.status != "ready"
    ):
        raise DraftDomainError(
            409,
            "stale_recommendation",
            "The recommendation changed. Refresh and confirm the same player again.",
            {
                "retryable": True,
                "recommendation_snapshot_id": recommendation_snapshot_id,
                "current_sequence": session.current_sequence,
            },
        )


@coordinated_mutation
def append_pick_event(
    db: Session,
    session_id: int,
    payload: DraftEventRequest,
    *,
    source: str = "manual",
    metadata: dict[str, object] | None = None,
) -> tuple[DraftSession, DraftEvent]:
    session = get_session(db, session_id)
    existing = _existing_idempotent_event(db, session.id, payload.idempotency_key)
    if existing is not None:
        return session, existing
    _check_sequence(session, payload.expected_sequence)
    events = _events(db, session.id)
    picks = reduce_picks(events)
    total = session.team_count * session.round_count

    if payload.type == "pick_recorded":
        if session.status != "LIVE":
            raise DraftDomainError(
                409,
                "session_state",
                "New picks can be recorded only while the draft is live.",
                {"status": session.status},
            )
        overall = len(picks) + 1
        if overall > total:
            raise DraftDomainError(409, "draft_complete", "Every configured pick is filled.")
        if payload.overall_pick is not None and payload.overall_pick != overall:
            raise DraftDomainError(
                409,
                "invalid_draft_order",
                "Record the next pick in order; gaps are not allowed.",
                {"expected_overall_pick": overall, "received_overall_pick": payload.overall_pick},
            )
        player = _validate_player(db, session, payload.player_id or 0)
        if player.id in {pick.player_id for pick in picks}:
            raise DraftDomainError(
                409,
                "duplicate_pick",
                "That player has already been drafted.",
                {"player_id": player.id},
            )
        team_slot = snake_team_slot(overall, session.team_count)
        if (
            source == "manual"
            and session.kind == "mock"
            and opponent_mode(session) == "automatic"
            and team_slot != session.owner_team_slot
        ):
            raise DraftDomainError(
                409,
                "automatic_opponent_turn",
                "This opponent turn is controlled by the automatic mock engine.",
            )
        if team_slot == session.owner_team_slot:
            _validate_owner_snapshot(db, session, payload.recommendation_snapshot_id)
        event = _add_event(
            db,
            session,
            event_type="pick_recorded",
            idempotency_key=payload.idempotency_key,
            overall_pick=overall,
            round_number=snake_round(overall, session.team_count),
            team_slot=team_slot,
            player=player,
            recommendation_snapshot_id=(
                payload.recommendation_snapshot_id if team_slot == session.owner_team_slot else None
            ),
            source=source,
            metadata=metadata,
        )
    else:
        if session.status not in {"LIVE", "PAUSED"}:
            raise DraftDomainError(
                409,
                "session_state",
                "Corrections are allowed only while a draft is live or paused.",
                {"status": session.status},
            )
        target = _active_target(picks, payload.target_event_id or 0)
        if payload.type == "pick_reversed":
            if target.overall_pick != len(picks):
                raise DraftDomainError(
                    409,
                    "invalid_draft_order",
                    "Undo only the latest pick; replace an earlier pick instead.",
                    {"latest_overall_pick": len(picks)},
                )
            event = _add_event(
                db,
                session,
                event_type="pick_reversed",
                idempotency_key=payload.idempotency_key,
                overall_pick=target.overall_pick,
                round_number=target.round,
                team_slot=target.team_slot,
                reason=payload.reason,
                supersedes_event_id=target.event.id,
            )
        else:
            player = _validate_player(db, session, payload.player_id or 0)
            drafted_elsewhere = {
                pick.player_id for pick in picks if pick.event.id != target.event.id
            }
            if player.id in drafted_elsewhere:
                raise DraftDomainError(
                    409,
                    "duplicate_pick",
                    "That replacement player has already been drafted.",
                    {"player_id": player.id},
                )
            _add_event(
                db,
                session,
                event_type="pick_reversed",
                idempotency_key=f"{payload.idempotency_key}:reverse",
                overall_pick=target.overall_pick,
                round_number=target.round,
                team_slot=target.team_slot,
                reason=payload.reason,
                supersedes_event_id=target.event.id,
            )
            event = _add_event(
                db,
                session,
                event_type="pick_replaced",
                idempotency_key=payload.idempotency_key,
                overall_pick=target.overall_pick,
                round_number=target.round,
                team_slot=target.team_slot,
                player=player,
                reason=payload.reason,
                supersedes_event_id=target.event.id,
            )

    try:
        db.flush()
        updated_picks = reduce_picks(_events(db, session.id))
        if len(updated_picks) == total and session.status == "LIVE":
            _add_event(
                db,
                session,
                event_type="session_completed",
                idempotency_key=f"{payload.idempotency_key}:complete",
            )
            session.status = "COMPLETE"
            session.completed_at = _utcnow()
            if session.kind == "live":
                project_live_roster(db, session)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DraftDomainError(409, "draft_conflict", "The draft changed concurrently.") from exc
    db.refresh(session)
    if session.status in {"LIVE", "PAUSED"}:
        publish_recommendation_snapshot(db, session, reduce_picks(_events(db, session.id)))
    return session, event


def board_payload(db: Session, session: DraftSession) -> dict[str, object]:
    events = _events(db, session.id)
    picks = reduce_picks(events)
    league = db.get(League, session.league_id)
    bye_weeks = team_bye_weeks(db, league.season) if league else {}
    player_rows = (
        list(
            db.scalars(
                select(ProjectionSnapshotRow)
                .options(joinedload(ProjectionSnapshotRow.league_player))
                .where(ProjectionSnapshotRow.snapshot_id == session.projection_snapshot_id)
                .order_by(ProjectionSnapshotRow.projected_points.desc())
            ).unique()
        )
        if session.projection_snapshot_id
        else []
    )
    by_player = {
        row.league_player_id: row
        for row in player_rows
        if row.league_player_id and row.league_player is not None
    }
    drafted_ids = {pick.player_id for pick in picks}
    preferences = list(
        db.scalars(
            select(DraftBoardPreference)
            .where(DraftBoardPreference.session_id == session.id)
            .order_by(DraftBoardPreference.queue_rank.is_(None), DraftBoardPreference.queue_rank)
        )
    )
    preference_by_player = {item.player_id: item for item in preferences}

    def player_payload(row: ProjectionSnapshotRow) -> dict[str, object]:
        player = row.league_player
        assert player is not None
        preference = preference_by_player.get(player.id)
        return {
            "id": player.id,
            "athlete_id": row.athlete_id,
            "name": player.name,
            "pro_team": player.pro_team,
            "bye_week": player_bye_week(player.pro_team, bye_weeks),
            "position": row.position,
            "projected_points": row.projected_points,
            "floor": row.floor,
            "ceiling": row.ceiling,
            "risk": row.risk,
            "queue_rank": preference.queue_rank if preference else None,
            "target": preference.target if preference else False,
            "fade": preference.fade if preference else False,
            "note": preference.note if preference else None,
        }

    pick_payloads = []
    rosters: dict[int, list[dict[str, object]]] = {
        slot: [] for slot in range(1, session.team_count + 1)
    }
    for pick in picks:
        row = by_player.get(pick.player_id)
        player = row.league_player if row else pick.event.player
        payload = {
            "event_id": pick.event.id,
            "sequence": pick.event.sequence,
            "overall_pick": pick.overall_pick,
            "round": pick.round,
            "team_slot": pick.team_slot,
            "player_id": pick.player_id,
            "player_name": player.name if player else "Unknown player",
            "position": player.position if player else None,
            "pro_team": player.pro_team if player else None,
            "bye_week": player_bye_week(player.pro_team if player else None, bye_weeks),
            "projected_points": row.projected_points if row else None,
            "source": pick.event.source,
            "recommendation_snapshot_id": pick.event.recommendation_snapshot_id,
        }
        pick_payloads.append(payload)
        rosters[pick.team_slot].append(payload)

    total = session.team_count * session.round_count
    next_overall = len(picks) + 1 if len(picks) < total else None
    current_team_slot = (
        snake_team_slot(next_overall, session.team_count) if next_overall is not None else None
    )
    teams = list(
        db.scalars(
            select(DraftTeam).where(DraftTeam.session_id == session.id).order_by(DraftTeam.slot)
        )
    )
    return {
        "session_id": session.id,
        "status": session.status,
        "current_sequence": session.current_sequence,
        "preference_revision": session.preference_revision,
        "total_picks": total,
        "completed_picks": len(picks),
        "current_overall_pick": next_overall,
        "current_round": (
            snake_round(next_overall, session.team_count) if next_overall is not None else None
        ),
        "current_team_slot": current_team_slot,
        "owner_on_clock": current_team_slot == session.owner_team_slot,
        "opponent_mode": opponent_mode(session),
        "next_owner_pick": next_owner_pick(
            len(picks), session.team_count, session.owner_team_slot, total
        ),
        "teams": [{**_team_payload(team), "roster": rosters[team.slot]} for team in teams],
        "picks": pick_payloads,
        "available_players": [
            player_payload(row)
            for row in player_rows
            if row.league_player_id not in drafted_ids and row.league_player is not None
        ],
        "queue": [
            player_payload(by_player[item.player_id])
            for item in preferences
            if item.queue_rank is not None
            and item.player_id not in drafted_ids
            and item.player_id in by_player
        ],
        "freshness": {
            "source_mode": session.source_mode,
            "projection_snapshot_id": session.projection_snapshot_id,
            "provisional": session.source_mode.endswith("_shadow"),
        },
    }


def latest_recommendation_payload(db: Session, session: DraftSession) -> dict[str, object]:
    picks = reduce_picks(_events(db, session.id))
    snapshot = db.scalar(
        select(DraftRecommendationSnapshot)
        .where(
            DraftRecommendationSnapshot.session_id == session.id,
            DraftRecommendationSnapshot.session_sequence == session.current_sequence,
        )
        .order_by(DraftRecommendationSnapshot.id.desc())
    )
    if session.status in {"LIVE", "PAUSED"} and (
        snapshot is None or snapshot.algorithm_version != ALGORITHM_VERSION
    ):
        snapshot = publish_recommendation_snapshot(db, session, picks)
    return recommendation_payload(snapshot, session, picks)


def preferences_payload(db: Session, session: DraftSession) -> dict[str, object]:
    items = list(
        db.scalars(
            select(DraftBoardPreference)
            .where(DraftBoardPreference.session_id == session.id)
            .order_by(DraftBoardPreference.queue_rank.is_(None), DraftBoardPreference.queue_rank)
        )
    )
    return {
        "revision": session.preference_revision,
        "items": [
            {
                "player_id": item.player_id,
                "queue_rank": item.queue_rank,
                "target": item.target,
                "fade": item.fade,
                "note": item.note,
            }
            for item in items
        ],
    }


@coordinated_mutation
def replace_preferences(
    db: Session, session_id: int, payload: DraftPreferenceUpdate
) -> dict[str, object]:
    session = get_session(db, session_id)
    if session.preference_revision != payload.expected_revision:
        raise DraftDomainError(
            409,
            "preference_conflict",
            "The queue changed in another tab. Reload it before reordering.",
            {"current_revision": session.preference_revision},
        )
    player_ids = {item.player_id for item in payload.items}
    valid_ids = set(
        db.scalars(
            select(Player.id).where(
                Player.league_id == session.league_id,
                Player.id.in_(player_ids) if player_ids else False,
            )
        )
    )
    if valid_ids != player_ids:
        raise DraftDomainError(
            422,
            "player_not_in_league",
            "Every preference must reference this league's player pool.",
        )
    db.execute(delete(DraftBoardPreference).where(DraftBoardPreference.session_id == session.id))
    for item in payload.items:
        db.add(
            DraftBoardPreference(
                session_id=session.id,
                player_id=item.player_id,
                queue_rank=item.queue_rank,
                target=item.target,
                fade=item.fade,
                note=item.note,
            )
        )
    session.preference_revision += 1
    db.commit()
    db.refresh(session)
    return preferences_payload(db, session)


def event_payload(event: DraftEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "sequence": event.sequence,
        "type": event.type,
        "overall_pick": event.overall_pick,
        "round": event.round,
        "team_slot": event.team_slot,
        "player_id": event.player_id,
        "athlete_id": event.athlete_id,
        "player_name": event.player.name if event.player else None,
        "source": event.source,
        "metadata": json.loads(event.metadata_json or "{}"),
        "reason": event.reason,
        "supersedes_event_id": event.supersedes_event_id,
        "recommendation_snapshot_id": event.recommendation_snapshot_id,
        "recorded_at": event.recorded_at,
    }
