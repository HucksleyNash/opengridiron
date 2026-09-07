from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..models import League
from ..services.projections import (
    effective_projection_points,
    scale_projection_range,
    scoring_context,
)
from .bye_weeks import player_bye_week, team_bye_weeks
from .evidence import attributed_evidence
from .forecasting import availability_guidance
from .models import (
    DraftRecommendationSnapshot,
    DraftSession,
    ProjectionSnapshot,
    ProjectionSnapshotRow,
)
from .rankings import market_ranks, normalized_market_values
from .reducer import ReducedPick, next_owner_pick

ALGORITHM_VERSION = "draft-score-v9"

MEANINGFUL_LINEUP_UPGRADE_POINTS = 20.0
VOR_NORMALIZATION_WINDOW = 60
MAX_BYE_CONGESTION_PENALTY = 6.0
SCORE_WEIGHTS = {
    "normalized_vor": 0.20,
    "roster_need": 0.50,
    "depth_need": 0.10,
    "tier_urgency": 0.08,
    "risk_adjustment": 0.05,
    "market_value": 0.07,
}

NON_STARTER_SLOTS = {"BENCH", "BN", "IR", "NA"}
FLEX_SLOT_POSITIONS = {
    "FLEX": {"RB", "WR", "TE"},
    "W/R/T": {"RB", "WR", "TE"},
    "WR/RB/TE": {"RB", "WR", "TE"},
    "RB/WR/TE": {"RB", "WR", "TE"},
    "W/R": {"RB", "WR"},
    "WR/RB": {"RB", "WR"},
    "RB/WR": {"RB", "WR"},
    "W/T": {"WR", "TE"},
    "WR/TE": {"WR", "TE"},
    "SUPERFLEX": {"QB", "RB", "WR", "TE"},
    "Q/W/R/T": {"QB", "RB", "WR", "TE"},
    "QB/WR/RB/TE": {"QB", "RB", "WR", "TE"},
    "OP": {"QB", "RB", "WR", "TE"},
}
POSITION_ALIASES = {"D/ST": "DEF", "DST": "DEF"}


@dataclass(frozen=True)
class CandidateInput:
    row: ProjectionSnapshotRow
    projected_points: float
    floor: float
    ceiling: float
    scoring_source: str
    scoring_breakdown: dict[str, float]
    vor: float
    roster_need: float
    depth_need: float
    lineup_delta: float
    roster_impact: str
    tier_urgency: float
    risk_adjustment: float
    tier_cliff: bool
    vor_drop: float
    market_rank: float | None
    market_value: float


@dataclass(frozen=True)
class LineupPlayerProjection:
    positions: frozenset[str]
    projected_points: float


@dataclass(frozen=True)
class LineupContext:
    slots: tuple[tuple[str, frozenset[str]], ...]
    direct_slots: tuple[tuple[str, frozenset[str]], ...]
    states: dict[int, float]
    direct_states: dict[int, float]


@dataclass(frozen=True)
class MarginalLineupUtility:
    roster_need: float
    projected_delta: float
    impact: str


def _json_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def recommendation_input_hash(
    session: DraftSession, picks: list[ReducedPick], bye_weeks: dict[str, int] | None = None
) -> str:
    return _json_hash(
        {
            "projection_snapshot_id": session.projection_snapshot_id,
            "ranking_snapshot_id": session.ranking_snapshot_id,
            "session_sequence": session.current_sequence,
            "config_version": session.config_version,
            "algorithm_version": ALGORITHM_VERSION,
            "strategy_mode": session.strategy_mode,
            "scoring_snapshot": session.scoring_snapshot_json,
            "roster_slots_snapshot": session.roster_slots_snapshot_json,
            "picks": [(pick.overall_pick, pick.player_id, pick.event.id) for pick in picks],
            "bye_weeks": bye_weeks or {},
        }
    )


def _normalize_position(position: str) -> str:
    normalized = position.strip().upper()
    return POSITION_ALIASES.get(normalized, normalized)


def _slot_positions(slot: str) -> set[str]:
    normalized = slot.strip().upper()
    if normalized in NON_STARTER_SLOTS:
        return set()
    if normalized in FLEX_SLOT_POSITIONS:
        return FLEX_SLOT_POSITIONS[normalized]
    return {_normalize_position(normalized)}


