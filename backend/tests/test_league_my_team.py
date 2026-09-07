from __future__ import annotations

import json

import pytest
from app.db import SessionLocal
from app.models import DataSnapshot, League


def make_league(client, name, teams):
    response = client.post("/api/v1/leagues", json={"name": name, "season": 2026})
    assert response.status_code == 201
    league_id = response.json()["id"]
    for team in teams:
        response = client.post(
            f"/api/v1/leagues/{league_id}/players",
            json={
                "name": f"{team} runner",
                "pro_team": "CHI",
                "position": "RB",
                "rostered_by": team,
            },
        )
        assert response.status_code == 201
    return league_id


def test_my_team_persists_independently_and_survives_settings_updates(client):
    first = make_league(client, "First my-team league", ["Alpha", "Bravo"])
    second = make_league(client, "Second my-team league", ["Charlie", "Delta"])
    for league_id, name in [(first, "Bravo"), (second, "Charlie")]:
        saved = client.put(f"/api/v1/leagues/{league_id}/my-team", json={"my_team_name": name})
        assert saved.status_code == 200
        assert saved.json()["my_team_name"] == name
        assert client.get(f"/api/v1/leagues/{league_id}").json()["my_team_name"] == name
    with SessionLocal() as db:
        assert db.get(League, first).my_team_name == "Bravo"
        assert db.get(League, second).my_team_name == "Charlie"
    updated = client.put(f"/api/v1/leagues/{first}", json={"name": "Renamed", "season": 2026})
    assert updated.json()["my_team_name"] == "Bravo"
    listed = {league["id"]: league for league in client.get("/api/v1/leagues").json()}
    assert listed[first]["my_team_name"] == "Bravo"
    assert listed[second]["my_team_name"] == "Charlie"
    assert (
        client.put(f"/api/v1/leagues/{first}/my-team", json={"my_team_name": "Alpha"}).status_code
        == 200
    )
    cleared = client.put(f"/api/v1/leagues/{first}/my-team", json={"my_team_name": None})
    assert cleared.json()["my_team_name"] is None
    assert client.get(f"/api/v1/leagues/{first}").json()["my_team_name"] is None
    assert client.get(f"/api/v1/leagues/{second}").json()["my_team_name"] == "Charlie"


@pytest.mark.parametrize(
    "payload",
    [
        {"my_team_name": "Elsewhere"},
        {"my_team_name": ""},
        {"my_team_name": " "},
        {"my_team_name": "x" * 161},
        {},
        {"my_team_name": None, "name": "Oops"},
    ],
)
def test_my_team_rejects_invalid_input_without_losing_saved_choice(client, payload):
    league_id = make_league(client, "Validated my-team league", ["Home"])
    make_league(client, "Other league", ["Elsewhere"])
    client.put(f"/api/v1/leagues/{league_id}/my-team", json={"my_team_name": "Home"})
    rejected = client.put(f"/api/v1/leagues/{league_id}/my-team", json=payload)
    assert rejected.status_code == 422
    assert client.get(f"/api/v1/leagues/{league_id}").json()["my_team_name"] == "Home"


def test_can_select_imported_yahoo_team_before_roster_exists(client):
    league_id = make_league(client, "Yahoo my-team league", [])
    with SessionLocal() as db:
        league = db.get(League, league_id)
        league.yahoo_key = "nfl.l.my-team-test"
        db.add(
            DataSnapshot(
                source="yahoo_scrape",
                source_id=league.yahoo_key,
                payload_json=json.dumps({"teams": [{"name": "Owner's Team"}]}),
            )
        )
        db.commit()
    result = client.put(
        f"/api/v1/leagues/{league_id}/my-team", json={"my_team_name": "Owner's Team"}
    )
    assert result.status_code == 200
    assert result.json()["my_team_name"] == "Owner's Team"
    assert result.json()["player_count"] == 0


def test_my_team_missing_league(client):
    assert (
        client.put("/api/v1/leagues/999999/my-team", json={"my_team_name": None}).status_code == 404
    )
