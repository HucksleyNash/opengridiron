from copy import deepcopy

import pytest
from app.services.weekly_forecast import forecast_players, parse_weekly_stats
from test_weekly_analysis import NOW, fixture_data, stats_csv


def test_history_warning_explains_sample_and_league_scoring():
    league, players, games, stats, identities = fixture_data()
    league.scoring_json = '{"rushing_yards":0.2}'
    stats = parse_weekly_stats({2025: stats_csv({"g1": 5})}, {"rushing_yards": 0.2})
    row = forecast_players(league, players, games, 1, stats, identities, NOW)[0]
    warning = row["warnings"][0]
    assert "10.0 pts/game across 8 games (2025 Wk 1 to 2025 Wk 8)" in warning
    assert "using this league's scoring" in warning
    assert "No 2026 game stats are included" in warning
    assert "check Starter: expected carries, receiving work and goal-line role" in warning
    assert "Preseason" not in warning and "unverified" not in warning
    assert row["points"] == 10 and row["confidence"] == "low"


@pytest.mark.parametrize(
    ("position", "check"),
    [
        ("QB", "the starting QB job and any expected snap limit"),
        ("RB", "expected carries, receiving work and goal-line role"),
        ("WR", "expected targets and route participation"),
        ("TE", "expected targets and route participation"),
        ("K", "the starting kicker job"),
        ("DEF", "defensive starter injuries and roster changes"),
        ("D/ST", "defensive starter injuries and roster changes"),
    ],
)
def test_history_warning_tells_each_position_what_to_check(position, check):
    league, players, games, stats, identities = fixture_data()
    players[0].position = position
    for sample in stats["g1"]:
        sample["has_kicking"] = True
    row = forecast_players(league, players, games, 1, stats, identities, NOW)[0]
    assert any(f"check Starter: {check}" in warning for warning in row["warnings"])


def test_history_warning_does_not_call_missing_in_season_stats_preseason():
    league, players, games, stats, identities = fixture_data()
    games[0].week = 6
    row = forecast_players(league, players, games, 6, stats, identities, NOW)[0]
    assert "No 2026 game stats are included" in row["warnings"][0]
    assert "Preseason" not in row["warnings"][0]


def test_history_warning_uses_only_the_selected_sample_across_seasons():
    league, players, games, stats, identities = fixture_data()
    older = [{**sample, "season": 2024} for sample in stats["g1"]]
    stats["g1"] = older + stats["g1"]
    # Neither an older appearance beyond the 16-game window nor this week's
    # outcome can influence the explanation or forecast.
    stats["g1"].insert(0, {**older[0], "week": 0, "points": 999})
    stats["g1"].append({**stats["g1"][-1], "season": 2026, "week": 1, "points": 999})
    row = forecast_players(league, players, games, 1, stats, identities, NOW)[0]
    assert "5.0 pts/game across 16 games (2024 Wk 1 to 2025 Wk 8)" in row["warnings"][0]
    assert row["points"] == 5


def test_current_season_data_removes_history_only_warning():
    league, players, games, stats, identities = fixture_data()
    games[0].week = 2
    stats["g1"].append({**stats["g1"][-1], "season": 2026, "week": 1})
    row = forecast_players(league, players, games, 2, stats, identities, NOW)[0]
    assert not row["warnings"]


def test_team_change_names_both_teams_without_changing_forecast():
    league, players, games, stats, identities = fixture_data()
    before = forecast_players(league, players, games, 1, stats, identities, NOW)[0]
    stats = deepcopy(stats)
    stats["g1"][-1]["team"] = "BUF"
    row = forecast_players(league, players, games, 1, stats, identities, NOW)[0]
    assert any("Latest game sample is with BUF; current team is CHI" in w for w in row["warnings"])
    assert (row["points"], row["floor"], row["ceiling"]) == (
        before["points"],
        before["floor"],
        before["ceiling"],
    )


@pytest.mark.parametrize("case", ["out", "missing_history", "source_fallback"])
def test_non_historical_forecasts_do_not_receive_the_baseline_warning(case):
    league, players, games, stats, identities = fixture_data()
    sources = None
    if case == "out":
        players[0].status = "Out"
    else:
        stats = {}
        if case == "source_fallback":
            sources = {
                1: {
                    "points": 12,
                    "period": "week",
                    "season": 2026,
                    "week": 1,
                    "scoring": {"rushing_yards": 0.1},
                }
            }
    row = forecast_players(league, players, games, 1, stats, identities, NOW, sources)[0]
    assert not any("Historical baseline" in warning for warning in row["warnings"])
    assert row["points"] == {"out": 0, "missing_history": None, "source_fallback": 12}[case]
