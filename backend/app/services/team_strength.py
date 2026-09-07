"""A transparent, walk-forward Elo fallback when a game has no market price.

Parameters are selected using only seasons before the requested season. Predictions
for a week are made before any results from that week update the ratings. The market
remains the primary source; Elo is not advertised as a betting edge.
"""

from __future__ import annotations

import csv
import io
import math
from collections import defaultdict

ALIASES = {"OAK": "LV", "SD": "LAC", "STL": "LA", "JAC": "JAX"}


def team_key(team: str) -> str:
    return ALIASES.get(team, team)


def _number(value: str | None) -> float | None:
    try:
        result = float(value or "")
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def _walk(rows: list[dict], k: float, home_edge: float) -> tuple[dict, list[dict]]:
    ratings: dict[str, float] = defaultdict(lambda: 1500.0)
    groups: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(int(row["season"]), int(row["week"]))].append(row)
    forecasts: dict[tuple[int, int, str, str], float] = {}
    results: list[dict] = []
    previous_season = None
    for (season, week), games in sorted(groups.items()):
        if previous_season is not None and previous_season != season:
            ratings = defaultdict(
                lambda: 1500.0,
                {team: 1500 + (value - 1500) * 0.67 for team, value in ratings.items()},
            )
        previous_season = season
        deltas: dict[str, float] = defaultdict(float)
        for row in games:
            home, away = team_key(row["home_team"]), team_key(row["away_team"])
            edge = 0 if row.get("location") == "Neutral" else home_edge
            p = 1 / (1 + 10 ** (-(ratings[home] - ratings[away] + edge) / 400))
            p = min(0.95, max(0.05, p))
            forecasts[(season, week, row["away_team"], row["home_team"])] = p
            hs, aws = _number(row.get("home_score")), _number(row.get("away_score"))
            if hs is None or aws is None:
                continue
            actual = 1.0 if hs > aws else 0.0 if hs < aws else 0.5
            deltas[home] += k * (actual - p)
            deltas[away] -= k * (actual - p)
            market = None
            hm, am = _number(row.get("home_moneyline")), _number(row.get("away_moneyline"))
            if hm and am:
                hp = 100 / (hm + 100) if hm > 0 else -hm / (-hm + 100)
                ap = 100 / (am + 100) if am > 0 else -am / (-am + 100)
                market = hp / (hp + ap)
            results.append({"season": season, "p": p, "actual": actual, "market": market})
        for team, delta in deltas.items():
            ratings[team] += delta
    return forecasts, results


def schedule_forecasts(content: str, season: int) -> tuple[dict, dict]:
    rows = [
        row
        for row in csv.DictReader(io.StringIO(content))
        if (row.get("game_type") or "").upper() in {"REG", "WC", "DIV", "CON", "SB"}
        and str(row.get("season", "")).isdigit()
        and season - 8 <= int(row["season"]) <= season
        and str(row.get("week", "")).isdigit()
        and row.get("away_team")
        and row.get("home_team")
    ]
    train = [row for row in rows if int(row["season"]) < season - 1]
    candidates = []
    for k in (15.0, 25.0, 35.0):
        for edge in (35.0, 55.0, 75.0):
            _, scores = _walk(train, k, edge)
            evaluation = [s for s in scores if s["season"] >= season - 4]
            if evaluation:
                brier = sum((s["p"] - s["actual"]) ** 2 for s in evaluation) / len(evaluation)
                candidates.append((brier, k, edge))
    if not candidates:
        return {}, {"status": "unavailable", "reason": "No prior-season training results."}
    _, k, edge = min(candidates)
    forecasts, scores = _walk(rows, k, edge)
    holdout = [s for s in scores if s["season"] == season - 1]
    if len(holdout) < 100:
        return {}, {"status": "unavailable", "reason": "Fewer than 100 prior-season holdout games."}
    brier = sum((s["p"] - s["actual"]) ** 2 for s in holdout) / len(holdout)
    baseline = sum((0.5 - s["actual"]) ** 2 for s in holdout) / len(holdout)
    priced = [s for s in holdout if s["market"] is not None]
    benchmark = {
        "model": "elo-weekly-v1",
        "status": "validated" if brier < baseline else "rejected",
        "k": k,
        "home_edge": edge,
        "holdout_season": season - 1,
        "holdout_games": len(holdout),
        "brier": round(brier, 5),
        "coin_flip_brier": round(baseline, 5),
        "market_games": len(priced),
        "market_brier": round(
            sum((s["market"] - s["actual"]) ** 2 for s in priced) / len(priced), 5
        )
        if priced
        else None,
        "model_on_market_games_brier": round(
            sum((s["p"] - s["actual"]) ** 2 for s in priced) / len(priced), 5
        )
        if priced
        else None,
        "limitations": (
            "Win probabilities only. Weekly Elo, no injury or lineup inputs. "
            "Holdout tests a 50% baseline; no claimed market edge."
        ),
    }
    return (forecasts if brier < baseline else {}), benchmark