def _row_positions(row: ProjectionSnapshotRow) -> frozenset[str]:
    positions = {_normalize_position(row.position)}
    try:
        eligibility = json.loads(row.eligibility_json or "[]")
    except json.JSONDecodeError:
        eligibility = []
    if isinstance(eligibility, list):
        for value in eligibility:
            if not isinstance(value, str):
                continue
            normalized = value.strip().upper()
            if normalized in NON_STARTER_SLOTS or normalized in FLEX_SLOT_POSITIONS:
                continue
            positions.add(_normalize_position(normalized))
    return frozenset(positions)


def _lineup_states(
    players: list[LineupPlayerProjection],
    slots: tuple[tuple[str, frozenset[str]], ...],
) -> dict[int, float]:
    states = {0: 0.0}
    for player in players:
        next_states = dict(states)
        for mask, total in states.items():
            for index, (_slot, eligible_positions) in enumerate(slots):
                bit = 1 << index
                if mask & bit or player.positions.isdisjoint(eligible_positions):
                    continue
                next_mask = mask | bit
                next_total = total + player.projected_points
                if next_total > next_states.get(next_mask, float("-inf")):
                    next_states[next_mask] = next_total
        states = next_states
    return states


def _best_lineup_state(states: dict[int, float]) -> tuple[int, float]:
    mask, total = max(states.items(), key=lambda item: (item[0].bit_count(), item[1]))
    return mask.bit_count(), total


def _best_lineup_with_candidate(
    candidate: LineupPlayerProjection,
    slots: tuple[tuple[str, frozenset[str]], ...],
    states: dict[int, float],
) -> tuple[int, float, str] | None:
    best: tuple[int, float, str] | None = None
    best_key: tuple[int, float, int] | None = None
    for mask, total in states.items():
        for index, (slot, eligible_positions) in enumerate(slots):
            bit = 1 << index
            if mask & bit or candidate.positions.isdisjoint(eligible_positions):
                continue
            result = (mask.bit_count() + 1, total + candidate.projected_points, slot)
            key = (result[0], result[1], -index)
            if best_key is None or key > best_key:
                best = result
                best_key = key
    return best


def _build_lineup_context(
    owner_players: list[LineupPlayerProjection], roster_slots: list[str]
) -> LineupContext:
    slots = tuple(
        (slot.strip().upper(), frozenset(_slot_positions(slot)))
        for slot in roster_slots
        if _slot_positions(slot)
    )
    direct_slots = tuple(slot for slot in slots if len(slot[1]) == 1)
    return LineupContext(
        slots=slots,
        direct_slots=direct_slots,
        states=_lineup_states(owner_players, slots),
        direct_states=_lineup_states(owner_players, direct_slots),
    )


def _marginal_lineup_utility(
    candidate: LineupPlayerProjection, context: LineupContext
) -> MarginalLineupUtility:
    base_mask, base_points = max(
        context.states.items(), key=lambda item: (item[0].bit_count(), item[1])
    )
    base_filled = base_mask.bit_count()
    candidate_lineup = _best_lineup_with_candidate(candidate, context.slots, context.states)
    if candidate_lineup is None:
        return MarginalLineupUtility(0.0, 0.0, "Cannot fill a starting lineup slot")

    candidate_filled, candidate_points, candidate_slot = candidate_lineup
    projected_delta = round(max(0.0, candidate_points - base_points), 2)
    base_direct_filled, _base_direct_points = _best_lineup_state(context.direct_states)
    candidate_direct = _best_lineup_with_candidate(
        candidate, context.direct_slots, context.direct_states
    )
    if candidate_direct is not None and candidate_direct[0] > base_direct_filled:
        return MarginalLineupUtility(
            1.0,
            projected_delta,
            f"Fills an open {candidate_direct[2]} starter",
        )
    if candidate_filled > base_filled:
        opened_flex_slot = next(
            (
                slot
                for index, (slot, eligible_positions) in enumerate(context.slots)
                if not base_mask & (1 << index)
                and len(eligible_positions) > 1
                and not candidate.positions.isdisjoint(eligible_positions)
            ),
            candidate_slot,
        )
        return MarginalLineupUtility(
            0.75,
            projected_delta,
            f"Fills an open {opened_flex_slot} starter",
        )
    if projected_delta > 0:
        proportional_need = round(
            0.75 * min(1.0, projected_delta / MEANINGFUL_LINEUP_UPGRADE_POINTS),
            4,
        )
        return MarginalLineupUtility(
            proportional_need,
            projected_delta,
            f"Improves the projected starting lineup by {projected_delta:.1f} points",
        )
    return MarginalLineupUtility(0.0, 0.0, "Bench-only at current projections")


