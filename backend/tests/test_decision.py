from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from app.models import Game, Player, Pool, PoolEntry, PoolPick
from app.services.decision import (
    confidence_recommendations,
    no_vig_probability,
    optimize_lineup,
    rank_waivers,
    survivor_recommendations,
)
from app.services.modeling import brier_score, team_win_probability
from app.services.projections import calibrated_interval, score_projection


def player(identifier: int, position: str, projection: float, slot: str | None = None) -> Player:
    return Player(
        id=identifier,
        league_id=1,
        name=f"Player {identifier}",
        pro_team="CHI",
        position=position,
        status="Active",
        projected_points=projection,
        floor=projection - 3,
        ceiling=projection + 4,
        ros_value=projection * 2,
        current_slot=slot,
    )


def game(identifier: int, week: int, away: str, home: str, home_probability: float) -> Game:
    return Game(
        id=identifier,
        season=2026,
        week=week,
        away_team=away,
        home_team=home,
        kickoff=datetime(2026, 9, 10, 0, tzinfo=UTC),
        home_win_probability=home_probability,
        home_cover_probability=home_probability - 0.04,
        source="fixture",
        source_timestamp=datetime.now(UTC),
        win_probability_kind="manual",
        cover_probability_kind="manual",
    )


def test_lineup_optimizer_obeys_slot_constraints_and_improves_current_lineup() -> None:
    players = [
        player(1, "QB", 20, "QB"),
        player(2, "RB", 14, "RB"),
        player(3, "RB", 9, "BN"),
        player(4, "WR", 17, "WR"),
        player(5, "WR", 13, "BN"),
    ]
    result = optimize_lineup(players, ["QB", "RB", "WR", "FLEX", "BN"])
    assert [slot for slot, _player, _score in result.assignments] == ["QB", "RB", "WR", "FLEX"]
    assert result.projected_total == 64
    assert result.current_total == 51
    assert not result.unfilled_slots


def test_waivers_include_every_available_role_beyond_top_twenty(client) -> None:
    league = client.post(
        "/api/v1/leagues",
        json={
            "name": "All waiver roles",
            "season": 2026,
            "roster_slots": ["QB", "RB", "WR", "TE", "K", "DEF", "LB"],
        },
    )
    assert league.status_code == 201
    league_id = league.json()["id"]
    expected_ids = []
    roles = ["QB"] * 25 + ["RB", "WR", "TE", "K", "DEF", "LB", "DB", "DL"]
    for index, position in enumerate(roles):
        response = client.post(
            f"/api/v1/leagues/{league_id}/players",
            json={
                "name": "Same Name" if index > 24 else f"Quarterback {index}",
                "position": position,
                "pro_team": "CHI",
                "projected_points": 100 - index,
                "ros_value": 200 - index,
                "ownership": "W" if index % 2 else "FA",
            },
        )
        assert response.status_code == 201
        expected_ids.append(response.json()["id"])
    client.post(
        f"/api/v1/leagues/{league_id}/players",
        json={
            "name": "Rostered star",
            "position": "QB",
            "pro_team": "BUF",
            "ownership": "TEAM",
            "rostered_by": "Owner",
            "ros_value": 999,
        },
    )

    response = client.get(f"/api/v1/leagues/{league_id}/waivers")
    assert response.status_code == 200
    recommendations = response.json()
    assert len(recommendations) == len(roles)
    assert [item["player_id"] for item in recommendations] == expected_ids
    assert [item["rank"] for item in recommendations] == list(range(1, len(roles) + 1))
    assert all("Rostered star" not in item["subject"] for item in recommendations)


def test_waivers_preserve_explicit_limits_and_empty_pool() -> None:
    candidates = [player(index, "WR", 10 + index) for index in range(30)]
    for candidate in candidates:
        candidate.ownership = "FA"
        candidate.risk = 0.2
    assert len(rank_waivers(candidates, ["WR"], limit=5)) == 5
    assert rank_waivers([], ["WR"]) == []


def test_survivor_respects_reuse_rule_and_loser_direction() -> None:
    pool = Pool(
        id=1,
        name="Loser",
        pool_type="survivor",
        season=2026,
        rules_json=json.dumps(
            {
                "direction": "loser",
                "basis": "straight_up",
                "picks_per_week": 1,
                "max_team_uses": 1,
                "allowed_teams": [],
                "blocked_teams": [],
                "tie_result": "eliminate",
                "lock_mode": "game_start",
                "confidence_weights": [],
                "future_value_weight": 0,
            }
        ),
    )
    entry = PoolEntry(id=1, pool=pool, name="A")
    entry.picks = [PoolPick(week=1, slot=1, team="GB")]
    recommendations = survivor_recommendations(
        pool,
        entry,
        [game(1, 2, "GB", "CHI", 0.70), game(2, 2, "DET", "MIN", 0.60)],
        2,
    )
    assert all(not item.subject.startswith("GB ") for item in recommendations)
    assert recommendations[0].subject.startswith("DET")
    assert recommendations[0].confidence == pytest.approx(0.60)


def test_confidence_weights_follow_probability_order() -> None:
    pool = Pool(
        id=2,
        name="Confidence",
        pool_type="confidence",
        season=2026,
        rules_json=json.dumps({"basis": "straight_up", "confidence_weights": [1, 5]}),
    )
    output = confidence_recommendations(
        pool,
        [game(1, 1, "GB", "CHI", 0.55), game(2, 1, "DET", "MIN", 0.82)],
    )
    assert output[0]["confidence_weight"] == 5
    assert output[0]["pick"] == "MIN"


def test_odds_normalization_and_scoring() -> None:
    home, away = no_vig_probability(1.80, 2.10)
    assert home + away == pytest.approx(1)
    assert (
        score_projection(
            {"passing_yards": 250, "passing_tds": 2, "interceptions": 1, "rushing_yards": 20},
            {"passing_tds": 6},
        )
        == 22
    )
    floor, ceiling, sigma = calibrated_interval(20, "WR", 2)
    assert floor < 20 < ceiling
    assert sigma > 5.8


def test_team_probability_blend_and_brier_score() -> None:
    model_only = team_win_probability(home_elo=1550, away_elo=1500, home_epa=0.04, away_epa=-0.02)
    assert model_only["home_win_probability"] > 0.5
    assert model_only["confidence"] == "lower"
    blended = team_win_probability(
        home_elo=1550,
        away_elo=1500,
        market_probability=0.7,
        market_weight=0.65,
    )
    assert blended["market_available"] is True
    assert 0.5 < blended["home_win_probability"] < 0.7
    assert brier_score([0.8, 0.3], [1, 0]) == pytest.approx(0.065)
