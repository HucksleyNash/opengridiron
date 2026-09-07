from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from app.db import SessionLocal
from app.models import DataSnapshot, League


def _automatic_mock_fixture(client, *, player_count: int = 32, rounds: int = 2):
    token = uuid4().hex
    league = client.post(
        "/api/v1/leagues",
        json={
            "name": f"Automatic Mock {token}",
            "season": 2026,
            "roster_slots": ["QB", "RB", "WR", "TE", "BENCH"],
        },
    ).json()
    positions = ["QB", "RB", "WR", "TE"]
    scraped_players = []
    for index in range(player_count):
        source_id = f"yahoo.p.{token[:8]}{index:03d}"
        position = positions[index % len(positions)]
        player = client.post(
            f"/api/v1/leagues/{league['id']}/players",
            json={
                "source_id": source_id,
                "name": f"Ranked {position} {index + 1}",
                "pro_team": "CHI",
                "position": position,
                "projected_points": 300 - index,
                "floor": 250 - index,
                "ceiling": 330 - index,
                "risk": 0.2,
            },
        ).json()
        scraped_players.append(
            {
                "source_id": source_id,
                "name": player["name"],
                "pro_team": player["pro_team"],
                "position": player["position"],
                "overall_rank": index + 1,
            }
        )

    db = SessionLocal()
    try:
        stored_league = db.get(League, league["id"])
        assert stored_league is not None
        stored_league.yahoo_key = f"scrape:2026:{token}"
        db.add(
            DataSnapshot(
                source="yahoo_scrape",
                source_id=stored_league.yahoo_key,
                status="fresh",
                payload_json=json.dumps(
                    {
                        "players": scraped_players,
                        "player_pool_complete": True,
                    }
                ),
            )
        )
        db.commit()
    finally:
        db.close()

    session = client.post(
        f"/api/v1/leagues/{league['id']}/draft-sessions",
        json={
            "kind": "mock",
            "opponent_mode": "automatic",
            "team_count": 8,
            "round_count": rounds,
            "owner_team_slot": 3,
        },
    ).json()
    return league, session


def _sync(client, league_id: int, session: dict):
    return client.post(
        f"/api/v1/leagues/{league_id}/draft-rankings/sync",
        json={
            "session_id": session["id"],
            "expected_sequence": session["current_sequence"],
        },
    )


def test_automatic_mock_syncs_rankings_and_stops_on_owner_turn(client) -> None:
    league, session = _automatic_mock_fixture(client)
    assert session["status"] == "SETUP"
    assert session["opponent_mode"] == "automatic"
    assert session["readiness"]["findings"][0]["code"] == "ranking_required"

    synced_response = _sync(client, league["id"], session)
    assert synced_response.status_code == 200
    synced = synced_response.json()
    assert synced["status"] == "ready"
    assert synced["row_count"] == 32
    assert synced["session"]["status"] == "READY"

    started = client.post(
        f"/api/v1/draft-sessions/{session['id']}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": f"start-{uuid4()}"},
    ).json()["session"]
    assert started["current_sequence"] == 1

    first_key = f"auto-{uuid4()}"
    first = client.post(
        f"/api/v1/draft-sessions/{session['id']}/opponent-picks/next",
        json={"expected_sequence": 1, "idempotency_key": first_key},
    )
    assert first.status_code == 201
    assert first.json()["event"]["source"] == "mock_auto"
    assert first.json()["event"]["metadata"]["policy_version"] == "mock-team-aware-v1"
    assert first.json()["board"]["current_team_slot"] == 2

    retry = client.post(
        f"/api/v1/draft-sessions/{session['id']}/opponent-picks/next",
        json={"expected_sequence": 1, "idempotency_key": first_key},
    )
    assert retry.status_code == 201
    assert retry.json()["event"]["id"] == first.json()["event"]["id"]

    second = client.post(
        f"/api/v1/draft-sessions/{session['id']}/opponent-picks/next",
        json={"expected_sequence": 2, "idempotency_key": f"auto-{uuid4()}"},
    )
    assert second.status_code == 201
    assert second.json()["board"]["owner_on_clock"] is True
    assert [pick["team_slot"] for pick in second.json()["board"]["picks"]] == [1, 2]

    stopped = client.post(
        f"/api/v1/draft-sessions/{session['id']}/opponent-picks/next",
        json={"expected_sequence": 3, "idempotency_key": f"auto-{uuid4()}"},
    )
    assert stopped.status_code == 409
    assert stopped.json()["error"] == "owner_on_clock"