def _position_depth_target(position: str, roster_slots: list[str]) -> int:
    normalized_position = _normalize_position(position)
    direct_slots = 0
    flexible_slots = 0
    for slot in roster_slots:
        eligible_positions = _slot_positions(slot)
        if normalized_position not in eligible_positions:
            continue
        if len(eligible_positions) == 1:
            direct_slots += 1
        else:
            flexible_slots += 1

    if normalized_position in {"QB", "TE"}:
        return direct_slots + 1
    if normalized_position in {"K", "DEF"}:
        return direct_slots
    if normalized_position in {"RB", "WR"}:
        return direct_slots + flexible_slots + 2
    return direct_slots + flexible_slots + 1


def _position_is_saturated(
    position: str, owner_position_counts: Counter[str], roster_slots: list[str]
) -> bool:
    normalized_position = _normalize_position(position)
    return owner_position_counts[normalized_position] >= _position_depth_target(
        normalized_position, roster_slots
    )


def _bench_depth_need(
    position: str, owner_position_counts: Counter[str], roster_slots: list[str]
) -> float:
    if not any(slot.strip().upper() in NON_STARTER_SLOTS for slot in roster_slots):
        return 0.0
    normalized_position = _normalize_position(position)
    if _position_is_saturated(normalized_position, owner_position_counts, roster_slots):
        return 0.0
    if normalized_position in {"RB", "WR"}:
        return 0.35
    if normalized_position in {"QB", "TE"}:
        return 0.15
    return 0.0


def _replacement_baselines(
    rows: list[ProjectionSnapshotRow],
    roster_slots: list[str],
    team_count: int,
    points_by_row: dict[int, float] | None = None,
) -> dict[str, float]:
    points = points_by_row or {}

    def projected(row: ProjectionSnapshotRow) -> float:
        return points.get(row.id, row.projected_points)

    by_position: dict[str, list[ProjectionSnapshotRow]] = defaultdict(list)
    for row in rows:
        by_position[_normalize_position(row.position)].append(row)
    for position_rows in by_position.values():
        position_rows.sort(key=lambda row: (-projected(row), row.athlete_id))

    slot_counts = Counter(slot.strip().upper() for slot in roster_slots)
    consumed: set[int] = set()
    flexible_slots: list[tuple[set[str], int]] = []
    for slot, count in slot_counts.items():
        positions = _slot_positions(slot)
        if not positions:
            continue
        if len(positions) == 1 and slot not in FLEX_SLOT_POSITIONS:
            position = next(iter(positions))
            consumed.update(row.id for row in by_position.get(position, [])[: count * team_count])
        else:
            flexible_slots.append((positions, count * team_count))

    for positions, demand in sorted(flexible_slots, key=lambda item: len(item[0])):
        pool = sorted(
            (
                row
                for row in rows
                if _normalize_position(row.position) in positions and row.id not in consumed
            ),
            key=lambda row: (-projected(row), row.athlete_id),
        )
        consumed.update(row.id for row in pool[:demand])

    baselines: dict[str, float] = {}
    for position, position_rows in by_position.items():
        replacement = next((row for row in position_rows if row.id not in consumed), None)
        baselines[position] = projected(replacement) if replacement else 0.0
    return baselines


def _normalize(values: list[float], *, window_size: int | None = None) -> list[float]:
    if not values:
        return []
    comparison_values = values
    if window_size is not None:
        comparison_values = sorted(values, reverse=True)[:window_size]
    low = min(comparison_values)
    high = max(comparison_values)
    if high == low:
        return [0.5] * len(values)
    return [max(0.0, min(1.0, (value - low) / (high - low))) for value in values]


