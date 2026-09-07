from __future__ import annotations

import json
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .errors import DraftDomainError
from .events import coordinated_mutation
from .models import DraftEvent, DraftRankingSnapshot, DraftSession, ProjectionSnapshotRow
from .reducer import reduce_picks, snake_team_slot
from .schemas import DraftEventRequest, DraftOpponentPickRequest
from .session import (
    _events,
    _existing_idempotent_event,
    append_pick_event,
    get_session,
    opponent_mode,
)

POLICY_VERSION = "mock-team-aware-v1"
FLEX_POSITIONS = {"RB", "WR", "TE"}
NON_PLAYER_SLOTS = {"BN", "BENCH", "IR", "FLEX", "W/R/T", "W/R", "Q/W/R/T"}


def _ranking_rows(snapshot: DraftRankingSnapshot) -> list[dict[str, object]]:
    try:
        value = json.loads(snapshot.rows_json or "[]")
    except json.JSONDecodeError:
        return []
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _rank(row: dict[str, object], fallback: int) -> float:
    for key in ("overall_rank", "adp"):
        try:
            value = float(row.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return float(fallback)


def select_opponent_player(db: Session, session: DraftSession) -> tuple[int, dict[str, object]]:
    if session.ranking_snapshot_id is None:
        raise DraftDomainError(
            422, "ranking_required", "Sync rankings before advancing automatic opponents."
        )
    ranking = db.get(DraftRankingSnapshot, session.ranking_snapshot_id)
    if ranking is None or ranking.status != "ready":
        raise DraftDomainError(
            422, "ranking_incomplete", "The bound ranking snapshot is not ready."
        )
    picks = reduce_picks(_events(db, session.id))
    drafted = {pick.player_id for pick in picks}
    next_overall = len(picks) + 1
    team_slot = snake_team_slot(next_overall, session.team_count)
    team_pick_ids = [pick.player_id for pick in picks if pick.team_slot == team_slot]

    projection_rows = list(
        db.scalars(
            select(ProjectionSnapshotRow)
            .options(joinedload(ProjectionSnapshotRow.league_player))
            .where(ProjectionSnapshotRow.snapshot_id == session.projection_snapshot_id)
        ).unique()
    )
    projection_by_player = {
        row.league_player_id: row
        for row in projection_rows
        if row.league_player_id is not None and row.league_player is not None
    }
    position_by_player = {
        player_id: row.position.upper() for player_id, row in projection_by_player.items()
    }
    roster = Counter(position_by_player.get(player_id, "") for player_id in team_pick_ids)
    try:
        configured_slots = [
            str(slot).upper() for slot in json.loads(session.roster_slots_snapshot_json or "[]")
        ]
    except (TypeError, json.JSONDecodeError):
        configured_slots = []
    targets = Counter(slot for slot in configured_slots if slot not in NON_PLAYER_SLOTS)
    flex_target = configured_slots.count("FLEX") + configured_slots.count("W/R/T")
    remaining_roster_picks = max(0, session.round_count - len(team_pick_ids))
    unmet_starters = sum(max(0, count - roster[position]) for position, count in targets.items())

    ranked = _ranking_rows(ranking)
    choices: list[tuple[float, float, float, int, dict[str, object]]] = []
    for fallback, rank_row in enumerate(ranked, start=1):
        raw_player_id = rank_row.get("player_id")
        if not str(raw_player_id or "").isdigit():
            continue
        player_id = int(raw_player_id)
        projection = projection_by_player.get(player_id)
        if projection is None or player_id in drafted:
            continue
        position = projection.position.upper()
        base_rank = _rank(rank_row, fallback)
        open_direct = max(0, targets[position] - roster[position])
        urgent = remaining_roster_picks <= unmet_starters
        need_bonus = open_direct * (14.0 if urgent else 7.0)
        if position in FLEX_POSITIONS and flex_target and not open_direct:
            flex_filled = sum(roster[item] for item in FLEX_POSITIONS)
            need_bonus += (
                3.0
                if flex_filled < sum(targets[item] for item in FLEX_POSITIONS) + flex_target
                else 0.0
            )
        overfill = max(0, roster[position] - targets[position])
        depth_penalty = overfill * 4.0
        selection_score = base_rank - need_bonus + depth_penalty
        choices.append(
            (
                selection_score,
                base_rank,
                -projection.projected_points,
                projection.athlete_id,
                {
                    "player_id": player_id,
                    "team_slot": team_slot,
                    "overall_pick": next_overall,
                    "base_rank": base_rank,
                    "selection_score": round(selection_score, 4),
                    "position": position,
                    "need_bonus": need_bonus,
                    "depth_penalty": depth_penalty,
                },
            )
        )
    if not choices:
        raise DraftDomainError(
            409, "mock_pool_exhausted", "No ranked, projected player remains for the next pick."
        )
    choices.sort(key=lambda item: (item[0], item[1], item[2], item[3], int(item[4]["player_id"])))
    selected = choices[0][4]
    return int(selected["player_id"]), selected


@coordinated_mutation
def advance_opponent_pick(
    db: Session, session_id: int, payload: DraftOpponentPickRequest
) -> tuple[DraftSession, DraftEvent]:
    session = get_session(db, session_id)
    existing = _existing_idempotent_event(db, session.id, payload.idempotency_key)
    if existing is not None:
        return session, existing
    if session.kind != "mock" or opponent_mode(session) != "automatic":
        raise DraftDomainError(
            409,
            "automatic_mock_disabled",
            "Automatic opponents are enabled only for automatic mock sessions.",
        )
    if session.source_mode != "manual":
        raise DraftDomainError(
            409,
            "synthetic_source_forbidden",
            "Synthetic picks cannot run while a Yahoo source is active.",
        )
    if session.status != "LIVE":
        raise DraftDomainError(
            409,
            "session_state",
            "Automatic opponents run only while the mock is live.",
            {"status": session.status},
        )
    picks = reduce_picks(_events(db, session.id))
    total = session.team_count * session.round_count
    if len(picks) >= total:
        raise DraftDomainError(409, "draft_complete", "Every configured pick is filled.")
    team_slot = snake_team_slot(len(picks) + 1, session.team_count)
    if team_slot == session.owner_team_slot:
        raise DraftDomainError(
            409, "owner_on_clock", "Automatic opponents stop when the owner is on the clock."
        )
    player_id, decision = select_opponent_player(db, session)
    return append_pick_event(
        db,
        session_id,
        DraftEventRequest(
            type="pick_recorded",
            expected_sequence=payload.expected_sequence,
            idempotency_key=payload.idempotency_key,
            player_id=player_id,
        ),
        source="mock_auto",
        metadata={"policy_version": POLICY_VERSION, **decision},
    )
