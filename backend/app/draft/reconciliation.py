from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Player
from .errors import DraftDomainError, not_found
from .events import coordinated_mutation
from .models import (
    DraftEvent,
    DraftRecommendationSnapshot,
    DraftReconciliationConflict,
    DraftSession,
    YahooAuthorityEvidence,
)
from .recommendations import publish_recommendation_snapshot
from .reducer import reduce_picks, snake_round, snake_team_slot
from .schemas import (
    DraftConflictResolution,
    DraftSourceModeUpdate,
    DraftYahooObservation,
)


def _check_sequence(session: DraftSession, expected: int) -> None:
    if session.current_sequence != expected:
        raise DraftDomainError(
            409,
            "draft_conflict",
            "The draft changed before Yahoo reconciliation completed.",
            {"expected_sequence": expected, "current_sequence": session.current_sequence},
        )


def _events(db: Session, session_id: int) -> list[DraftEvent]:
    return list(
        db.scalars(
            select(DraftEvent)
            .where(DraftEvent.session_id == session_id)
            .order_by(DraftEvent.sequence)
        )
    )


def _latest_snapshot(db: Session, session: DraftSession) -> int | None:
    return db.scalar(
        select(DraftRecommendationSnapshot.id)
        .where(
            DraftRecommendationSnapshot.session_id == session.id,
            DraftRecommendationSnapshot.session_sequence == session.current_sequence,
            DraftRecommendationSnapshot.status == "ready",
        )
        .order_by(DraftRecommendationSnapshot.id.desc())
    )


def _append(
    db: Session,
    session: DraftSession,
    *,
    event_type: str,
    idempotency_key: str,
    overall_pick: int | None = None,
    team_slot: int | None = None,
    player: Player | None = None,
    provider_key: str | None = None,
    provider_revision: str | None = None,
    supersedes_event_id: int | None = None,
    recommendation_snapshot_id: int | None = None,
) -> DraftEvent:
    session.current_sequence += 1
    event = DraftEvent(
        session_id=session.id,
        sequence=session.current_sequence,
        type=event_type,
        overall_pick=overall_pick,
        round=(snake_round(overall_pick, session.team_count) if overall_pick else None),
        team_slot=team_slot,
        player_id=player.id if player else None,
        athlete_id=player.athlete_id if player else None,
        source=session.source_mode,
        idempotency_key=idempotency_key,
        provider_key=provider_key,
        provider_revision=provider_revision,
        observed_at=datetime.now(UTC),
        supersedes_event_id=supersedes_event_id,
        recommendation_snapshot_id=recommendation_snapshot_id,
    )
    db.add(event)
    db.flush()
    return event


def _player_for_observation(
    db: Session, session: DraftSession, observation: DraftYahooObservation
) -> Player | None:
    if observation.player_id:
        player = db.get(Player, observation.player_id)
        return player if player and player.league_id == session.league_id else None
    if observation.external_player_id:
        return db.scalar(
            select(Player).where(
                Player.league_id == session.league_id,
                Player.source_id == observation.external_player_id,
            )
        )
    return None


def _proposal(
    db: Session,
    session: DraftSession,
    observation: DraftYahooObservation,
    canonical_event: DraftEvent | None,
) -> DraftReconciliationConflict:
    existing = db.scalar(
        select(DraftReconciliationConflict).where(
            DraftReconciliationConflict.session_id == session.id,
            DraftReconciliationConflict.provider_key == observation.provider_key,
            DraftReconciliationConflict.provider_revision == observation.provider_revision,
        )
    )
    if existing is not None:
        if existing.status == "resolved" and existing.resolution_action in {
            "accept_incoming",
            "map_player_and_accept",
            "auto_applied",
        }:
            existing.status = "unresolved"
            existing.canonical_event_id = canonical_event.id if canonical_event else None
            existing.detected_sequence = session.current_sequence
            existing.resolution_action = None
            existing.resolving_event_ids_json = "[]"
            existing.resolved_at = None
            db.flush()
        return existing
    conflict = DraftReconciliationConflict(
        session_id=session.id,
        overall_pick=observation.overall_pick,
        status="unresolved",
        canonical_event_id=canonical_event.id if canonical_event else None,
        incoming_payload_json=observation.model_dump_json(),
        provider_key=observation.provider_key,
        provider_revision=observation.provider_revision,
        detected_sequence=session.current_sequence,
    )
    db.add(conflict)
    db.flush()
    return conflict


