from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from typing import Literal

from ..models import Game, Player, Pool, PoolEntry
from ..schemas import Evidence, PoolRules, Recommendation
from .player_availability import can_start, conditional_status
from .projection_context import context_for

FLEX_POSITIONS: dict[str, set[str]] = {
    "FLEX": {"RB", "WR", "TE"},
    "W/R/T": {"RB", "WR", "TE"},
    "W/R": {"RB", "WR"},
    "W/T": {"WR", "TE"},
    "SUPERFLEX": {"QB", "RB", "WR", "TE"},
    "Q/W/R/T": {"QB", "RB", "WR", "TE"},
    "OP": {"QB", "RB", "WR", "TE"},
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def eligible(position: str, slot: str) -> bool:
    position = position.upper()
    slot = slot.upper()
    if slot in {"BN", "BENCH", "IR", "IR+", "NA"}:
        return False
    if slot in FLEX_POSITIONS:
        return position in FLEX_POSITIONS[slot]
    if slot in {"D/ST", "DST"}:
        return position in {"DEF", "D/ST", "DST"}
    return position == slot


def player_score(player: Player, mode: Literal["floor", "balanced", "ceiling"]) -> float:
    if mode == "floor":
        return player.floor
    if mode == "ceiling":
        return player.ceiling
    return player.projected_points


@dataclass
class LineupResult:
    assignments: list[tuple[str, Player, float]]
    projected_total: float
    current_total: float
    unfilled_slots: list[str]


def optimize_lineup(
    players: list[Player],
    roster_slots: list[str],
    mode: Literal["floor", "balanced", "ceiling"] = "balanced",
) -> LineupResult:
    active_slots = [
        slot.upper()
        for slot in roster_slots
        if slot.upper() not in {"BN", "BENCH", "IR", "IR+", "NA"}
    ]
    candidates = [player for player in players if can_start(player.status, player.current_slot)]

    @cache
    def solve(slot_index: int, used_mask: int) -> tuple[float, tuple[int, ...]]:
        if slot_index >= len(active_slots):
            return 0.0, ()
        slot = active_slots[slot_index]
        best_score, best_path = solve(slot_index + 1, used_mask)
        best_path = (-1,) + best_path
        for index, player in enumerate(candidates):
            if used_mask & (1 << index) or not eligible(player.position, slot):
                continue
            remainder, path = solve(slot_index + 1, used_mask | (1 << index))
            total = player_score(player, mode) + remainder
            if total > best_score:
                best_score = total
                best_path = (index,) + path
        return best_score, best_path

    total, path = solve(0, 0)
    assignments: list[tuple[str, Player, float]] = []
    unfilled: list[str] = []
    for slot, player_index in zip(active_slots, path, strict=True):
        if player_index < 0:
            unfilled.append(slot)
        else:
            player = candidates[player_index]
            assignments.append((slot, player, player_score(player, mode)))

    current_total = sum(
        player_score(player, mode)
        for player in candidates
        if player.current_slot and player.current_slot.upper() not in {"BN", "BENCH", "IR", "NA"}
    )
    return LineupResult(assignments, round(total, 2), round(current_total, 2), unfilled)


def projection_key(player: Player) -> tuple | None:
    """Only explicitly identified periods and scoring can support comparisons."""
    context = context_for(player)
    if context.period == "unknown" or context.season is None:
        return None
    if context.scoring is None or context.scoring_basis != "league_rules":
        return None
    from .nflverse_draft import _normalized_scoring

    rules = tuple(sorted(_normalized_scoring(context.scoring).items()))
    return context.period, context.season, context.week, rules


def rank_waivers(
    players: Iterable[Player],
    roster_positions: Iterable[str],
    limit: int | None = None,
    *,
    team_name: str | None = None,
    season: int | None = None,
) -> list[Recommendation]:
    players = list(players)
    slots = list(roster_positions)
    roster = [p for p in players if team_name and p.rostered_by == team_name]
    free_agents = [
        p
        for p in players
        if not p.rostered_by
        and (p.ownership or "FA").upper() in {"FA", "W", "WAIVERS"}
        and can_start(p.status, p.current_slot)
    ]
    keys = {projection_key(p) for p in free_agents}
    comparable = len(keys) == 1 and None not in keys
    shared_key = next(iter(keys)) if comparable else None
    if shared_key and season is not None and shared_key[1] != season:
        comparable = False
    roster_comparable = bool(
        comparable
        and roster
        and all(projection_key(p) == shared_key for p in roster)
        and shared_key[0] == "week"
    )
    baseline = optimize_lineup(roster, slots).projected_total if roster_comparable else None
    ranked = []
    for player in free_agents:
        value = float(player.projected_points or 0)
        gain = None
        if baseline is not None:
            gain = round(optimize_lineup([*roster, player], slots).projected_total - baseline, 2)
        ranked.append((gain if gain is not None else value, player, gain))
    # Unknown/mixed periods are a review list. Numeric values never establish an Add verdict.
    ranked.sort(key=lambda item: (-item[0], item[1].id or 0))
    recommendations = []
    for rank, (score, player, gain) in enumerate(ranked[:limit], 1):
        context = context_for(player)
        risks = [player.status] if conditional_status(player.status) else []
        rationale = [
            f"Stored {context.period.replace('_', '-')} projection: {player.projected_points:.1f}.",
            "FAAB bid withheld: remaining budget and comparable winning-bid evidence are required.",
        ]
        if not comparable:
            rationale.insert(0, "Review only: projection periods or scoring are unknown or differ.")
        elif gain is not None:
            rationale.insert(0, f"{team_name}: modeled weekly starting-lineup gain {gain:.2f}.")
            rationale.append("Check game locks and long-term drop cost in the weekly report.")
        elif team_name:
            rationale.insert(0, "Roster gain unavailable: matching weekly roster inputs required.")
        else:
            rationale.insert(0, "Source projection order; select a team for starting-lineup fit.")
        recommendations.append(
            Recommendation(
                rank=rank,
                action="Review"
                if not roster_comparable or not gain or gain <= 0
                else "Consider add",
                subject=f"{player.name} ({player.position}, {player.pro_team})",
                player_id=player.id,
                expected_value=round(score, 2),
                ranking_basis="weekly_lineup_gain" if gain is not None else "source_points",
                confidence=0.0,
                rationale=rationale,
                risks=[*risks, "Projection accuracy and waiver market are not calibrated."],
                evidence=[
                    Evidence(
                        label=f"{context.period.replace('_', '-')} projection",
                        value=f"{player.projected_points:.1f}",
                        source=context.source or "Stored projection",
                    )
                ],
                data_as_of=context.received_at or utcnow(),
            )
        )
    return recommendations


def evaluate_trade(outgoing: list[Player], incoming: list[Player]) -> dict[str, object]:
    players = [*outgoing, *incoming]
    keys = {projection_key(p) for p in players}
    shared = next(iter(keys)) if len(keys) == 1 and None not in keys else None
    ros_supported = bool(
        outgoing
        and incoming
        and shared
        and shared[0] == "rest_of_season"
        and all(context_for(p).ros_value_state == "provided" for p in players)
    )
    weekly_supported = bool(outgoing and incoming and shared and shared[0] == "week")
    outgoing_value = sum(p.ros_value for p in outgoing) if ros_supported else None
    incoming_value = sum(p.ros_value for p in incoming) if ros_supported else None
    delta = incoming_value - outgoing_value if ros_supported else None
    weekly_delta = (
        sum(p.projected_points for p in incoming) - sum(p.projected_points for p in outgoing)
        if weekly_supported
        else None
    )
    risk_delta = (
        sum(p.risk or 0 for p in incoming) / len(incoming)
        - sum(p.risk or 0 for p in outgoing) / len(outgoing)
        if outgoing and incoming
        else None
    )
    verdict = "Insufficient comparable rest-of-season data"
    if delta is not None:
        if delta > max(3, abs(outgoing_value) * 0.08):
            verdict = "Favors incoming side"
        elif delta < -max(3, abs(outgoing_value) * 0.08):
            verdict = "Favors outgoing side"
        else:
            verdict = "Fair range"
    return {
        "verdict": verdict,
        "rest_of_season_delta": round(delta, 2) if delta is not None else None,
        "next_week_delta": round(weekly_delta, 2) if weekly_delta is not None else None,
        "risk_delta": round(risk_delta, 3) if risk_delta is not None else None,
        "outgoing_value": round(outgoing_value, 2) if outgoing_value is not None else None,
        "incoming_value": round(incoming_value, 2) if incoming_value is not None else None,
        "projection_context": context_for(players[0]).model_dump(mode="json") if shared else None,
        "rationale": [
            (
                "Only explicitly supplied, matching rest-of-season periods and "
                "scoring support a verdict."
            ),
            (
                "Missing or legacy-unknown values are unavailable; an explicitly "
                "supplied zero is valid."
            ),
            (
                "Weekly deltas require matching weekly periods and scoring; season "
                "totals are not weekly points."
            ),
            "Check starting-lineup fit, game locks, availability, and long-term roster cost.",
        ],
    }


def no_vig_probability(decimal_home: float, decimal_away: float) -> tuple[float, float]:
    if decimal_home <= 1 or decimal_away <= 1:
        raise ValueError("Decimal odds must be greater than one")
    home_raw, away_raw = 1 / decimal_home, 1 / decimal_away
    total = home_raw + away_raw
    return home_raw / total, away_raw / total


def team_probability(game: Game, team: str, basis: str) -> float:
    team = team.upper()
    if team not in {game.home_team, game.away_team}:
        raise ValueError(f"{team} is not part of game {game.id}")
    home_probability = (
        game.home_cover_probability if basis == "against_spread" else game.home_win_probability
    )
    return home_probability if team == game.home_team else 1 - home_probability


def _team_candidates(game: Game) -> list[str]:
    return [game.away_team, game.home_team]


def _pool_probability(
    game: Game, pool: Pool, rules: PoolRules, now: datetime
) -> tuple[float, str] | None:
    """Use the weekly-card probability contract, with no implicit 50% priors."""
    from .pool_week import _game_locked, _probability, _utc

    if (
        game.season != pool.season
        or game.completed
        or not game.kickoff
        or _game_locked(game, now)
        or not game.away_team
        or not game.home_team
        or game.away_team == game.home_team
    ):
        return None
    if (
        game.source_timestamp is None
        or (now - _utc(game.source_timestamp)).total_seconds() > 96 * 3600
    ):
        return None
    if rules.basis == "against_spread" and (
        game.spread_home is None or not math.isfinite(game.spread_home)
    ):
        return None
    probability, kind = _probability(game, game.home_team, rules)
    if kind not in {"market", "model", "manual"}:
        return None
    if probability is None or not math.isfinite(probability) or not 0 <= probability <= 1:
        return None
    return probability, kind


def survivor_recommendations(
    pool: Pool,
    entry: PoolEntry,
    games: list[Game],
    week: int,
    future_games: list[Game] | None = None,
    *,
    now: datetime | None = None,
) -> list[Recommendation]:
    from .pool_outcomes import entry_outcome
    from .pool_week import _game_locked, _utc

    if pool.pool_type != "survivor" or entry.active is False or not 1 <= week <= 30:
        return []
    rules = PoolRules.model_validate(json.loads(pool.rules_json))
    timestamp = _utc(now or utcnow())
    games = [game for game in games if game.week == week and game.season == pool.season]
    future_games = [g for g in (future_games or []) if g.week > week and g.season == pool.season]
    # Existing pick relationships supply prior final scores even though this API
    # receives only the current and future schedule. Reuse canonical grading.
    result_games = {g.id: g for g in [*games, *future_games]}
    for pick in entry.picks:
        if pick.game is not None:
            result_games[pick.game.id] = pick.game
    if (
        entry_outcome(pool, entry, list(result_games.values()), before_week=week + 1)["status"]
        == "eliminated"
    ):
        return []
    if rules.lock_mode == "week_start" and any(_game_locked(game, timestamp) for game in games):
        return []
    current = [pick for pick in entry.picks if pick.week == week]
    filled_slots = {
        pick.slot for pick in current if pick.slot and 1 <= pick.slot <= rules.picks_per_week
    }
    if len(filled_slots) >= rules.picks_per_week:
        return []
    current_teams = {pick.team for pick in current}
    current_games = {pick.game_id for pick in current}
    prior_uses: dict[str, int] = {}
    for pick in entry.picks:
        if pick.week < week:
            prior_uses[pick.team] = prior_uses.get(pick.team, 0) + 1
    allowed = {team.upper() for team in rules.allowed_teams}
    blocked = {team.upper() for team in rules.blocked_teams}
    future_peak: dict[str, float] = {}
    for game in future_games:
        available = _pool_probability(game, pool, rules, timestamp)
        if available is None:
            continue
        home_probability, _ = available
        for team in _team_candidates(game):
            probability = home_probability if team == game.home_team else 1 - home_probability
            survival = 1 - probability if rules.direction == "loser" else probability
            future_peak[team] = max(future_peak.get(team, 0), survival)

    ranked = []
    for game in games:
        available = _pool_probability(game, pool, rules, timestamp)
        if available is None or game.id in current_games:
            continue
        home_probability, kind = available
        for team in _team_candidates(game):
            if (allowed and team not in allowed) or team in blocked or team in current_teams:
                continue
            if rules.max_team_uses is not None and prior_uses.get(team, 0) >= rules.max_team_uses:
                continue
            probability = home_probability if team == game.home_team else 1 - home_probability
            survival = 1 - probability if rules.direction == "loser" else probability
            score = survival - future_peak.get(team, 0) * rules.future_value_weight
            ranked.append((score, survival, team, game, kind))
    ranked.sort(key=lambda item: (-item[0], item[3].id, item[2]))
    output = []
    for rank, (score, survival, team, game, kind) in enumerate(ranked[:10], 1):
        opponent = game.away_team if team == game.home_team else game.home_team
        data_as_of = _utc(game.source_timestamp)
        output.append(
            Recommendation(
                rank=rank,
                action="Pick",
                subject=f"{team} vs {opponent}",
                expected_value=round(score, 4),
                confidence=round(survival, 4),
                rationale=[
                    f"Mode: {rules.direction.replace('_', ' ')} / {rules.basis.replace('_', ' ')}",
                    f"Estimated survival probability {survival:.1%}; source: {kind}.",
                    f"Future-value penalty {max(0, survival - score):.1%} "
                    "using supported future games.",
                    "Alternatives for open slots; check the weekly card "
                    "for a complete legal allocation.",
                ],
                risks=["Market and availability inputs can change before kickoff"],
                evidence=[
                    Evidence(
                        label="Game probability",
                        value=f"{survival:.1%}",
                        source=game.source,
                        as_of=data_as_of,
                    )
                ],
                data_as_of=data_as_of,
            )
        )
    return output


def confidence_recommendations(
    pool: Pool,
    games: list[Game],
    *,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    from .pool_week import _utc

    if pool.pool_type != "confidence" or not games:
        return []
    rules = PoolRules.model_validate(json.loads(pool.rules_json))
    timestamp = _utc(now or utcnow())
    if len({game.week for game in games}) != 1:
        return []
    choices = []
    allowed = {team.upper() for team in rules.allowed_teams}
    blocked = {team.upper() for team in rules.blocked_teams}
    for game in games:
        available = _pool_probability(game, pool, rules, timestamp)
        # This endpoint has no selected entry or saved weights. A partial card
        # cannot safely be reweighted around started/missing games.
        if available is None:
            return []
        home_probability, kind = available
        candidates = []
        for team in _team_candidates(game):
            if (allowed and team not in allowed) or team in blocked:
                continue
            probability = home_probability if team == game.home_team else 1 - home_probability
            success = 1 - probability if rules.direction == "loser" else probability
            candidates.append((success, team))
        if not candidates:
            return []
        success, team = max(candidates)
        choices.append((success, game, team, kind))
    choices.sort(key=lambda item: (item[0], item[1].id))
    weights = (
        sorted(rules.confidence_weights)
        if rules.confidence_weights
        else list(range(1, len(games) + 1))
    )
    if (
        len(weights) != len(games)
        or len(set(weights)) != len(weights)
        or any(weight <= 0 for weight in weights)
    ):
        return []
    output = []
    for (probability, game, team, kind), weight in zip(choices, weights, strict=True):
        output.append(
            {
                "game_id": game.id,
                "pick": team,
                "opponent": game.away_team if team == game.home_team else game.home_team,
                "probability": round(probability, 4),
                "confidence_weight": weight,
                "expected_points": round(probability * weight, 3),
                "source": game.source,
                "probability_kind": kind,
                "data_as_of": _utc(game.source_timestamp).isoformat(),
            }
        )
    return sorted(output, key=lambda item: item["confidence_weight"], reverse=True)


def spread_to_win_probability(spread_home: float) -> float:
    """Calibrated logistic fallback when a moneyline is unavailable."""
    return 1 / (1 + math.exp(0.18 * spread_home))