def _weighted_score(
    *,
    normalized_vor: float,
    roster_need: float,
    depth_need: float,
    tier_urgency: float,
    risk_adjustment: float,
    market_value: float,
) -> float:
    return 100 * (
        SCORE_WEIGHTS["normalized_vor"] * normalized_vor
        + SCORE_WEIGHTS["roster_need"] * roster_need
        + SCORE_WEIGHTS["depth_need"] * depth_need
        + SCORE_WEIGHTS["tier_urgency"] * tier_urgency
        + SCORE_WEIGHTS["risk_adjustment"] * risk_adjustment
        + SCORE_WEIGHTS["market_value"] * market_value
    )


def _range_model(row: ProjectionSnapshotRow) -> dict[str, object] | None:
    try:
        payload = json.loads(row.raw_stats_json or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("kind") != "projection_range_model":
        return None
    return payload


def _vor_explanation(vor: float, tier_cliff: bool, vor_drop: float) -> str:
    if vor == 0:
        summary = "At league-scored replacement value"
    else:
        direction = "above" if vor > 0 else "below"
        summary = f"{abs(vor):.1f} league-scored points {direction} replacement"
    return summary + (f" with a {vor_drop:.1f}-point tier cliff" if tier_cliff else "")


def _bye_congestion(
    bye_week: int | None,
    position: str,
    roster_slots: list[str],
    owner_bye_counts: Counter[int],
    owner_position_bye_counts: Counter[tuple[str, int]],
) -> tuple[float, str | None]:
    """A bounded tie-breaker, not a veto on stronger players or required starters."""
    if bye_week is None:
        return 0.0, None
    position = _normalize_position(position)
    same_bye = owner_bye_counts[bye_week]
    same_position = owner_position_bye_counts[position, bye_week]
    direct_slots = sum(_slot_positions(slot) == {position} for slot in roster_slots)
    coverage_overlap = same_position >= max(1, direct_slots)
    penalty = min(
        MAX_BYE_CONGESTION_PENALTY,
        1.5 * max(0, same_bye - 1) + (3.0 if coverage_overlap else 0.0),
    )
    if penalty == 0:
        return 0.0, None
    noun = "player already shares" if same_bye == 1 else "players already share"
    warning = f"Week {bye_week}: {same_bye} rostered {noun} this bye"
    if coverage_overlap:
        warning += f", including {same_position} {position}"
    return penalty, f"{warning} (-{penalty:.1f} decision points)"


def _range_tradeoff(
    candidate: CandidateInput, replacement: float, model: dict[str, object] | None
) -> str:
    if model is None:
        return f"Downside input {candidate.row.risk:.0%}; modeled range is unavailable"
    factors = model.get("risk_factors")
    factor = str(factors[0]) if isinstance(factors, list) and factors else None
    signals = model.get("confidence_signals")
    signal = str(signals[0]) if isinstance(signals, list) and signals else None
    floor_delta = candidate.floor - replacement
    range_label = f"P20-P80 {candidate.floor:.1f}-{candidate.ceiling:.1f}"
    downside = (
        f"{candidate.row.risk:.0%} estimated chance of finishing at least 20% below "
        "Yahoo's projection"
    )
    if floor_delta >= 0:
        summary = (
            f"{range_label}; the floor stays {floor_delta:.1f} points above replacement. "
            f"{downside}."
        )
    else:
        summary = (
            f"{range_label}; the floor falls {abs(floor_delta):.1f} points below replacement. "
            f"{downside}."
        )
    if factor:
        return f"{summary} Main uncertainty: {factor}."
    if signal:
        return f"{summary} Confidence signal: {signal}."
    return summary


def _positional_run(
    picks: list[ReducedPick], position_by_player: dict[int, str], team_count: int
) -> str | None:
    window = max(6, (team_count + 1) // 2)
    if len(picks) < window:
        return None
    recent = [position_by_player.get(pick.player_id) for pick in picks[-window:]]
    recent_counts = Counter(value for value in recent if value)
    if not recent_counts:
        return None
    position, count = recent_counts.most_common(1)[0]
    if count < 3:
        return None
    previous = [
        position_by_player.get(pick.player_id) for pick in picks[-window - team_count : -window]
    ]
    previous_share = previous.count(position) / max(1, len(previous))
    recent_share = count / window
    if recent_share >= 2 * previous_share:
        return f"{position} run: {count} of the last {window} picks"
    return None


def score_candidates(
    db: Session,
    session: DraftSession,
    picks: list[ReducedPick],
    *,
    bye_weeks: dict[str, int] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]], str | None]:
    if session.projection_snapshot_id is None:
        return [], [], None
    snapshot = db.get(ProjectionSnapshot, session.projection_snapshot_id)
    if snapshot is None or snapshot.status != "ready":
        return [], [], None
    rows = list(
        db.scalars(
            select(ProjectionSnapshotRow)
            .options(joinedload(ProjectionSnapshotRow.league_player))
            .where(ProjectionSnapshotRow.snapshot_id == snapshot.id)
            .order_by(ProjectionSnapshotRow.projected_points.desc())
        )
    )
    drafted_ids = {pick.player_id for pick in picks}
    available = [
        row for row in rows if row.league_player_id and row.league_player_id not in drafted_ids
    ]
    if not available:
        return [], [], None

    roster_slots = json.loads(session.roster_slots_snapshot_json or "[]")
    roster_slots = [str(slot).upper() for slot in roster_slots]
    scoring = json.loads(session.scoring_snapshot_json or "{}")
    point_details: dict[int, tuple[float, str, dict[str, float], float, float]] = {}
    for row in rows:
        points, source, breakdown = effective_projection_points(
            row.projected_points, row.raw_stats_json, scoring
        )
        if source == "source_points_only" and snapshot.source.lower().startswith("yahoo"):
            source = "league_scored_source_points"
        floor, ceiling = scale_projection_range(
            row.projected_points, points, row.floor, row.ceiling
        )
        point_details[row.id] = (points, source, breakdown, floor, ceiling)
    points_by_row = {row_id: details[0] for row_id, details in point_details.items()}
    baselines = _replacement_baselines(rows, roster_slots, session.team_count, points_by_row)
    owner_pick_ids = [pick.player_id for pick in picks if pick.team_slot == session.owner_team_slot]
    position_by_player = {
        row.league_player_id: _normalize_position(row.position)
        for row in rows
        if row.league_player_id
    }
    row_by_player = {
        int(row.league_player_id): row for row in rows if row.league_player_id is not None
    }
    owner_lineup = [
        LineupPlayerProjection(
            positions=_row_positions(row_by_player[player_id]),
            projected_points=points_by_row[row_by_player[player_id].id],
        )
        for player_id in owner_pick_ids
        if player_id in row_by_player
    ]
    owner_position_counts = Counter(
        position_by_player[player_id]
        for player_id in owner_pick_ids
        if player_id in position_by_player
    )
    lineup_context = _build_lineup_context(owner_lineup, roster_slots)
    availability = availability_guidance(
        db,
        session,
        picks,
        player_ids=[int(row.league_player_id) for row in available],
    )
    ranks = market_ranks(db, session)
    market_values = normalized_market_values(
        [int(row.league_player_id) for row in available if row.league_player_id], ranks
    )
    if bye_weeks is None:
        league = db.get(League, session.league_id)
        bye_weeks = team_bye_weeks(db, league.season) if league else {}
    owner_bye_counts: Counter[int] = Counter()
    owner_position_bye_counts: Counter[tuple[str, int]] = Counter()
    for player_id in owner_pick_ids:
        row = row_by_player.get(player_id)
        if row is None or row.league_player is None:
            continue
        bye = player_bye_week(row.league_player.pro_team, bye_weeks)
        if bye is not None:
            owner_bye_counts[bye] += 1
            owner_position_bye_counts[_normalize_position(row.position), bye] += 1

    vors = {
        row.id: points_by_row[row.id] - baselines.get(_normalize_position(row.position), 0.0)
        for row in available
    }
    by_position: dict[str, list[ProjectionSnapshotRow]] = defaultdict(list)
    for row in available:
        by_position[_normalize_position(row.position)].append(row)
    for position_rows in by_position.values():
        position_rows.sort(key=lambda row: (-vors[row.id], row.athlete_id))

    inputs: list[CandidateInput] = []
    for row in available:
        position = _normalize_position(row.position)
        peers = by_position[position]
        index = peers.index(row)
        next_vor = vors[peers[index + 1].id] if index + 1 < len(peers) else 0.0
        vor_drop = vors[row.id] - next_vor
        cliff_threshold = max(3.0, abs(vors[row.id]) * 0.1)
        tier_urgency = max(0.0, min(1.0, vor_drop / cliff_threshold))
        lineup_utility = _marginal_lineup_utility(
            LineupPlayerProjection(
                positions=_row_positions(row),
                projected_points=point_details[row.id][0],
            ),
            lineup_context,
        )
        position_saturated = _position_is_saturated(position, owner_position_counts, roster_slots)
        if position_saturated and (
            position in {"QB", "TE", "K", "DEF"} or lineup_utility.roster_need == 0.0
        ):
            continue
        depth_need = (
            0.0
            if lineup_utility.roster_need > 0.0
            else _bench_depth_need(position, owner_position_counts, roster_slots)
        )
        inputs.append(
            CandidateInput(
                row=row,
                projected_points=point_details[row.id][0],
                scoring_source=point_details[row.id][1],
                scoring_breakdown=point_details[row.id][2],
                floor=point_details[row.id][3],
                ceiling=point_details[row.id][4],
                vor=vors[row.id],
                roster_need=lineup_utility.roster_need,
                depth_need=depth_need,
                lineup_delta=lineup_utility.projected_delta,
                roster_impact=lineup_utility.impact,
                tier_urgency=tier_urgency,
                risk_adjustment=1.0 - max(0.0, min(1.0, row.risk)),
                tier_cliff=vor_drop >= cliff_threshold,
                vor_drop=vor_drop,
                market_rank=ranks.get(int(row.league_player_id or 0)),
                market_value=market_values.get(int(row.league_player_id or 0), 0.5),
            )
        )

    normalized_vor = _normalize(
        [candidate.vor for candidate in inputs],
        window_size=VOR_NORMALIZATION_WINDOW,
    )
    evidence = attributed_evidence(db, {candidate.row.athlete_id for candidate in inputs})
    scored: list[dict[str, object]] = []
    for candidate, normalized in zip(inputs, normalized_vor, strict=True):
        score = _weighted_score(
            normalized_vor=normalized,
            roster_need=candidate.roster_need,
            depth_need=candidate.depth_need,
            tier_urgency=candidate.tier_urgency,
            risk_adjustment=candidate.risk_adjustment,
            market_value=candidate.market_value,
        )
        player = candidate.row.league_player
        if player is None:
            continue
        why = _vor_explanation(candidate.vor, candidate.tier_cliff, candidate.vor_drop)
        model = _range_model(candidate.row)
        position = _normalize_position(candidate.row.position)
        replacement = baselines.get(position, 0.0)
        bye = player_bye_week(player.pro_team, bye_weeks)
        bye_penalty, bye_warning = _bye_congestion(
            bye, position, roster_slots, owner_bye_counts, owner_position_bye_counts
        )
        score = max(0.0, score - bye_penalty)
        scored.append(
            {
                "player_id": player.id,
                "athlete_id": candidate.row.athlete_id,
                "name": player.name,
                "pro_team": player.pro_team,
                "bye_week": bye,
                "position": position,
                "projected_points": round(candidate.projected_points, 2),
                "floor": round(candidate.floor, 2),
                "ceiling": round(candidate.ceiling, 2),
                "risk": round(candidate.row.risk, 4),
                "scoring_source": candidate.scoring_source,
                "scoring_breakdown": candidate.scoring_breakdown,
                "range_model": model,
                "score": round(score, 2),
                "vor": round(candidate.vor, 2),
                "tier_cliff": candidate.tier_cliff,
                "vor_drop": round(candidate.vor_drop, 2),
                "why_now": why,
                "roster_impact": candidate.roster_impact
                + (f" · {bye_warning}" if bye_warning else ""),
                "tradeoff": _range_tradeoff(candidate, replacement, model),
                "evidence": evidence.get(candidate.row.athlete_id, []),
                "next_turn": availability[player.id],
                "components": {
                    "normalized_vor": round(normalized, 4),
                    "roster_need": candidate.roster_need,
                    "depth_need": candidate.depth_need,
                    "lineup_delta": candidate.lineup_delta,
                    "tier_urgency": round(candidate.tier_urgency, 4),
                    "risk_adjustment": round(candidate.risk_adjustment, 4),
                    "market_value": round(candidate.market_value, 4),
                    "market_rank": candidate.market_rank,
                    "bye_congestion_penalty": bye_penalty,
                    "same_bye_teammates": owner_bye_counts[bye] if bye else 0,
                    "weights": SCORE_WEIGHTS,
                },
            }
        )
    scored.sort(key=lambda item: (-float(item["score"]), int(item["athlete_id"])))
    run = _positional_run(picks, position_by_player, session.team_count)
    return scored[:3], scored[3:], run