def _matching_conflict(
    db: Session, session_id: int, observation: DraftYahooObservation
) -> DraftReconciliationConflict | None:
    return db.scalar(
        select(DraftReconciliationConflict).where(
            DraftReconciliationConflict.session_id == session_id,
            DraftReconciliationConflict.provider_key == observation.provider_key,
            DraftReconciliationConflict.provider_revision == observation.provider_revision,
        )
    )


def _matches_draft_placeholder(
    current_player: Player | None,
    incoming_player: Player,
    observation: DraftYahooObservation,
) -> bool:
    if current_player is None or current_player.source_id != f"draft.{observation.overall_pick}":
        return False
    return (
        current_player.name.strip().casefold() == incoming_player.name.strip().casefold()
        and current_player.pro_team.strip().upper() == incoming_player.pro_team.strip().upper()
        and current_player.position.strip().upper() == incoming_player.position.strip().upper()
    )


@coordinated_mutation
def reconcile_observations(
    db: Session,
    session: DraftSession,
    observations: list[DraftYahooObservation],
    *,
    expected_sequence: int,
) -> dict[str, object]:
    _check_sequence(session, expected_sequence)
    events = _events(db, session.id)
    canonical = {pick.overall_pick: pick for pick in reduce_picks(events)}
    applied: list[int] = []
    confirmed: list[int] = []
    proposals: list[int] = []
    authoritative = session.source_mode.endswith("_authoritative")

    for observation in sorted(observations, key=lambda item: item.overall_pick):
        if observation.overall_pick > session.team_count * session.round_count:
            conflict = _proposal(db, session, observation, None)
            proposals.append(conflict.id)
            continue
        player = _player_for_observation(db, session, observation)
        current = canonical.get(observation.overall_pick)
        idempotency = (
            f"{session.source_mode}:{observation.provider_key}:{observation.provider_revision}"
        )
        existing_event = db.scalar(
            select(DraftEvent).where(
                DraftEvent.session_id == session.id,
                DraftEvent.idempotency_key == idempotency,
            )
        )
        if player is not None and current is not None and current.player_id == player.id:
            if existing_event is not None:
                confirmed.append(existing_event.id)
                continue
            event = _append(
                db,
                session,
                event_type="provider_confirmed",
                idempotency_key=idempotency,
                overall_pick=observation.overall_pick,
                team_slot=current.team_slot,
                player=player,
                provider_key=observation.provider_key,
                provider_revision=observation.provider_revision,
            )
            confirmed.append(event.id)
            continue
        safe_identity_upgrade = (
            authoritative
            and player is not None
            and existing_event is None
            and current is not None
            and observation.team_slot == current.team_slot
            and observation.team_slot
            == snake_team_slot(observation.overall_pick, session.team_count)
            and _matches_draft_placeholder(current.event.player, player, observation)
        )
        if safe_identity_upgrade:
            event = _append(
                db,
                session,
                event_type="pick_replaced",
                idempotency_key=idempotency,
                overall_pick=observation.overall_pick,
                team_slot=observation.team_slot,
                player=player,
                provider_key=observation.provider_key,
                provider_revision=observation.provider_revision,
                supersedes_event_id=current.event.id,
                recommendation_snapshot_id=current.event.recommendation_snapshot_id,
            )
            canonical[observation.overall_pick] = reduce_picks([event])[0]
            conflict = _matching_conflict(db, session.id, observation)
            if conflict is not None and conflict.status == "unresolved":
                conflict.status = "resolved"
                conflict.resolution_action = "auto_applied"
                conflict.resolving_event_ids_json = json.dumps([event.id])
                conflict.resolved_at = datetime.now(UTC)
            applied.append(event.id)
            continue
        expected_overall = len(canonical) + 1
        expected_team = snake_team_slot(observation.overall_pick, session.team_count)
        safe_new_pick = (
            player is not None
            and existing_event is None
            and current is None
            and observation.overall_pick == expected_overall
            and observation.team_slot == expected_team
        )
        if authoritative and safe_new_pick:
            event = _append(
                db,
                session,
                event_type="pick_recorded",
                idempotency_key=idempotency,
                overall_pick=observation.overall_pick,
                team_slot=observation.team_slot,
                player=player,
                provider_key=observation.provider_key,
                provider_revision=observation.provider_revision,
                recommendation_snapshot_id=(
                    _latest_snapshot(db, session)
                    if observation.team_slot == session.owner_team_slot
                    else None
                ),
            )
            canonical[observation.overall_pick] = reduce_picks([event])[0]
            conflict = _matching_conflict(db, session.id, observation)
            if conflict is not None and conflict.status == "unresolved":
                conflict.status = "resolved"
                conflict.resolution_action = "auto_applied"
                conflict.resolving_event_ids_json = json.dumps([event.id])
                conflict.resolved_at = datetime.now(UTC)
            applied.append(event.id)
            continue
        conflict = _proposal(db, session, observation, current.event if current else None)
        proposals.append(conflict.id)

    total = session.team_count * session.round_count
    if applied and len(canonical) == total and session.status == "LIVE":
        _append(
            db,
            session,
            event_type="session_completed",
            idempotency_key=f"yahoo-auto-complete:{applied[-1]}",
        )
        session.status = "COMPLETE"
        session.completed_at = datetime.now(UTC)

    db.commit()
    db.refresh(session)
    if session.status in {"LIVE", "PAUSED"} and (confirmed or applied):
        publish_recommendation_snapshot(db, session, reduce_picks(_events(db, session.id)))
    return {
        "session_id": session.id,
        "current_sequence": session.current_sequence,
        "source_mode": session.source_mode,
        "applied_event_ids": applied,
        "confirmed_event_ids": confirmed,
        "proposal_ids": proposals,
        "counts": {
            "applied": len(applied),
            "confirmed": len(confirmed),
            "proposed": len(proposals),
        },
    }


