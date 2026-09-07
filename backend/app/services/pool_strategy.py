"""Season allocation with team-use, game, week, lock and saved-pick constraints.

Minimum-cost flow maximizes summed log survival probability for one entry.
Additional entries use a disclosed overlap penalty, a diversification heuristic.
Plans are advisory and never submit or alter a saved card.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..models import Game, Pool, PoolEntry
from ..schemas import PoolRules
from .pool_outcomes import entry_outcome


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _allocate(
    pool: Pool,
    entry: PoolEntry,
    games: list[Game],
    start_week: int,
    exposure: Counter,
    penalty: float,
    now: datetime,
) -> dict:
    rules = PoolRules.model_validate(json.loads(pool.rules_json))
    outcome = entry_outcome(pool, entry, games)
    if outcome["status"] != "active":
        return {"entry_id": entry.id, "name": entry.name, "status": outcome["status"], "picks": []}
    weeks = list(range(start_week, 19))
    if outcome["ungraded"]:
        return {"entry_id": entry.id, "name": entry.name, "status": "needs_repair", "picks": []}
    graph: dict[object, list[list]] = {}

    def edge(u: object, v: object, capacity: int, cost: float) -> list:
        graph.setdefault(u, [])
        graph.setdefault(v, [])
        fwd = [v, len(graph[v]), capacity, cost]
        back = [u, len(graph[u]), 0, -cost]
        graph[u].append(fwd)
        graph[v].append(back)
        return fwd

    by_id = {g.id: g for g in games}
    fixed = [p for p in entry.picks if p.week >= start_week and p.week <= 18]
    used = Counter(p.team for p in entry.picks if p.week < start_week)
    fixed_counts = Counter(p.week for p in fixed)
    fixed_games = {p.game_id for p in fixed}
    output = []
    for p in fixed:
        g = by_id.get(p.game_id)
        if g is None or p.team not in {g.away_team, g.home_team}:
            return {"entry_id": entry.id, "name": entry.name, "status": "needs_repair", "picks": []}
        used[p.team] += 1
        output.append(
            {
                "week": p.week,
                "game_id": p.game_id,
                "team": p.team,
                "probability": p.probability,
                "kind": p.probability_kind or "unknown",
                "saved": True,
            }
        )
    if any(count > rules.picks_per_week for count in fixed_counts.values()) or (
        rules.max_team_uses and any(count > rules.max_team_uses for count in used.values())
    ):
        return {"entry_id": entry.id, "name": entry.name, "status": "needs_repair", "picks": []}
    required = sum(max(0, rules.picks_per_week - fixed_counts[w]) for w in weeks)
    for week in weeks:
        edge(("week", week), "sink", max(0, rules.picks_per_week - fixed_counts[week]), 0)
    teams = {t for g in games for t in (g.away_team, g.home_team)}
    for team in sorted(teams):
        cap = rules.max_team_uses - used[team] if rules.max_team_uses else required
        edge("source", ("team", team), max(0, cap), 0)
    candidates = []
    for g in sorted(games, key=lambda g: (g.week, g.id)):
        if (
            g.week not in weeks
            or g.id in fixed_games
            or g.completed
            or g.locked_at
            or _utc(g.kickoff) <= now
        ):
            continue
        if rules.lock_mode == "week_start" and any(
            _utc(other.kickoff) <= now for other in games if other.week == g.week
        ):
            continue
        if (now - _utc(g.source_timestamp)).total_seconds() > 96 * 3600:
            continue
        kind = (
            g.cover_probability_kind if rules.basis == "against_spread" else g.win_probability_kind
        )
        if kind not in {"market", "manual", "model"} or (
            rules.basis == "against_spread" and g.spread_home is None
        ):
            continue
        hp = g.home_cover_probability if rules.basis == "against_spread" else g.home_win_probability
        edge(("game", g.id), ("week", g.week), 1, 0)
        for team in (g.away_team, g.home_team):
            if (
                rules.allowed_teams and team not in rules.allowed_teams
            ) or team in rules.blocked_teams:
                continue
            p = hp if team == g.home_team else 1 - hp
            if rules.direction == "loser":
                p = 1 - p
            cost = -math.log(max(0.001, min(0.999, p))) + penalty * exposure[(g.week, team)]
            fwd = edge(("team", team), ("game", g.id), 1, cost)
            candidates.append(
                (
                    fwd,
                    {
                        "week": g.week,
                        "game_id": g.id,
                        "team": team,
                        "probability": round(p, 5),
                        "kind": kind,
                        "saved": False,
                    },
                )
            )
    flow = 0
    while flow < required and "source" in graph:
        distance = {node: math.inf for node in graph}
        distance["source"] = 0
        previous = {}
        for _ in range(len(graph) - 1):
            changed = False
            for u, edges in graph.items():
                for index, (v, _, cap, cost) in enumerate(edges):
                    if cap > 0 and distance[u] + cost < distance[v] - 1e-12:
                        distance[v] = distance[u] + cost
                        previous[v] = (u, index)
                        changed = True
            if not changed:
                break
        if "sink" not in previous:
            break
        node = "sink"
        while node != "source":
            u, index = previous[node]
            e = graph[u][index]
            e[2] -= 1
            graph[node][e[1]][2] += 1
            node = u
        flow += 1
    output.extend(item for e, item in candidates if e[2] == 0)
    output.sort(key=lambda item: (item["week"], item["team"]))
    counts = Counter(p["week"] for p in output)
    for p in output:
        exposure[(p["week"], p["team"])] += 1
    return {
        "entry_id": entry.id,
        "name": entry.name,
        "status": "complete" if weeks and flow == required else "partial",
        "picks": output,
        "missing_weeks": [w for w in weeks if counts[w] < rules.picks_per_week],
        "season_survival_probability": round(math.prod(p["probability"] for p in output), 6)
        if weeks and flow == required and all(p["probability"] is not None for p in output)
        else None,
    }


def season_strategy(
    db: Session,
    pool: Pool,
    start_week: int,
    overlap_penalty: float = 0.15,
    now: datetime | None = None,
) -> dict:
    if pool.pool_type != "survivor":
        return {
            "status": "not_applicable",
            "entries": [],
            "limitations": "Confidence weights are optimized on each weekly card.",
        }
    games = db.query(Game).filter(Game.season == pool.season, Game.week <= 18).all()
    exposure: Counter = Counter()
    plans = [
        _allocate(
            pool, entry, games, start_week, exposure, overlap_penalty, now or datetime.now(UTC)
        )
        for entry in sorted(pool.entries, key=lambda e: e.id)
    ]
    return {
        "status": "ready",
        "start_week": start_week,
        "overlap_penalty": overlap_penalty,
        "entries": plans,
        "method": "Constrained minimum-cost flow; maximize sum of log survival probabilities.",
        "limitations": (
            "Assumes independent game outcomes and fixed probabilities. "
            "Multi-entry overlap penalty is a heuristic, not an optimal portfolio "
            "guarantee. Saved picks stay fixed. Missing or stale probabilities "
            "leave a partial plan. Plans do not save picks."
        ),
    }
