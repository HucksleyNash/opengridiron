"""Deterministic result settlement and standings from final, source-backed scores."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy.orm import Session, selectinload

from ..models import Game, Pool, PoolEntry, PoolPick
from ..schemas import PoolRules


def pick_result(pick: PoolPick, game: Game | None, rules: PoolRules) -> str | None:
    if game is None or not game.completed or game.home_score is None or game.away_score is None:
        return None
    if pick.team not in {game.home_team, game.away_team} or pick.week != game.week:
        return "ungraded"
    margin = game.home_score - game.away_score
    if rules.basis == "against_spread":
        if pick.spread_home is None:
            return "ungraded"  # A later line is not evidence of the accepted line.
        margin += pick.spread_home
    if margin == 0:
        return {"survive": "win", "eliminate": "loss", "push": "push"}[rules.tie_result]
    won = (margin > 0) == (pick.team == game.home_team)
    if rules.direction == "loser":
        won = not won
    return "win" if won else "loss"


def entry_results(
    pool: Pool, entry: PoolEntry, games: list[Game]
) -> list[tuple[PoolPick, str | None]]:
    """Do not award points for malformed legacy cards or moved source games."""
    rules = PoolRules.model_validate(json.loads(pool.rules_json))
    by_id = {game.id: game for game in games}
    game_counts = Counter((p.week, p.game_id) for p in entry.picks)
    team_counts = Counter((p.week, p.team) for p in entry.picks)
    slot_counts = Counter((p.week, p.slot) for p in entry.picks)
    weights = Counter((p.week, p.confidence) for p in entry.picks)
    output = []
    for p in entry.picks:
        game = by_id.get(p.game_id)
        invalid = (
            game is None
            or game.week != p.week
            or (pool.season is not None and game.season != pool.season)
            or p.team not in {game.away_team, game.home_team}
            or bool(rules.allowed_teams and p.team not in rules.allowed_teams)
            or p.team in rules.blocked_teams
            or game_counts[(p.week, p.game_id)] > 1
            or team_counts[(p.week, p.team)] > 1
        )
        if pool.pool_type == "survivor":
            prior_uses = sum(other.team == p.team and other.week < p.week for other in entry.picks)
            invalid = invalid or (
                p.slot is None
                or not 1 <= p.slot <= rules.picks_per_week
                or slot_counts[(p.week, p.slot)] > 1
                or p.confidence is not None
                or bool(rules.max_team_uses and prior_uses >= rules.max_team_uses)
            )
        else:
            allowed_weights = rules.confidence_weights or list(
                range(1, 1 + sum(g.week == p.week for g in games))
            )
            invalid = (
                invalid
                or p.confidence not in allowed_weights
                or weights[(p.week, p.confidence)] > 1
            )
        output.append((p, "ungraded" if invalid else pick_result(p, game, rules)))
    return output


def settle_season(db: Session, season: int) -> int:
    games = {g.id: g for g in db.query(Game).filter(Game.season == season).all()}
    pools = (
        db.query(Pool)
        .options(selectinload(Pool.entries).selectinload(PoolEntry.picks))
        .filter(Pool.season == season)
        .all()
    )
    count = 0
    for pool in pools:
        for entry in pool.entries:
            for pick, result in entry_results(pool, entry, list(games.values())):
                if pick.game_id not in games:
                    continue  # Preserve manually recorded legacy results, without awarding points.
                if pick.result != result:
                    pick.result = result
                    count += 1
    return count


def entry_outcome(pool: Pool, entry: PoolEntry, games: list[Game], before_week: int = 31) -> dict:
    results = [
        {
            "week": p.week,
            "slot": p.slot,
            "team": p.team,
            "game_id": p.game_id,
            "confidence": p.confidence,
            "result": result,
        }
        for p, result in entry_results(pool, entry, games)
        if p.week < before_week
    ]
    eliminated = (
        min((p["week"] for p in results if p["result"] == "loss"), default=None)
        if pool.pool_type == "survivor"
        else None
    )
    return {
        "entry_id": entry.id,
        "name": entry.name,
        "active": entry.active,
        "status": "inactive" if not entry.active else "eliminated" if eliminated else "active",
        "eliminated_week": eliminated,
        "wins": sum(p["result"] == "win" for p in results),
        "losses": sum(p["result"] == "loss" for p in results),
        "pushes": sum(p["result"] == "push" for p in results),
        "pending": sum(p["result"] is None for p in results),
        "ungraded": sum(p["result"] == "ungraded" for p in results),
        "points": sum((p["confidence"] or 1) for p in results if p["result"] == "win"),
        "results": sorted(results, key=lambda p: (p["week"], p["slot"])),
    }


def standings(db: Session, pool: Pool) -> dict:
    games = db.query(Game).filter(Game.season == pool.season).all()
    rows = [entry_outcome(pool, entry, games) for entry in pool.entries]
    rows.sort(key=lambda r: (r["status"] != "active", -r["points"], -r["wins"], r["name"]))
    return {
        "pool_id": pool.id,
        "generated_at": datetime.now(UTC).isoformat(),
        "entries": rows,
        "grading": (
            "Final scores; ATS uses the line saved with each pick. "
            "Legacy ATS picks without a saved line remain ungraded."
        ),
    }
