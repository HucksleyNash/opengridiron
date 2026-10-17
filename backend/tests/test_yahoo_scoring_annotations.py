from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from app.models import Game, League, Player
from app.services.nflverse_draft import parse_historical_profiles
from app.services.player_points_scoring import score_actual
from app.services.projections import normalize_scoring, score_projection, scoring_context
from app.services.weekly_forecast import forecast_players, parse_weekly_stats, scoring_rules
from app.services.yahoo_scraper import parse_settings_page

# The configured values deliberately differ from Yahoo's informational defaults.
SCORING_ROWS = [
    ("Passing Yards", "25 yards per point", "50 yards per point", "passing_yards", 0.02),
    ("Passing Touchdowns", "4", "6", "passing_tds", 6.0),
    ("Interceptions", "-1", "-2", "interceptions", -2.0),
    ("Receptions", "0.5", "1", "receptions", 1.0),
    ("Kickoff Return Yards", "0", "10 yards per point", "kickoff_return_yards", 0.1),
    ("Punt Return Yards", "0", "10 yards per point", "punt_return_yards", 0.1),
]
EXPECTED = {key: value for _, _, _, key, value in SCORING_ROWS}
SAVED_SCORING = {
    "passing_yards_yahoo_default_25_yards_per_point": 0.02,
    "passing_touchdowns_yahoo_default_4": 6.0,
    "interceptions_yahoo_default_1": -2.0,
    "receptions_yahoo_default_0_5": 1.0,
    "kickoff_return_yards_yahoo_default_0": 0.1,
    "punt_return_yards_yahoo_default_0": 0.1,
}
STATS = (
    "player_id,season,week,team,position,passing_yards,passing_tds,passing_interceptions,"
    "receptions,kickoff_return_yards,punt_return_yards,attempts\n"
    + "\n".join(f"qb,2025,{week},CHI,QB,200,2,1,4,10,20,30" for week in range(1, 9))
)


@pytest.mark.parametrize(
    "annotation",
    [
        "<span>Yahoo Default: {default}</span>",
        " Yahoo default {default}",
        " (Yahoo Default: {default})",
        " Yahoo Default",
    ],
)
def test_scraped_scoring_excludes_default_annotations(annotation):
    rows = "".join(
        f"<tr><td>{label}{annotation.format(default=default)}</td>"
        f"<td>{configured}</td><td></td></tr>"
        for label, default, configured, _, _ in SCORING_ROWS
    )
    html = (
        '<table id="settings-table"><tbody>'
        "<tr><td>League Name:</td><td>Scoring regression</td></tr></tbody></table>"
        f'<table id="settings-stat-mod-table"><tbody>{rows}</tbody></table>'
    )
    scoring = parse_settings_page(html, "https://football.fantasysports.yahoo.com/f1/42")["scoring"]
    assert scoring == EXPECTED
    assert scoring_rules(scoring) == (EXPECTED, [])


@pytest.mark.parametrize(
    "scoring",
    [
        SAVED_SCORING,
        {
            f"{label} (Yahoo Default: {default})": value
            for label, default, _, _, value in SCORING_ROWS
        },
    ],
)
def test_saved_annotations_preserve_custom_scoring_across_consumers(scoring):
    assert scoring_rules(scoring) == (EXPECTED, [])
    assert parse_historical_profiles({2025: STATS}, scoring)["qb"].weekly_points == [21.0] * 8
    assert [row["points"] for row in parse_weekly_stats({2025: STATS}, scoring)["qb"]] == [21.0] * 8
    actual = score_actual(
        {
            "passing_yards": 200,
            "passing_tds": 2,
            "passing_interceptions": 1,
            "receptions": 4,
            "kickoff_return_yards": 10,
            "punt_return_yards": 20,
        },
        scoring,
        "QB",
    )
    assert actual == {"points": 21.0, "reason": None}
    context = scoring_context(scoring)
    assert context["configured"] == EXPECTED
    for key, value in EXPECTED.items():
        assert context["effective"][key] == value
    assert context["yardage_rates"]["passing_yards"]["yards_per_point"] == 50.0


def test_saved_annotations_no_longer_withhold_weekly_forecast():
    now = datetime(2026, 9, 1, tzinfo=UTC)
    league = League(id=1, season=2026, scoring_json=json.dumps(SAVED_SCORING))
    player = Player(
        id=1,
        league_id=1,
        name="Test Quarterback",
        pro_team="CHI",
        position="QB",
        status="Active",
        current_slot="QB",
    )
    game = Game(
        season=2026,
        week=1,
        home_team="CHI",
        away_team="GB",
        kickoff=now + timedelta(days=10),
    )
    histories = parse_weekly_stats({2025: STATS}, SAVED_SCORING)
    row = forecast_players(league, [player], [game], 1, histories, {1: "qb"}, now)[0]
    assert row["points"] == 21.0
    assert row["reason"] is None


def test_annotations_do_not_hide_unsupported_rules_or_merge_defense_interceptions():
    rules, unsupported = scoring_rules(
        {
            **SAVED_SCORING,
            "interception_yahoo_default_2": 3,
            "long_td_bonus_yahoo_default_0": 5,
        }
    )
    assert rules["interceptions"] == -2.0
    assert unsupported == ["long_td_bonus"]


@pytest.mark.parametrize("reverse_rules", [False, True])
@pytest.mark.parametrize(
    "names",
    [
        ("interceptions", "interception"),
        ("interceptions_yahoo_default_1", "interception_yahoo_default_2"),
        ("Interceptions (Yahoo Default: -1)", "Interception (Yahoo Default: 2)"),
    ],
)
def test_projection_scoring_keeps_defense_interceptions_separate(names, reverse_rules):
    entries = [(names[0], -2.0), (names[1], 3.0), ("long_td_bonus_yahoo_default_0", 5.0)]
    scoring = dict(reversed(entries) if reverse_rules else entries)
    expected = {"interceptions": -2.0, "interception": 3.0, "long_td_bonus": 5.0}

    normalized = normalize_scoring(scoring)
    context = scoring_context(scoring)
    assert context["configured"] == expected
    for key, value in expected.items():
        assert normalized[key] == value
        assert context["effective"][key] == value
    assert score_projection({"interceptions": 1}, scoring) == -2.0
    # The singular alias remains valid for offensive raw projection stats.
    assert score_projection({"interception": 1}, scoring) == -2.0


def test_zero_scoring_values_stay_disabled():
    scoring = {key: 0 for key in SAVED_SCORING}
    rules, unsupported = scoring_rules(scoring)
    assert rules == dict.fromkeys(EXPECTED, 0.0)
    assert unsupported == []
