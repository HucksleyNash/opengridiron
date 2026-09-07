from __future__ import annotations

import csv
import io
import json

import httpx
import pytest
from app.db import SessionLocal
from app.models import League, Player
from app.services import yahoo_weekly
from app.services.decision import evaluate_trade, rank_waivers
from app.services.decision_freshness import recommendation_gate, withhold_decisions
from app.services.defense_forecast import defense_points, parse_defense_stats
from app.services.player_availability import can_start, conditional_status
from app.services.weekly_forecast import (
    build_weekly_report,
    forecast_players,
    match_identities,
    team_decisions,
)
from app.services.weekly_model import benchmark, opponent_index, predict, select_model
from app.services.yahoo_weekly import parse_verified_week
from test_weekly_analysis import NOW, fixture_data


@pytest.mark.parametrize(
    "status", ["INACTIVE", "PUP-R", "NFI", "NFI-R", "IR-Return", "O", "IR", "SUSP"]
)
def test_unavailable_provider_tags_never_produce_upgrades(status):
    league, players, games, stats, identities = fixture_data()
    players[3].status = status
    rows = forecast_players(league, players, games, 1, stats, identities, NOW)
    assert rows[3]["points"] == 0
    assert rows[3]["conditional"] is False
    assert not can_start(status)
    assert not any(
        p["add_id"] == 4 for p in team_decisions(rows, ["RB", "FLEX"], "Mine")["waivers"]
    )


def test_unknown_and_questionable_statuses_remain_conditional():
    assert conditional_status("Q") and conditional_status("medical review")
    assert not can_start("Active", "IR+")


def test_league_comparison_ranks_only_complete_matching_weekly_lineups():
    league, players, games, stats, identities = fixture_data()
    league.roster_slots_json = '["RB"]'
    # Move irrelevant imported slots to bench so the one-slot fixture is legal.
    players[1].current_slot = players[5].current_slot = "BN"
    report = build_weekly_report(league, players, games, 1, "Mine", stats, identities, NOW)
    comparison = report["league_comparison"]
    assert comparison["ranked_teams"] == 2
    assert [(row["team"], row["rank"], row["optimized_points"]) for row in comparison["teams"]] == [
        ("Rival", 1, 100),
        ("Mine", 2, 15),
    ]
    stats.pop("g5")
    report = build_weekly_report(league, players, games, 1, "Mine", stats, identities, NOW)
    comparison = report["league_comparison"]
    assert comparison["ranked_teams"] == 1
    rival = next(row for row in comparison["teams"] if row["team"] == "Rival")
    assert rival["rank"] is None and rival["complete"] is False
    assert rival["optimized_points"] is None
    assert rival["missing_players"] == ["Rival"]
    assert "Partial" in rival["reason"]


def test_weekly_source_fallback_has_no_invented_interval_or_independent_comparison():
    league, players, games, stats, identities = fixture_data()
    source = {
        1: {
            "points": 12,
            "source": "verified fixture",
            "period": "week",
            "week": 1,
            "season": 2026,
            "scoring_basis": "league_rules",
            "scoring": {"rushing_yards": 0.1},
            "received_at": NOW.isoformat(),
        }
    }
    row = forecast_players(league, players, games, 1, {}, {}, NOW, source)[0]
    assert row["points"] == 12 and row["method"] == "source_weekly_fallback"
    assert not row["independent"] and row["floor"] is None and row["ceiling"] is None
    assert row["difference"] is None
    source[1]["week"] = 2
    assert forecast_players(league, players, games, 1, {}, {}, NOW, source)[0]["points"] is None


def player(i, points, *, period="week", week=1, ros_state="provided", ros=0):
    return Player(
        id=i,
        name=f"Player {i}",
        pro_team="CHI",
        position="RB",
        status="Active",
        projected_points=points,
        floor=points,
        ceiling=points,
        ros_value=ros,
        risk=0.5,
        ownership="FA",
        projection_context_json=json.dumps(
            {
                "source": "fixture",
                "period": period,
                "season": 2026,
                "week": week,
                "scoring_basis": "league_rules",
                "scoring": {"rushing_yards": 0.1},
                "ros_value_state": ros_state,
            }
        ),
    )


