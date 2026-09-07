"""Conservative league-scored D/ST history from nflverse team statistics.

Team aggregates do not distinguish every offensive/special-team fumble touchdown.
Ambiguous games are omitted instead of assigning a guessed Yahoo points-allowed
bucket. The output reports how many games are actually supported.
"""

from __future__ import annotations

import csv
import io
import math
import re
from collections import defaultdict

from .nflverse_draft import _normalized_scoring

TEAM_STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_week_{season}.csv"
ALIASES = {"JAC": "JAX", "LA": "LAR", "WSH": "WAS", "WFT": "WAS", "OAK": "LV", "SD": "LAC"}
FIELDS = {
    "sack": ("def_sacks",),
    "sacks": ("def_sacks",),
    "interception": ("def_interceptions",),
    "interceptions_defense": ("def_interceptions",),
    "fumble_recovery": ("fumble_recovery_opp",),
    "fumble_recoveries": ("fumble_recovery_opp",),
    "safety": ("def_safeties",),
    "safeties": ("def_safeties",),
    "block_kick": ("def_punt_blocks", "def_pat_blocks", "def_fg_blocks"),
    "blocked_kick": ("def_punt_blocks", "def_pat_blocks", "def_fg_blocks"),
    "blocked_kicks": ("def_punt_blocks", "def_pat_blocks", "def_fg_blocks"),
    "kickoff_and_punt_return_touchdowns": ("special_teams_tds",),
    "extra_point_returned": ("def_2pt_made",),
    "extra_point_return": ("def_2pt_made",),
}


def code(value: str) -> str:
    return ALIASES.get(value.upper(), value.upper())


def number(row: dict, key: str) -> float:
    value = float(row[key])
    if not math.isfinite(value):
        raise ValueError("Nonfinite team stat")
    return value


def defense_points(row: dict, opponent: dict, scoring: dict) -> float | None:
    rules = _normalized_scoring(scoring)
    score, used = 0.0, False
    try:
        for key, weight in rules.items():
            if not weight:
                continue
            value = None
            if key in FIELDS:
                value = sum(number(row, field) for field in FIELDS[key])
                if key == "kickoff_and_punt_return_touchdowns" and value:
                    # Blocked-kick returns are included in the aggregate but scored separately.
                    return None
            elif key in {"touchdown", "touchdowns", "defensive_touchdowns"}:
                fumble_tds = number(row, "fumble_recovery_tds")
                if fumble_tds and number(row, "fumble_recovery_own"):
                    return None
                value = number(row, "def_tds") + fumble_tds
            elif key.startswith("points_allowed"):
                # Yahoo counts offensive TDs, field goals and all conversion points.
                # Exclude ambiguous return/fumble/safety games from this aggregate source.
                if any(
                    number(opponent, field)
                    for field in ("fumble_recovery_tds", "special_teams_tds", "def_safeties")
                ):
                    return None
                allowed = (
                    6 * (number(opponent, "passing_tds") + number(opponent, "rushing_tds"))
                    + 3 * number(opponent, "fg_made")
                    + number(opponent, "pat_made")
                    + 2
                    * (
                        number(opponent, "passing_2pt_conversions")
                        + number(opponent, "rushing_2pt_conversions")
                        + number(opponent, "def_2pt_made")
                    )
                )
                bounds = [int(value) for value in re.findall(r"\d+", key)]
                if not bounds:
                    return None
                low, high = bounds[0], bounds[-1]
                if len(bounds) == 1 and low > 0:
                    high = math.inf
                value = float(low <= allowed <= high)
            elif key.startswith(("yards_allowed", "defensive_")):
                return None
            if value is not None:
                score += weight * value
                used = True
    except (KeyError, ValueError, TypeError):
        return None
    return score if used else None


def parse_defense_stats(contents: dict[int, str], scoring: dict) -> dict[str, list[dict]]:
    result = defaultdict(list)
    for expected_season, content in contents.items():
        rows = list(csv.DictReader(io.StringIO(content)))
        lookup = {
            (row.get("season"), row.get("week"), code(row.get("team", ""))): row for row in rows
        }
        for row in rows:
            try:
                season, week = int(row["season"]), int(row["week"])
                if (
                    season != expected_season
                    or not 1 <= week <= 18
                    or row.get("season_type", "REG") != "REG"
                ):
                    continue
                team, opposing = code(row["team"]), code(row["opponent_team"])
                opponent = lookup.get((row["season"], row["week"], opposing))
                points = defense_points(row, opponent or {}, scoring)
                if points is None:
                    continue
                result[f"DEF:{team}"].append(
                    {
                        "season": season,
                        "week": week,
                        "points": points,
                        "usage": 0.0,
                        "position": "DEF",
                        "team": team,
                        "opponent": opposing,
                        "has_kicking": False,
                        "source": "nflverse_team_stats",
                    }
                )
            except (KeyError, ValueError, TypeError):
                continue
    return {
        key: sorted(rows, key=lambda row: (row["season"], row["week"]))
        for key, rows in result.items()
    }
