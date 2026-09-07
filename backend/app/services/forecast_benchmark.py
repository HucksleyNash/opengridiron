"""Offline benchmark: python -m app.services.forecast_benchmark --help."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .weekly_forecast import parse_weekly_stats
from .weekly_model import select_model


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate exactly the weekly serving model on historical nflverse CSVs. "
            "No network or database access."
        )
    )
    parser.add_argument(
        "--input-dir", type=Path, required=True, help="player-YYYY.csv and optional team-YYYY.csv"
    )
    parser.add_argument(
        "--season",
        type=int,
        required=True,
        help="Forecast season; evaluation uses completed earlier seasons",
    )
    parser.add_argument("--scoring", type=Path, help="JSON mapping of this league's scoring rules")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scoring = (
        json.loads(args.scoring.read_text())
        if args.scoring
        else {
            "passing_yards": 0.04,
            "passing_tds": 4,
            "interceptions": -2,
            "rushing_yards": 0.1,
            "rushing_tds": 6,
            "receptions": 1,
            "receiving_yards": 0.1,
            "receiving_tds": 6,
            "fumbles_lost": -2,
            "two_point_conversions": 2,
            "sack": 1,
            "interception": 2,
            "fumble_recovery": 2,
            "touchdown": 6,
            "safety": 2,
            "block_kick": 2,
            "kickoff_and_punt_return_touchdowns": 6,
            "extra_point_returned": 2,
            "points_allowed_0_points": 10,
            "points_allowed_1_6_points": 7,
            "points_allowed_7_13_points": 4,
            "points_allowed_14_20_points": 1,
            "points_allowed_21_27_points": 0,
            "points_allowed_28_34_points": -1,
            "points_allowed_35_points": -4,
            "field_goals_0_19_yards": 3,
            "field_goals_20_29_yards": 3,
            "field_goals_30_39_yards": 3,
            "field_goals_40_49_yards": 4,
            "field_goals_50_yards": 5,
            "point_after_attempt_made": 1,
        }
    )
    contents, sources = {}, []
    for path in sorted(args.input_dir.glob("*.csv")):
        kind, year = path.stem.split("-")
        if kind not in {"team", "player"} or int(year) >= args.season:
            continue
        content = path.read_text()
        contents[f"team:{year}" if kind == "team" else int(year)] = content
        sources.append(
            {
                "file": path.name,
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "url": f"https://github.com/nflverse/nflverse-data/releases/download/stats_{kind}/stats_{kind}_week_{year}.csv",
            }
        )
    stats = parse_weekly_stats(contents, scoring)
    result = {**select_model(stats, args.season), "scoring": scoring, "sources": sources}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "status",
                    "selected",
                    "training_count",
                    "calibration_count",
                    "holdout_count",
                    "candidate",
                    "baseline",
                )
                if key in result
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