def test_waivers_compare_roster_gain_and_withhold_faab():
    starter, bench, target = player(1, 10), player(2, 12), player(3, 15)
    starter.rostered_by = bench.rostered_by = "Mine"
    starter.current_slot, bench.current_slot = "RB", "BN"
    result = rank_waivers([starter, bench, target], ["RB"], team_name="Mine", season=2026)
    assert len(result) == 1 and result[0].expected_value == 3
    assert result[0].action == "Consider add"
    assert any("withheld" in line for line in result[0].rationale)
    assert not any("%" in line for line in result[0].rationale)
    target.projection_context_json = "{}"
    assert rank_waivers([starter, target], ["RB"], team_name="Mine")[0].action == "Review"


def test_trade_unknown_ros_is_not_zero_and_differing_weeks_are_not_compared():
    a, b = player(1, 12, ros_state="missing"), player(2, 14, week=2, ros_state="missing")
    result = evaluate_trade([a], [b])
    assert result["rest_of_season_delta"] is None and result["next_week_delta"] is None
    assert result["verdict"] != "Fair range"
    a, b = player(1, 0, period="rest_of_season"), player(2, 0, period="rest_of_season")
    assert evaluate_trade([a], [b])["rest_of_season_delta"] == 0
    assert evaluate_trade([a], [b])["verdict"] == "Fair range"


def test_weekly_projection_parser_requires_selected_week_not_request_assumption():
    assert (
        parse_verified_week(
            '<select name="stat1"><option selected value="S_PW_1">Week 1</option></select>', 1
        )
        == []
    )
    with pytest.raises(ValueError, match="confirm"):
        parse_verified_week(
            '<select name="stat1"><option selected value="S_PS_2026">Season</option></select>', 1
        )


@pytest.mark.asyncio
async def test_weekly_yahoo_snapshot_preserves_season_points_and_uses_cache(client, monkeypatch):
    from test_yahoo_scraper import PLAYER_HTML

    calls = []

    async def fetch(_client, url):
        calls.append(url)
        html = PLAYER_HTML.replace("267.4", "12.5").replace(
            "<body>",
            '<body><select name="stat1"><option selected value="S_PW_3">'
            "Week 3 projections</option></select>",
        )
        return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(yahoo_weekly, "_fetch", fetch)
    monkeypatch.setattr(yahoo_weekly, "_rate_limit_remaining", lambda db: 0)
    monkeypatch.setattr(
        yahoo_weekly,
        "scraper_status",
        lambda db: {"league_urls": ["https://football.fantasysports.yahoo.com/f1/768321"]},
    )
    monkeypatch.setattr(yahoo_weekly, "_secret", lambda *args: "A1=fixture")
    with SessionLocal() as db:
        league = League(
            name="Weekly source isolation",
            season=2026,
            source="yahoo_scrape",
            yahoo_key="scrape:2026:768321",
            scoring_json='{"rushing_yards":0.1}',
        )
        db.add(league)
        db.flush()
        runner = player(93487, 267.4, period="season", week=None)
        runner.league_id = league.id
        runner.source_id = "yahoo.p.5678"
        db.add(runner)
        db.commit()
        sources, metadata = await yahoo_weekly.refresh_weekly_projections(db, league, 3)
        assert sources[runner.id]["points"] == 12.5
        assert sources[runner.id]["week"] == 3
        assert metadata["status"] == "available"
        assert runner.projected_points == 267.4
        assert json.loads(runner.projection_context_json)["period"] == "season"
        assert len(calls) == 6 and all("S_PW_3" in url and "/2026/" in url for url in calls)
        await yahoo_weekly.refresh_weekly_projections(db, league, 3)
        assert len(calls) == 6


