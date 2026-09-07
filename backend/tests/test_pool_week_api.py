from __future__ import annotations

from fastapi.testclient import TestClient


def _game(client: TestClient, away: str, home: str, kickoff: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/games",
        json={
            "season": 2096,
            "week": 1,
            "away_team": away,
            "home_team": home,
            "kickoff": kickoff,
            "home_win_probability": 0.7,
            "home_cover_probability": 0.54,
            "spread_home": -3.5,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_survivor_week_flow_and_version_conflict(client: TestClient) -> None:
    first = _game(client, "GB", "CHI", "2096-09-06T17:00:00Z")
    _game(client, "KC", "DEN", "2096-09-07T00:20:00Z")
    pool = client.post(
        "/api/v1/pools",
        json={
            "name": "Pool Week Winner",
            "pool_type": "survivor",
            "season": 2096,
            "rules": {
                "direction": "winner",
                "basis": "straight_up",
                "picks_per_week": 1,
                "max_team_uses": 1,
                "lock_mode": "game_start",
            },
        },
    )
    assert pool.status_code == 201
    entry = client.post(f"/api/v1/pools/{pool.json()['id']}/entries", json={"name": "Main"})
    assert entry.status_code == 201

    overview = client.get("/api/v1/pools/overview")
    assert overview.status_code == 200
    summary = next(item for item in overview.json()["pools"] if item["id"] == pool.json()["id"])
    assert summary["suggested_week"] == 1
    assert summary["entries"][0]["card_state"] == "draft"
    assert summary["entries"][0]["missing_count"] == 1

    workspace = client.get(
        f"/api/v1/pools/{pool.json()['id']}/weeks/1?entry_id={entry.json()['id']}"
    )
    assert workspace.status_code == 200
    assert workspace.json()["card"]["version"] == 0
    assert workspace.json()["games"][0]["recommendations"]
    assert len(workspace.json()["survivor_slots"]) == 1

    payload = {
        "version": 0,
        "picks": [
            {
                "slot": 1,
                "game_id": first["id"],
                "team": "CHI",
                "confidence": None,
            }
        ],
    }
    saved = client.put(f"/api/v1/entries/{entry.json()['id']}/weeks/1/picks", json=payload)
    assert saved.status_code == 200
    assert saved.json()["card"]["version"] == 1
    assert saved.json()["card"]["state"] == "complete"

    idempotent = client.put(f"/api/v1/entries/{entry.json()['id']}/weeks/1/picks", json=payload)
    assert idempotent.status_code == 200
    assert idempotent.json()["card"]["version"] == 1

    changed = {**payload, "picks": [{**payload["picks"][0], "team": "GB"}]}
    conflict = client.put(f"/api/v1/entries/{entry.json()['id']}/weeks/1/picks", json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "card_conflict"
    assert conflict.json()["card"]["picks"][0]["team"] == "CHI"

    assert client.post(f"/api/v1/entries/{entry.json()['id']}/picks", json={}).status_code == 405


def test_confidence_card_requires_every_game_and_unique_weight(client: TestClient) -> None:
    _game(client, "BUF", "MIA", "2096-09-07T17:00:00Z")
    _game(client, "DAL", "NYG", "2096-09-07T20:00:00Z")
    games = client.get("/api/v1/games?season=2096&week=1").json()
    assert len(games) >= 2
    pool = client.post(
        "/api/v1/pools",
        json={
            "name": "Pool Week Confidence",
            "pool_type": "confidence",
            "season": 2096,
            "rules": {"direction": "winner", "basis": "straight_up"},
        },
    )
    entry = client.post(
        f"/api/v1/pools/{pool.json()['id']}/entries", json={"name": "Confidence main"}
    )
    workspace = client.get(
        f"/api/v1/pools/{pool.json()['id']}/weeks/1?entry_id={entry.json()['id']}"
    )
    assert workspace.status_code == 200
    suggested = {
        game["id"]: (game["suggested_team"], game["suggested_confidence"])
        for game in workspace.json()["games"]
    }
    assert all(team and weight for team, weight in suggested.values())

    invalid = client.put(
        f"/api/v1/entries/{entry.json()['id']}/weeks/1/picks",
        json={
            "version": 0,
            "picks": [
                {
                    "game_id": game["id"],
                    "team": suggested[game["id"]][0],
                    "confidence": 1,
                }
                for game in games
            ],
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"] == "invalid_card"
    assert any(item["code"] == "duplicate_weight" for item in invalid.json()["findings"])

    complete = client.put(
        f"/api/v1/entries/{entry.json()['id']}/weeks/1/picks",
        json={
            "version": 0,
            "picks": [
                {
                    "game_id": game["id"],
                    "team": suggested[game["id"]][0],
                    "confidence": suggested[game["id"]][1],
                }
                for game in games
            ],
        },
    )
    assert complete.status_code == 200
    assert complete.json()["card"]["state"] == "complete"
    assert complete.json()["card"]["selection_count"] == len(games)
    assert complete.json()["card"]["weight_count"] == len(games)

    saved_card = complete.json()["card"]
    assert all(pick["slot"] is None for pick in saved_card["picks"])
    first_pick = saved_card["picks"][0]
    first_game = next(game for game in games if game["id"] == first_pick["game_id"])
    changed_team = (
        first_game["home_team"]
        if first_pick["team"] == first_game["away_team"]
        else first_game["away_team"]
    )
    round_trip_picks = [
        {
            "slot": slot,
            "game_id": pick["game_id"],
            "team": changed_team if pick["game_id"] == first_pick["game_id"] else pick["team"],
            "confidence": pick["confidence"],
        }
        for slot, pick in enumerate(saved_card["picks"], start=1)
    ]
    changed = client.put(
        f"/api/v1/entries/{entry.json()['id']}/weeks/1/picks",
        json={"version": saved_card["version"], "picks": round_trip_picks},
    )
    assert changed.status_code == 200
    assert changed.json()["card"]["version"] == saved_card["version"] + 1
    assert changed.json()["card"]["state"] == "complete"