def conflicts_payload(db: Session, session_id: int) -> list[dict[str, object]]:
    rows = list(
        db.scalars(
            select(DraftReconciliationConflict)
            .where(DraftReconciliationConflict.session_id == session_id)
            .order_by(
                DraftReconciliationConflict.status.desc(),
                DraftReconciliationConflict.overall_pick,
            )
        )
    )
    return [
        {
            "id": row.id,
            "overall_pick": row.overall_pick,
            "status": row.status,
            "canonical_event_id": row.canonical_event_id,
            "incoming": json.loads(row.incoming_payload_json),
            "detected_sequence": row.detected_sequence,
            "resolution_action": row.resolution_action,
            "created_at": row.created_at,
            "resolved_at": row.resolved_at,
        }
        for row in rows
    ]


@coordinated_mutation
def resolve_conflict(
    db: Session,
    session: DraftSession,
    conflict_id: int,
    payload: DraftConflictResolution,
) -> dict[str, object]:
    _check_sequence(session, payload.expected_sequence)
    conflict = db.get(DraftReconciliationConflict, conflict_id)
    if conflict is None or conflict.session_id != session.id:
        raise not_found("draft_conflict", conflict_id)
    if conflict.status != "unresolved":
        return {"conflict_id": conflict.id, "status": conflict.status, "event_ids": []}
    observation = DraftYahooObservation.model_validate_json(conflict.incoming_payload_json)
    if payload.action in {"keep_canonical", "ignore_incoming"}:
        conflict.status = "ignored" if payload.action == "ignore_incoming" else "resolved"
        conflict.resolution_action = payload.action
        conflict.resolved_at = datetime.now(UTC)
        db.commit()
        return {"conflict_id": conflict.id, "status": conflict.status, "event_ids": []}

    player_id = payload.player_id or observation.player_id
    player = db.get(Player, player_id) if player_id else None
    if player is None or player.league_id != session.league_id or player.athlete_id is None:
        raise DraftDomainError(
            422,
            "player_identity_pending",
            "Map the incoming Yahoo player to this league before accepting it.",
        )
    events = _events(db, session.id)
    current = next(
        (pick for pick in reduce_picks(events) if pick.overall_pick == conflict.overall_pick),
        None,
    )
    appended: list[DraftEvent] = []
    base_key = f"resolve:{conflict.id}:{conflict.detected_sequence}:{observation.provider_revision}"
    if current is not None:
        appended.append(
            _append(
                db,
                session,
                event_type="pick_reversed",
                idempotency_key=f"{base_key}:reverse",
                overall_pick=current.overall_pick,
                team_slot=current.team_slot,
                supersedes_event_id=current.event.id,
            )
        )
        appended.append(
            _append(
                db,
                session,
                event_type="pick_replaced",
                idempotency_key=base_key,
                overall_pick=current.overall_pick,
                team_slot=current.team_slot,
                player=player,
                provider_key=observation.provider_key,
                provider_revision=observation.provider_revision,
                supersedes_event_id=current.event.id,
            )
        )
    else:
        active = reduce_picks(events)
        if conflict.overall_pick != len(active) + 1:
            raise DraftDomainError(
                409,
                "invalid_draft_order",
                "Resolve missing Yahoo picks in overall order.",
                {"expected_overall_pick": len(active) + 1},
            )
        appended.append(
            _append(
                db,
                session,
                event_type="pick_recorded",
                idempotency_key=base_key,
                overall_pick=conflict.overall_pick,
                team_slot=snake_team_slot(conflict.overall_pick, session.team_count),
                player=player,
                provider_key=observation.provider_key,
                provider_revision=observation.provider_revision,
                recommendation_snapshot_id=(
                    _latest_snapshot(db, session)
                    if observation.team_slot == session.owner_team_slot
                    else None
                ),
            )
        )
    conflict.status = "resolved"
    conflict.resolution_action = payload.action
    conflict.resolving_event_ids_json = json.dumps([event.id for event in appended])
    conflict.resolved_at = datetime.now(UTC)
    db.commit()
    db.refresh(session)
    if session.status in {"LIVE", "PAUSED"}:
        publish_recommendation_snapshot(db, session, reduce_picks(_events(db, session.id)))
    return {
        "conflict_id": conflict.id,
        "status": conflict.status,
        "event_ids": [event.id for event in appended],
        "current_sequence": session.current_sequence,
    }