def publish_recommendation_snapshot(
    db: Session, session: DraftSession, picks: list[ReducedPick]
) -> DraftRecommendationSnapshot | None:
    if session.projection_snapshot_id is None or session.status not in {"LIVE", "PAUSED"}:
        return None
    league = db.get(League, session.league_id)
    bye_weeks = team_bye_weeks(db, league.season) if league else {}
    input_hash = recommendation_input_hash(session, picks, bye_weeks)
    existing = db.scalar(
        select(DraftRecommendationSnapshot).where(
            DraftRecommendationSnapshot.session_id == session.id,
            DraftRecommendationSnapshot.session_sequence == session.current_sequence,
            DraftRecommendationSnapshot.input_hash == input_hash,
        )
    )
    if existing is not None:
        return existing
    candidates, alternatives, run = score_candidates(db, session, picks, bye_weeks=bye_weeks)
    forecast_statuses = {
        candidate["next_turn"]["status"] for candidate in candidates + alternatives
    }
    forecast_status = next(
        (status for status in ("calibrated", "ordinal", "stale") if status in forecast_statuses),
        "unavailable",
    )
    snapshot = DraftRecommendationSnapshot(
        session_id=session.id,
        session_sequence=session.current_sequence,
        algorithm_version=ALGORITHM_VERSION,
        config_version=session.config_version,
        input_hash=input_hash,
        projection_snapshot_id=session.projection_snapshot_id,
        ranking_snapshot_id=session.ranking_snapshot_id,
        status="ready" if candidates else "unavailable",
        candidates_json=json.dumps(candidates),
        alternatives_json=json.dumps(alternatives),
        forecast_status=forecast_status,
        scenario_summaries_json="[]",
        freshness_json=json.dumps(
            {
                "projection_snapshot_id": session.projection_snapshot_id,
                "positional_run": run,
                "bye_weeks": bye_weeks,
                "league_scoring": scoring_context(
                    json.loads(session.scoring_snapshot_json or "{}")
                ),
            }
        ),
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def recommendation_payload(
    snapshot: DraftRecommendationSnapshot | None,
    session: DraftSession,
    picks: list[ReducedPick],
) -> dict[str, object]:
    total = session.team_count * session.round_count
    current_overall = max((pick.overall_pick for pick in picks), default=0)
    return {
        "snapshot_id": snapshot.id if snapshot else None,
        "status": snapshot.status if snapshot else "unavailable",
        "session_sequence": session.current_sequence,
        "algorithm_version": snapshot.algorithm_version if snapshot else ALGORITHM_VERSION,
        "candidates": json.loads(snapshot.candidates_json) if snapshot else [],
        "alternatives": json.loads(snapshot.alternatives_json) if snapshot else [],
        "forecast_status": snapshot.forecast_status if snapshot else "unavailable",
        "next_owner_pick": next_owner_pick(
            current_overall, session.team_count, session.owner_team_slot, total
        ),
        "freshness": json.loads(snapshot.freshness_json) if snapshot else {},
    }
