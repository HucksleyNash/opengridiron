import json
from datetime import UTC, datetime, timedelta

import pytest
from app.db import SessionLocal
from app.models import Game, Player


@pytest.fixture
def weekly_league(client, monkeypatch):
    league = client.post(
        "/api/v1/leagues",
        json={
            "name": "Weekly roster projections",
            "season": 2096,
            "scoring": {"rushing_yards": 0.1},
            "roster_slots": ["RB", "BN"],
        },
    ).json()
    roster_csv = (
        "gsis_id,full_name,position,team\n"
        "current,Current Runner,RB,CHI\nbench,Bench Runner,RB,CHI\n"
    )
    stats_csv = "player_id,season,week,season_type,team,rushing_yards\n" + "".join(
        f"{name},2095,{week},REG,CHI,{yards}\n"
        for name, yards in [("current", 100), ("bench", 200)]
        for week in range(1, 5)
    )

    async def inputs(season):
        assert season == 2096
        return roster_csv, {2095: stats_csv}, []

    monkeypatch.setattr("app.routes.league_analysis.fetch_weekly_inputs", inputs, raising=False)
    with SessionLocal() as db:
        game = (
            db.query(Game).filter_by(season=2096, week=2, away_team="CHI", home_team="BUF").first()
        )
        if game is None:
            game = Game(season=2096, week=2, away_team="CHI", home_team="BUF")
            db.add(game)
        game.kickoff = datetime.now(UTC) + timedelta(days=2)
        for name, slot, season_points in [
            ("Current Runner", "RB", 900),
            ("Bench Runner", "BN", 100),
        ]:
            db.add(
                Player(
                    league_id=league["id"],
                    name=name,
                    position="RB",
                    pro_team="CHI",
                    rostered_by="Team A",
                    ownership="Team A",
                    current_slot=slot,
                    projected_points=season_points,
                    floor=season_points - 30,
                    ceiling=season_points + 30,
                    status="Active",
                    projection_context_json=json.dumps({"period": "season", "season": 2096}),
                )
            )
        db.commit()
    return league["id"]


def test_rosters_and_optimization_use_weekly_forecasts_without_overwriting_season(
    client, weekly_league
):
    response = client.get(f"/api/v1/leagues/{weekly_league}/weekly-lineup?team_name=Team+A")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["season"] == 2096 and data["week"] == 2
    assert data["current_total"] == 10 and data["projected_total"] == 20
    assert data["projected_gain"] == 10
    forecasts = {row["name"]: row for row in data["forecasts"]}
    assert forecasts["Current Runner"]["points"] == 10
    assert data["assignments"][0]["player_id"] == forecasts["Bench Runner"]["player_id"]
    stored = client.get(f"/api/v1/leagues/{weekly_league}/roster").json()
    assert {p["name"]: p["projected_points"] for p in stored} == {
        "Current Runner": 900,
        "Bench Runner": 100,
    }
    floor = client.get(
        f"/api/v1/leagues/{weekly_league}/weekly-lineup?team_name=Team+A&week=2&mode=floor"
    ).json()
    assert floor["projected_total"] == 17
    assert floor["current_total"] == 7
    assert (
        floor["forecasts"][0]["points"] == 10
    )  # Current-roster projections remain the central estimate.


def test_missing_weekly_data_stays_missing_and_holds_unknown_starters(
    client, weekly_league, monkeypatch
):
    async def inputs(_season):
        return "", {}, []

    monkeypatch.setattr("app.routes.league_analysis.fetch_weekly_inputs", inputs)
    data = client.get(
        f"/api/v1/leagues/{weekly_league}/weekly-lineup?team_name=Team+A&week=2"
    ).json()
    assert all(row["points"] is None for row in data["forecasts"])
    assert data["current_total"] is None and data["projected_total"] is None
    assert data["projected_gain"] is None and data["partial_total"]
    assert data["assignments"][0]["score"] is None
    assert data["assignments"][0]["action"] == "Hold"


def test_weekly_lineup_respects_week_and_game_locks(client, weekly_league):
    with SessionLocal() as db:
        game = db.query(Game).filter_by(season=2096, week=2, away_team="CHI").one()
        game.kickoff = datetime.now(UTC) - timedelta(minutes=5)
        db.commit()
    data = client.get(
        f"/api/v1/leagues/{weekly_league}/weekly-lineup?team_name=Team+A&week=2"
    ).json()
    current = next(p for p in data["forecasts"] if p["name"] == "Current Runner")
    assert data["assignments"][0]["player_id"] == current["player_id"]
    assert data["assignments"][0]["action"] == "Hold"
    other_week = client.get(
        f"/api/v1/leagues/{weekly_league}/weekly-lineup?team_name=Team+A&week=3"
    ).json()
    assert other_week["week"] == 3
    assert all(p["points"] is None for p in other_week["forecasts"])


def test_weekly_lineup_validates_scope(client, weekly_league):
    prefix = f"/api/v1/leagues/{weekly_league}/weekly-lineup"
    assert client.get(f"{prefix}?team_name=Team+A&week=19").status_code == 422
    assert client.get(f"{prefix}?team_name=Another+Team").status_code == 422
    assert client.get(f"{prefix}?team_name=Team+A&mode=season").status_code == 422
