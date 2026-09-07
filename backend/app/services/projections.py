from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

DEFAULT_SCORING: dict[str, float] = {
    "passing_yards": 0.04,
    "passing_tds": 4.0,
    "interceptions": -2.0,
    "rushing_yards": 0.1,
    "rushing_tds": 6.0,
    "receptions": 0.0,
    "receiving_yards": 0.1,
    "receiving_tds": 6.0,
    "fumbles_lost": -2.0,
    "two_point_conversions": 2.0,
}


ALIASES: dict[str, str] = {
    "passing yard": "passing_yards",
    "passing yards": "passing_yards",
    "pass_yd": "passing_yards",
    "pass yd": "passing_yards",
    "pass yds": "passing_yards",
    "passing td": "passing_tds",
    "passing touchdown": "passing_tds",
    "passing touchdowns": "passing_tds",
    "pass_td": "passing_tds",
    "pass td": "passing_tds",
    "pass tds": "passing_tds",
    "interception": "interceptions",
    "pass_int": "interceptions",
    "pass int": "interceptions",
    "rushing yard": "rushing_yards",
    "rushing yards": "rushing_yards",
    "rush_yd": "rushing_yards",
    "rush yd": "rushing_yards",
    "rush yds": "rushing_yards",
    "rushing td": "rushing_tds",
    "rushing touchdown": "rushing_tds",
    "rushing touchdowns": "rushing_tds",
    "rush_td": "rushing_tds",
    "rush td": "rushing_tds",
    "rush tds": "rushing_tds",
    "reception": "receptions",
    "rec": "receptions",
    "receiving yard": "receiving_yards",
    "receiving yards": "receiving_yards",
    "rec_yd": "receiving_yards",
    "rec yd": "receiving_yards",
    "rec yds": "receiving_yards",
    "receiving td": "receiving_tds",
    "receiving touchdown": "receiving_tds",
    "receiving touchdowns": "receiving_tds",
    "rec_td": "receiving_tds",
    "rec td": "receiving_tds",
    "rec tds": "receiving_tds",
    "fumble lost": "fumbles_lost",
    "fum_lost": "fumbles_lost",
    "two point conversion": "two_point_conversions",
    "two point conversions": "two_point_conversions",
    "2 point conversion": "two_point_conversions",
    "2 point conversions": "two_point_conversions",
    "two_pt": "two_point_conversions",
}


def canonical_stat_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()
    normalized = re.sub(r"\s+yahoo\s+default$", "", normalized).strip()
    return ALIASES.get(normalized, normalized.replace(" ", "_"))


def normalize_scoring(scoring: Mapping[str, float]) -> dict[str, float]:
    normalized = DEFAULT_SCORING.copy()
    for key, value in scoring.items():
        normalized[canonical_stat_name(key)] = float(value)
    return normalized


def score_projection_breakdown(
    raw_stats: Mapping[str, float], scoring: Mapping[str, float]
) -> dict[str, float]:
    modifiers = normalize_scoring(scoring)
    breakdown: dict[str, float] = {}
    for raw_name, value in raw_stats.items():
        name = canonical_stat_name(raw_name)
        if name not in modifiers:
            continue
        breakdown[name] = round(float(value) * modifiers[name], 3)
    return breakdown


def score_projection(raw_stats: Mapping[str, float], scoring: Mapping[str, float]) -> float:
    return round(sum(score_projection_breakdown(raw_stats, scoring).values()), 3)


def projection_raw_stats(value: str | Mapping[str, Any] | None) -> dict[str, float]:
    payload: Any = value
    if isinstance(value, str):
        try:
            payload = json.loads(value or "{}")
        except json.JSONDecodeError:
            return {}
    if not isinstance(payload, Mapping):
        return {}
    if isinstance(payload.get("projection_stats"), Mapping):
        payload = payload["projection_stats"]
    elif payload.get("kind"):
        # Historical range-model metadata is not a raw statistical projection.
        return {}
    stats: dict[str, float] = {}
    for key, raw_value in payload.items():
        try:
            stats[canonical_stat_name(str(key))] = float(raw_value)
        except (TypeError, ValueError):
            continue
    return stats


def effective_projection_points(
    source_points: float,
    raw_stats: str | Mapping[str, Any] | None,
    scoring: Mapping[str, float],
) -> tuple[float, str, dict[str, float]]:
    projection_stats = projection_raw_stats(raw_stats)
    if not projection_stats:
        return round(float(source_points), 3), "source_points_only", {}
    breakdown = score_projection_breakdown(projection_stats, scoring)
    if not breakdown:
        return round(float(source_points), 3), "source_points_only", {}
    return round(sum(breakdown.values()), 3), "league_rules_recomputed", breakdown


def scale_projection_range(
    source_points: float, effective_points: float, floor: float, ceiling: float
) -> tuple[float, float]:
    if source_points <= 0 or effective_points == source_points:
        return round(float(floor), 3), round(float(ceiling), 3)
    scale = effective_points / source_points
    return round(float(floor) * scale, 3), round(float(ceiling) * scale, 3)


def scoring_context(scoring: Mapping[str, float]) -> dict[str, object]:
    configured = {canonical_stat_name(key): float(value) for key, value in scoring.items()}
    effective = normalize_scoring(configured)
    yardage_rates: dict[str, dict[str, float]] = {}
    for stat, points_per_yard in effective.items():
        if not stat.endswith("_yards") or points_per_yard <= 0:
            continue
        yardage_rates[stat] = {
            "points_per_yard": round(points_per_yard, 6),
            "yards_per_point": round(1 / points_per_yard, 3),
        }
    return {
        "configured": configured,
        "effective": effective,
        "yardage_rates": yardage_rates,
    }


def calibrated_interval(
    mean: float, position: str, history_games: int
) -> tuple[float, float, float]:
    base_sigma = {"QB": 6.0, "RB": 5.2, "WR": 5.8, "TE": 4.3, "K": 3.5, "DEF": 4.5}.get(
        position.upper(), 6.0
    )
    history_factor = 1.35 if history_games < 4 else 1.15 if history_games < 12 else 1.0
    sigma = base_sigma * history_factor
    return round(max(0.0, mean - 1.28 * sigma), 2), round(mean + 1.28 * sigma, 2), round(sigma, 2)
