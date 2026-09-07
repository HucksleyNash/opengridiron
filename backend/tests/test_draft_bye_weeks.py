from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4


def test_owner_roster_includes_bye_week_from_cached_schedule_with_team_alias(client) -> None:
    token = uuid4().hex
    league = client.post(
        "/api/v1/leagues",
        json={
            "name": f"Bye Week Draft {token}",
            "season": 2098,
            "roster_slots": ["QB"],
        },
    ).json()
    player = client.post(
        f"/api/v1/leagues/{league['id']}/players",
        json={
            "source_id": f"bye-week-{token}",
            "name": "Los Angeles Receiver",
            "pro_team": "LAR",
            "position": "WR",
            "projected_points": 250,
            "floor": 200,
            "ceiling": 300,
            "risk": 0.2,
        },
    ).json()
    for week in set(range(1, 19)) - {11}:
        response = client.post(
            "/api/v1/games",
            json={
                "season": 2098,
                "week": week,
                "away_team": "LA",
                "home_team": "TST",
                "kickoff": datetime(2098, 9, 1, 17, 0, tzinfo=UTC).isoformat(),
            },
        )
        assert response.status_code == 201

    session = client.post(
        f"/api/v1/leagues/{league['id']}/draft-sessions",
        json={
            "kind": "mock",
            "team_count": 8,
            "round_count": 1,
            "owner_team_slot": 1,
            "owner_team_name": "Open Gridiron",
        },
    ).json()
    started = client.post(
        f"/api/v1/draft-sessions/{session['id']}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": f"start-{token}"},
    ).json()["session"]
    recommendations = client.get(f"/api/v1/draft-sessions/{session['id']}/recommendations").json()
    board_before_pick = client.get(f"/api/v1/draft-sessions/{session['id']}/board").json()
    assert recommendations["candidates"][0]["bye_week"] == 11
    assert board_before_pick["available_players"][0]["bye_week"] == 11
    recorded = client.post(
        f"/api/v1/draft-sessions/{session['id']}/events",
        json={
            "type": "pick_recorded",
            "expected_sequence": started["current_sequence"],
            "idempotency_key": f"pick-{token}",
            "player_id": player["id"],
            "recommendation_snapshot_id": recommendations["snapshot_id"],
        },
    )
    assert recorded.status_code == 201

    board = client.get(f"/api/v1/draft-sessions/{session['id']}/board").json()
    owner = next(team for team in board["teams"] if team["is_owner"])
    assert owner["roster"][0]["bye_week"] == 11
    assert board["picks"][0]["bye_week"] == 11