@coordinated_mutation
def change_source_mode(
    db: Session,
    session: DraftSession,
    payload: DraftSourceModeUpdate,
) -> DraftSession:
    _check_sequence(session, payload.expected_sequence)
    if payload.mode.endswith("_authoritative"):
        expected_shadow_mode = payload.mode.replace("_authoritative", "_shadow")
        if session.source_mode != expected_shadow_mode:
            raise DraftDomainError(
                409,
                "shadow_validation_required",
                "Enable the matching Yahoo approval mode before auto-approving picks.",
                {"required_mode": expected_shadow_mode, "current_mode": session.source_mode},
            )
        evidence = (
            db.get(YahooAuthorityEvidence, payload.authority_evidence_id)
            if payload.authority_evidence_id
            else None
        )
        expected_transport = "scrape" if "scrape" in payload.mode else "oauth"
        evidence_valid = bool(
            evidence is not None
            and evidence.league_id == session.league_id
            and evidence.transport == expected_transport
            and evidence.passed
            and evidence.consecutive_pick_count >= 50
        )
        if not payload.owner_confirmed and not evidence_valid:
            raise DraftDomainError(
                422,
                "authority_evidence_required",
                (
                    "Confirm the Yahoo approval feed or provide a passing compatible "
                    "50-pick rehearsal."
                ),
            )
        session.authority_evidence_id = evidence.id if evidence_valid and evidence else None
    else:
        session.authority_evidence_id = None
    session.source_mode = payload.mode
    _append(
        db,
        session,
        event_type="source_mode_changed",
        idempotency_key=f"source-mode:{session.current_sequence + 1}:{payload.mode}",
    )
    db.commit()
    db.refresh(session)
    if session.status in {"LIVE", "PAUSED"}:
        publish_recommendation_snapshot(db, session, reduce_picks(_events(db, session.id)))
    return session
