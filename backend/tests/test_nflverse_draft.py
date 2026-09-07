from __future__ import annotations

import json
from uuid import uuid4

from app.db import SessionLocal
from app.draft.imports import ensure_legacy_projection_snapshot
from app.models import League, Player
from app.services.nflverse_draft import (
    HistoricalProfile,
    apply_projection_ranges,
    estimate_range,
    parse_historical_profiles,
)


def test_league_scoring_is_applied_to_nflverse_weekly_stats() -> None:
    content = "\n".join(
        [
            (
                "player_id,season,week,season_type,passing_yards,passing_tds,"
                "interceptions,carries,rushing_yards,rushing_tds,receptions,targets,"
                "receiving_yards,receiving_tds,rushing_fumbles_lost"
            ),
            "00-test,2025,1,REG,0,0,0,12,80,1,4,5,30,1,1",
        ]
    )
    profiles = parse_historical_profiles(
        {2025: content},
        {
            "rushing_yards": 0.1,
            "rushing_tds": 6,
            "receptions_yahoo_default": 1,
            "receiving_yards": 0.1,
            "receiving_tds": 6,
            "fumbles_lost": -2,
        },
    )

    assert profiles["00-test"].weekly_points == [25.0]
    assert profiles["00-test"].weekly_usage == [17.0]
    assert profiles["00-test"].season_points == {2025: 25.0}


def test_empirical_range_distinguishes_stable_and_volatile_players() -> None:
    stable = HistoricalProfile(
        gsis_id="stable",
        weekly_points=[20.0] * 34,
        weekly_usage=[20.0] * 34,
        season_points={2024: 340.0, 2025: 340.0},
        season_games={2024: 17, 2025: 17},
    )
    volatile = HistoricalProfile(
        gsis_id="volatile",
        weekly_points=[0.0, 40.0] * 17,
        weekly_usage=[20.0] * 34,
        season_points={2024: 340.0, 2025: 340.0},
        season_games={2024: 17, 2025: 17},
    )

    stable_range = estimate_range(
        projected_points=340,
        position="RB",
        profile=stable,
        years_exp=4,
        draft_number=8,
        status="Active",
        matched=True,
    )
    volatile_range = estimate_range(
        projected_points=340,
        position="RB",
        profile=volatile,
        years_exp=4,
        draft_number=8,
        status="Active",
        matched=True,
    )

    assert stable_range.floor > volatile_range.floor
    assert stable_range.ceiling < volatile_range.ceiling
    assert stable_range.risk < volatile_range.risk
    assert stable_range.metadata["confidence"] == "high"
    assert stable_range.metadata["risk_factors"] == []
    assert stable_range.metadata["confidence_signals"] == ["stable multi-season production sample"]
    assert "volatile weekly production" in volatile_range.metadata["risk_factors"]


def test_nflverse_ranges_are_frozen_into_projection_snapshot(client) -> None:
    suffix = uuid4().hex[:10]
    yahoo_id = str(900000000 + int(suffix[:6], 16) % 90000000)
    gsis_id = f"00-{suffix}"
    roster = "\n".join(
        [
            ("season,team,position,status,full_name,gsis_id,yahoo_id,years_exp,draft_number"),
            f"2026,ATL,RB,ACT,Range Runner,{gsis_id},{yahoo_id},3,12",
        ]
    )
    stats = "\n".join(
        [
            (
                "player_id,season,week,season_type,carries,rushing_yards,rushing_tds,"
                "receptions,targets,receiving_yards,receiving_tds"
            ),
            f"{gsis_id},2025,1,REG,18,90,1,4,5,35,0",
            f"{gsis_id},2025,2,REG,20,110,1,5,6,45,1",
        ]
    )

    with SessionLocal() as db:
        league = League(
            name=f"NFLverse ranges {suffix}",
            season=2026,
            source="yahoo_scrape",
            scoring_json=json.dumps(
                {
                    "rushing_yards": 0.1,
                    "rushing_tds": 6,
                    "receptions": 1,
                    "receiving_yards": 0.1,
                    "receiving_tds": 6,
                }
            ),
        )
        db.add(league)
        db.flush()
        player = Player(
            league_id=league.id,
            source_id=f"461.p.{yahoo_id}",
            name="Range Runner",
            pro_team="ATL",
            position="RB",
            projected_points=300,
        )
        db.add(player)
        db.flush()

        result = apply_projection_ranges(
            db,
            league,
            roster_content=roster,
            stats_contents={2025: stats},
        )
        snapshot = ensure_legacy_projection_snapshot(db, league)
        db.commit()

        assert result["modeled"] == 1
        assert result["matched"] == 1
        assert result["historical"] == 1
        assert player.floor < player.projected_points < player.ceiling
        assert player.risk != 0.5
        assert snapshot is not None
        assert snapshot.source == "yahoo+nflverse"
        assert snapshot.parser_version == "nflverse-range-v1"
        assert snapshot.rows[0].floor == player.floor
        raw = json.loads(snapshot.rows[0].raw_stats_json)
        assert raw["gsis_id"] == gsis_id
        assert raw["risk_definition"].startswith("estimated probability")
