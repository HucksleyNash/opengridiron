"""Weekly actuals require observed scoring inputs, never missing-value zeroes."""

from __future__ import annotations

import csv
import io
import math

from .defense_forecast import FIELDS as DEFENSE_FIELDS
from .defense_forecast import defense_points
from .nflverse_draft import _normalized_scoring
from .weekly_forecast import SCORABLE, scoring_rules, team_code

FIELDS = {key: (key,) for key in SCORABLE}
FIELDS.update(
    {
        "interceptions": ("passing_interceptions",),
        "two_point_conversions": (
            "passing_2pt_conversions",
            "rushing_2pt_conversions",
            "receiving_2pt_conversions",
        ),
        "fumbles_lost": ("sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost"),
        "return_touchdowns": ("special_teams_tds",),
        "offensive_fumble_return_td": ("fumble_recovery_tds",),
        "field_goals_0_19_yards": ("fg_made_0_19",),
        "field_goals_20_29_yards": ("fg_made_20_29",),
        "field_goals_30_39_yards": ("fg_made_30_39",),
        "field_goals_40_49_yards": ("fg_made_40_49",),
        "field_goals_50_yards": ("fg_made_50_59", "fg_made_60_"),
        "point_after_attempt_made": ("pat_made",),
    }
)


def finite(value: object) -> float | None:
    try:
        number = float(value) if not isinstance(value, bool) else math.nan
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def score_actual(row: dict, scoring: dict, role: str, opponent: dict | None = None) -> dict:
    if not scoring:
        return {"points": None, "reason": "League scoring has not been configured."}
    if role == "DEF":
        rules = _normalized_scoring(scoring)
        supported = set(DEFENSE_FIELDS) | {"touchdown", "touchdowns", "defensive_touchdowns"}
        # Offensive and kicking rules belong to other roster positions. Unknown rules
        # cannot be silently ignored by defense_points' aggregate calculation.
        offensive, _ = scoring_rules(scoring)
        unknown = [
            key
            for key, value in rules.items()
            if value
            and key not in supported
            and not key.startswith("points_allowed")
            and (key not in offensive or key not in SCORABLE)
        ]
        if unknown:
            return {"points": None, "reason": "Unsupported defense scoring: " + ", ".join(unknown)}
        points = defense_points(row, opponent or {}, scoring)
        return {
            "points": round(points, 2) if points is not None else None,
            "reason": None
            if points is not None
            else "Defense stats do not cover every scoring rule.",
        }
    if role not in {"QB", "RB", "WR", "TE", "K"}:
        return {"points": None, "reason": "Actual scoring is unsupported for this position."}
    rules, unsupported = scoring_rules(scoring)
    if unsupported:
        return {"points": None, "reason": "Unsupported scoring: " + ", ".join(unsupported)}
    total, used, missing = 0.0, False, []
    for rule, weight in rules.items():
        if not weight or (role != "K" and rule.startswith(("field_goals_", "point_after_"))):
            continue
        fields = FIELDS.get(rule)
        if fields is None:
            continue
        if rule == "interceptions" and "passing_interceptions" not in row:
            fields = ("interceptions",)
        if rule == "fumbles_lost" and finite(row.get("fumbles_lost_total")) is not None:
            fields = ("fumbles_lost_total",)
        values = [finite(row.get(field)) for field in fields]
        if any(value is None for value in values):
            missing.extend(
                field for field, value in zip(fields, values, strict=True) if value is None
            )
        else:
            total += weight * sum(values)
            used = True
    if missing:
        return {"points": None, "reason": "Missing scoring stats: " + ", ".join(missing)}
    return {
        "points": round(total, 2) if used else None,
        "reason": None if used else "No supported scoring rules for this position.",
    }


def weekly_actuals(
    contents: dict, season: int, identity: str | None, role: str, scoring: dict
) -> dict:
    if not identity:
        return {}
    defense = role == "DEF"
    rows = list(
        csv.DictReader(io.StringIO(contents.get(f"team:{season}" if defense else season, "")))
    )
    opponents = {(row.get("week"), team_code(row.get("team") or "")): row for row in rows}
    result = {}
    for row in rows:
        try:
            week = int(row.get("week") or 0)
            if (
                int(row.get("season") or 0) != season
                or not 1 <= week <= 18
                or row.get("season_type", "REG").upper() != "REG"
            ):
                continue
        except ValueError:
            continue
        team = team_code(row.get("team") or row.get("recent_team") or "")
        if (f"DEF:{team}" if defense else row.get("player_id") or row.get("gsis_id")) != identity:
            continue
        opposing = team_code(row.get("opponent_team") or "")
        value = {
            "team": team,
            "opponent": opposing,
            **score_actual(
                row,
                scoring,
                role,
                opponents.get((row.get("week"), opposing)),
            ),
        }
        if week in result:
            # Multiple competing rows cannot establish one complete weekly total.
            value = {
                "team": "",
                "opponent": "",
                "points": None,
                "reason": "Conflicting weekly stat rows; actual points are unavailable.",
            }
        result[week] = value
    return result