def test_freshness_gate_retains_forecasts_but_withholds_roster_changes():
    reasons = recommendation_gate([{"name": "Yahoo league", "status": "stale"}], 2026, 1)
    result = withhold_decisions({"assignments": [1], "waivers": [2], "current_points": 10}, reasons)
    assert result["assignments"] == [] and result["waivers"] == []
    assert result["recommendations_withheld"] and result["current_points"] == 10
    assert (
        recommendation_gate([{"name": "NFL statistics 2026", "status": "unavailable"}], 2026, 1)
        == []
    )
    assert recommendation_gate([{"name": "NFL statistics 2026", "status": "unavailable"}], 2026, 2)


def test_defense_team_history_scores_rules_and_rejects_missing_fields():
    scoring = {"sack": 1, "interception": 2, "fumble_recovery": 2}
    row = {"def_sacks": "3", "def_interceptions": "1", "fumble_recovery_opp": "1"}
    assert defense_points(row, {}, scoring) == 7
    assert defense_points({"def_sacks": "3"}, {}, scoring) is None
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=["season", "week", "team", "opponent_team", *row])
    writer.writeheader()
    for week in range(1, 9):
        writer.writerow({"season": 2025, "week": week, "team": "CHI", "opponent_team": "GB", **row})
    stats = parse_defense_stats({2025: stream.getvalue()}, scoring)
    league, players, games, _, _ = fixture_data()
    league.scoring_json = json.dumps(scoring)
    players[0].position = "DEF"
    identities = match_identities(players, "")
    assert identities[1] == "DEF:CHI"
    forecast = forecast_players(league, players[:1], games, 1, stats, identities, NOW)[0]
    assert forecast["points"] == 7 and forecast["sample_games"] == 8


def test_defense_points_allowed_excludes_opposing_pick_six_and_ambiguous_fumbles():
    opponent = dict.fromkeys(
        [
            "fumble_recovery_tds",
            "special_teams_tds",
            "def_safeties",
            "passing_tds",
            "rushing_tds",
            "fg_made",
            "pat_made",
            "passing_2pt_conversions",
            "rushing_2pt_conversions",
            "def_2pt_made",
        ],
        "0",
    )
    opponent.update({"def_tds": "1", "pat_made": "1"})
    assert defense_points({}, opponent, {"points_allowed_1_6_points": 7}) == 7
    opponent["fumble_recovery_tds"] = "1"
    assert defense_points({}, opponent, {"points_allowed_1_6_points": 7}) is None


def synthetic_stats():
    return {
        f"p{i}": [
            {
                "season": season,
                "week": week,
                "position": "RB",
                "opponent": "GB",
                "team": "CHI",
                "points": float(i + week % 4),
                "usage": 10 + week % 3,
                "has_kicking": False,
            }
            for season in (2023, 2024, 2025)
            for week in range(1, 18)
        ]
        for i in range(24)
    }


def test_benchmark_is_deterministic_time_ordered_and_only_admits_improvements():
    stats = synthetic_stats()
    model = benchmark(stats, 2026)
    assert model["status"] == "evaluated"
    assert model["holdout_season"] == 2025 and model["calibration_season"] == 2024
    assert model["candidate"]["count"] == model["baseline"]["count"]
    assert 0 <= model["baseline"]["interval_coverage"] <= 1
    assert model == benchmark(stats, 2026)
    if model["selected"] != "recency_baseline":
        assert model["candidate"]["mae"] < model["baseline"]["mae"]
    selected = select_model(stats, 2026)
    stats["future"] = [{**stats["p0"][0], "season": 2026, "points": 999999}]
    # Empty future-only identities do not change fitted weights or held-out metrics.
    with_future = select_model(stats, 2026)
    assert selected["candidate"] == with_future["candidate"]
    prior = stats["p0"][-16:]
    estimate = predict(prior, "GB", "RB", 2026, 1, opponent_index(stats), selected)
    assert estimate["interval_kind"] == "historically_calibrated_80_percent"
    assert estimate["floor"] <= estimate["points"] <= estimate["ceiling"]