def test_automatic_mock_policy_is_repeatable(client) -> None:
    league, first_session = _automatic_mock_fixture(client)
    first_session = _sync(client, league["id"], first_session).json()["session"]
    first_session = client.post(
        f"/api/v1/draft-sessions/{first_session['id']}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": f"start-{uuid4()}"},
    ).json()["session"]
    first_pick = client.post(
        f"/api/v1/draft-sessions/{first_session['id']}/opponent-picks/next",
        json={"expected_sequence": 1, "idempotency_key": f"auto-{uuid4()}"},
    ).json()["event"]["player_id"]

    second_session = client.post(
        f"/api/v1/leagues/{league['id']}/draft-sessions",
        json={
            "kind": "mock",
            "opponent_mode": "automatic",
            "team_count": 8,
            "round_count": 2,
            "owner_team_slot": 3,
        },
    ).json()
    second_session = _sync(client, league["id"], second_session).json()["session"]
    second_session = client.post(
        f"/api/v1/draft-sessions/{second_session['id']}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": f"start-{uuid4()}"},
    ).json()["session"]
    second_pick = client.post(
        f"/api/v1/draft-sessions/{second_session['id']}/opponent-picks/next",
        json={"expected_sequence": 1, "idempotency_key": f"auto-{uuid4()}"},
    ).json()["event"]["player_id"]
    assert second_pick == first_pick


def test_automatic_picks_are_rejected_outside_eligible_mock(client) -> None:
    league, automatic = _automatic_mock_fixture(client)
    automatic = _sync(client, league["id"], automatic).json()["session"]
    live = client.post(
        f"/api/v1/leagues/{league['id']}/draft-sessions",
        json={"kind": "live", "team_count": 8, "round_count": 1, "owner_team_slot": 3},
    ).json()
    live = client.post(
        f"/api/v1/draft-sessions/{live['id']}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": f"start-{uuid4()}"},
    ).json()["session"]
    rejected = client.post(
        f"/api/v1/draft-sessions/{live['id']}/opponent-picks/next",
        json={"expected_sequence": live["current_sequence"], "idempotency_key": f"auto-{uuid4()}"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"] == "automatic_mock_disabled"


def test_incomplete_ranked_pool_keeps_start_disabled(client) -> None:
    league, session = _automatic_mock_fixture(client, player_count=8, rounds=2)
    response = _sync(client, league["id"], session)
    assert response.status_code == 422
    assert response.json()["error"] == "ranking_incomplete"
    assert any(
        finding["code"] == "ranked_pool_incomplete" for finding in response.json()["findings"]
    )
    current = client.get(f"/api/v1/draft-sessions/{session['id']}").json()
    assert current["status"] == "SETUP"
    assert current["readiness"]["ready"] is False


def test_automatic_mock_can_run_to_completion_without_manual_opponent_picks(client) -> None:
    league, session = _automatic_mock_fixture(client)
    session = _sync(client, league["id"], session).json()["session"]
    session = client.post(
        f"/api/v1/draft-sessions/{session['id']}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": f"start-{uuid4()}"},
    ).json()["session"]

    while session["status"] == "LIVE":
        board = client.get(f"/api/v1/draft-sessions/{session['id']}/board").json()
        if board["owner_on_clock"]:
            recommendations = client.get(
                f"/api/v1/draft-sessions/{session['id']}/recommendations"
            ).json()
            response = client.post(
                f"/api/v1/draft-sessions/{session['id']}/events",
                json={
                    "type": "pick_recorded",
                    "expected_sequence": session["current_sequence"],
                    "idempotency_key": f"owner-{uuid4()}",
                    "player_id": recommendations["candidates"][0]["player_id"],
                    "recommendation_snapshot_id": recommendations["snapshot_id"],
                },
            )
        else:
            response = client.post(
                f"/api/v1/draft-sessions/{session['id']}/opponent-picks/next",
                json={
                    "expected_sequence": session["current_sequence"],
                    "idempotency_key": f"auto-{uuid4()}",
                },
            )
        assert response.status_code == 201
        session = response.json()["session"]

    board = client.get(f"/api/v1/draft-sessions/{session['id']}/board").json()
    assert session["status"] == "COMPLETE"
    assert board["completed_picks"] == board["total_picks"] == 16
    assert sum(pick["source"] == "mock_auto" for pick in board["picks"]) == 14
    assert sum(pick["team_slot"] == 3 for pick in board["picks"]) == 2


def test_two_automatic_tabs_commit_only_one_pick_for_a_sequence(client) -> None:
    league, session = _automatic_mock_fixture(client)
    session = _sync(client, league["id"], session).json()["session"]
    session = client.post(
        f"/api/v1/draft-sessions/{session['id']}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": f"start-{uuid4()}"},
    ).json()["session"]
    barrier = Barrier(2)

    def advance(index: int):
        barrier.wait(timeout=5)
        return client.post(
            f"/api/v1/draft-sessions/{session['id']}/opponent-picks/next",
            json={
                "expected_sequence": session["current_sequence"],
                "idempotency_key": f"tab-{index}-{uuid4()}",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(advance, range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    board = client.get(f"/api/v1/draft-sessions/{session['id']}/board").json()
    assert board["completed_picks"] == 1
