from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4


def test_two_same_sequence_writers_produce_one_canonical_pick(client) -> None:
    league = client.post(
        "/api/v1/leagues",
        json={"name": f"Concurrency {uuid4()}", "season": 2026},
    ).json()
    player_ids = []
    for index in range(2):
        player = client.post(
            f"/api/v1/leagues/{league['id']}/players",
            json={
                "source_id": f"concurrent-{uuid4()}",
                "name": f"Concurrent Player {index}",
                "pro_team": "CHI",
                "position": "RB",
                "projected_points": 200 - index,
            },
        ).json()
        player_ids.append(player["id"])
    session = client.post(
        f"/api/v1/leagues/{league['id']}/draft-sessions",
        json={
            "kind": "mock",
            "team_count": 8,
            "round_count": 1,
            "owner_team_slot": 3,
        },
    ).json()
    client.post(
        f"/api/v1/draft-sessions/{session['id']}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": f"start-{uuid4()}"},
    )

    barrier = Barrier(2)

    def record(player_id: int):
        barrier.wait(timeout=5)
        return client.post(
            f"/api/v1/draft-sessions/{session['id']}/events",
            json={
                "type": "pick_recorded",
                "expected_sequence": 1,
                "idempotency_key": f"writer-{player_id}-{uuid4()}",
                "player_id": player_id,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(record, player_ids))

    assert sorted(response.status_code for response in responses) == [201, 409]
    rejected = next(response for response in responses if response.status_code == 409)
    assert rejected.json()["error"] == "draft_conflict"
    board = client.get(f"/api/v1/draft-sessions/{session['id']}/board").json()
    assert board["completed_picks"] == 1
    assert board["picks"][0]["player_id"] in player_ids
