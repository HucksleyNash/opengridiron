from __future__ import annotations

from uuid import uuid4


def _league_with_players(client, count: int = 20) -> tuple[int, list[int]]:
    league_response = client.post(
        "/api/v1/leagues",
        json={
            "name": f"Draft API {uuid4()}",
            "season": 2026,
            "scoring": {"receptions": 1.0},
            "roster_slots": ["QB", "RB", "WR", "TE", "FLEX", "BENCH"],
        },
    )
    assert league_response.status_code == 201
    league_id = league_response.json()["id"]
    positions = ["RB", "WR", "QB", "TE"]
    player_ids = []
    for index in range(count):
        response = client.post(
            f"/api/v1/leagues/{league_id}/players",
            json={
                "source_id": f"draft-api-{index}",
                "name": f"Draft Player {index + 1}",
                "pro_team": "CHI",
                "position": positions[index % len(positions)],
                "projected_points": 300 - index * 5,
                "floor": 240 - index * 4,
                "ceiling": 350 - index * 4,
                "ros_value": 30 - index,
                "risk": min(0.9, 0.05 + index * 0.02),
            },
        )
        assert response.status_code == 201
        player_ids.append(response.json()["id"])
    return league_id, player_ids


def _session(client, league_id: int, *, owner_slot: int = 1) -> dict:
    response = client.post(
        f"/api/v1/leagues/{league_id}/draft-sessions",
        json={
            "kind": "mock",
            "team_count": 8,
            "round_count": 2,
            "owner_team_slot": owner_slot,
            "owner_team_name": "Open Gridiron",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_draft_sessions_can_be_archived_and_restored_without_changing_history(client) -> None:
    league_id, _player_ids = _league_with_players(client, count=8)
    session = _session(client, league_id)

    archived = client.put(
        f"/api/v1/draft-sessions/{session['id']}/archive",
        json={"expected_sequence": 0, "archived": True},
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert archived.json()["status"] == "READY"
    assert archived.json()["current_sequence"] == 0

    listed = client.get(f"/api/v1/leagues/{league_id}/draft-sessions").json()
    assert listed[0]["archived_at"] == archived.json()["archived_at"]

    stale = client.put(
        f"/api/v1/draft-sessions/{session['id']}/archive",
        json={"expected_sequence": 1, "archived": False},
    )
    assert stale.status_code == 409
    assert stale.json()["error"] == "draft_conflict"

    restored = client.put(
        f"/api/v1/draft-sessions/{session['id']}/archive",
        json={"expected_sequence": 0, "archived": False},
    )
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None
    assert restored.json()["status"] == "READY"


def test_manual_draft_lifecycle_is_sequence_safe_and_replayable(client) -> None:
    league_id, player_ids = _league_with_players(client)
    session = _session(client, league_id)
    session_id = session["id"]

    assert session["status"] == "READY"
    assert session["readiness"] == {"ready": True, "findings": []}
    assert len(session["teams"]) == 8

    start = client.post(
        f"/api/v1/draft-sessions/{session_id}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": "start-draft"},
    )
    assert start.status_code == 200
    assert start.json()["session"]["status"] == "LIVE"
    assert start.json()["session"]["current_sequence"] == 1

    recommendations = client.get(f"/api/v1/draft-sessions/{session_id}/recommendations").json()
    assert recommendations["status"] == "ready"
    assert len(recommendations["candidates"]) == 3
    assert recommendations["candidates"][0]["components"]["weights"]["normalized_vor"] == 0.20
    assert len(recommendations["alternatives"]) == 17

    first_pick = client.post(
        f"/api/v1/draft-sessions/{session_id}/events",
        json={
            "type": "pick_recorded",
            "expected_sequence": 1,
            "idempotency_key": "pick-1",
            "player_id": player_ids[0],
            "recommendation_snapshot_id": recommendations["snapshot_id"],
        },
    )
    assert first_pick.status_code == 201
    first_event = first_pick.json()["event"]
    assert first_event["overall_pick"] == 1
    assert first_event["team_slot"] == 1
    assert first_event["recommendation_snapshot_id"] == recommendations["snapshot_id"]

    stale = client.post(
        f"/api/v1/draft-sessions/{session_id}/events",
        json={
            "type": "pick_recorded",
            "expected_sequence": 1,
            "idempotency_key": "stale-pick",
            "player_id": player_ids[1],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"] == "draft_conflict"
    assert stale.json()["current_sequence"] == 2

    second_pick = client.post(
        f"/api/v1/draft-sessions/{session_id}/events",
        json={
            "type": "pick_recorded",
            "expected_sequence": 2,
            "idempotency_key": "pick-2",
            "player_id": player_ids[1],
        },
    )
    assert second_pick.status_code == 201
    assert second_pick.json()["event"]["team_slot"] == 2

    pause = client.post(
        f"/api/v1/draft-sessions/{session_id}/actions/pause",
        json={"expected_sequence": 3, "idempotency_key": "pause-draft"},
    )
    assert pause.status_code == 200
    assert pause.json()["session"]["status"] == "PAUSED"

    correction = client.post(
        f"/api/v1/draft-sessions/{session_id}/events",
        json={
            "type": "pick_replaced",
            "expected_sequence": 4,
            "idempotency_key": "replace-first-pick",
            "target_event_id": first_event["id"],
            "player_id": player_ids[2],
            "reason": "Wrong player selected during keyboard entry",
        },
    )
    assert correction.status_code == 201
    assert correction.json()["session"]["current_sequence"] == 6

    board = client.get(f"/api/v1/draft-sessions/{session_id}/board").json()
    assert board["completed_picks"] == 2
    assert [pick["player_id"] for pick in board["picks"]] == [player_ids[2], player_ids[1]]
    assert board["current_overall_pick"] == 3
    assert board["current_team_slot"] == 3

    resume = client.post(
        f"/api/v1/draft-sessions/{session_id}/actions/resume",
        json={"expected_sequence": 6, "idempotency_key": "resume-draft"},
    )
    assert resume.status_code == 200
    assert resume.json()["session"]["status"] == "LIVE"

    replay = client.get(f"/api/v1/draft-sessions/{session_id}/replay")
    assert replay.status_code == 200
    assert replay.json()["decisions"][0]["at_time"] is True


def test_owner_pick_requires_the_current_ready_snapshot(client) -> None:
    league_id, player_ids = _league_with_players(client, count=8)
    session = _session(client, league_id)
    session_id = session["id"]
    client.post(
        f"/api/v1/draft-sessions/{session_id}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": "start-owner-gate"},
    )

    missing = client.post(
        f"/api/v1/draft-sessions/{session_id}/events",
        json={
            "type": "pick_recorded",
            "expected_sequence": 1,
            "idempotency_key": "owner-without-advice",
            "player_id": player_ids[0],
        },
    )
    assert missing.status_code == 425
    assert missing.json()["error"] == "advice_pending"

    recommendations = client.get(f"/api/v1/draft-sessions/{session_id}/recommendations").json()
    accepted = client.post(
        f"/api/v1/draft-sessions/{session_id}/events",
        json={
            "type": "pick_recorded",
            "expected_sequence": 1,
            "idempotency_key": "owner-with-advice",
            "player_id": player_ids[0],
            "recommendation_snapshot_id": recommendations["snapshot_id"],
        },
    )
    assert accepted.status_code == 201


def test_replay_scores_a_legal_owner_choice_outside_the_shortlist(client) -> None:
    league_id, player_ids = _league_with_players(client, count=20)
    session = _session(client, league_id)
    session_id = session["id"]
    client.post(
        f"/api/v1/draft-sessions/{session_id}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": f"start-{uuid4()}"},
    )
    recommendations = client.get(f"/api/v1/draft-sessions/{session_id}/recommendations").json()
    assert player_ids[-1] not in {
        candidate["player_id"] for candidate in recommendations["candidates"]
    }
    recorded = client.post(
        f"/api/v1/draft-sessions/{session_id}/events",
        json={
            "type": "pick_recorded",
            "expected_sequence": 1,
            "idempotency_key": f"outside-shortlist-{uuid4()}",
            "player_id": player_ids[-1],
            "recommendation_snapshot_id": recommendations["snapshot_id"],
        },
    )
    assert recorded.status_code == 201

    replay = client.get(f"/api/v1/draft-sessions/{session_id}/replay").json()
    decision = replay["decisions"][0]
    assert decision["chosen_candidate"]["player_id"] == player_ids[-1]
    assert decision["rank_at_time"] == 20
    assert decision["eligible_count"] == 20
    assert decision["decision_quality"] > 0
    assert replay["coaching"]["lane"] == "mock"
    assert replay["waiver_moves"][0]["drop"]["player_id"] == player_ids[-1]
    assert replay["waiver_moves"][0]["projected_point_gain"] > 0


def test_queue_preferences_use_an_independent_revision(client) -> None:
    league_id, player_ids = _league_with_players(client, count=8)
    session = _session(client, league_id, owner_slot=3)
    session_id = session["id"]

    saved = client.put(
        f"/api/v1/draft-sessions/{session_id}/preferences",
        json={
            "expected_revision": 0,
            "items": [
                {"player_id": player_ids[1], "queue_rank": 1, "target": True},
                {"player_id": player_ids[3], "queue_rank": 2, "note": "If TE falls"},
            ],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1

    stale = client.put(
        f"/api/v1/draft-sessions/{session_id}/preferences",
        json={"expected_revision": 0, "items": []},
    )
    assert stale.status_code == 409
    assert stale.json()["error"] == "preference_conflict"

    board = client.get(f"/api/v1/draft-sessions/{session_id}/board").json()
    assert [player["id"] for player in board["queue"]] == player_ids[1:4:2]


def test_recommendation_evidence_requires_explicit_athlete_identity(client) -> None:
    import json
    from datetime import UTC, datetime

    from app.db import SessionLocal
    from app.models import NewsItem, NewsSource, Player

    league_id, player_ids = _league_with_players(client, count=8)
    session = _session(client, league_id)
    with SessionLocal() as db:
        player = db.get(Player, player_ids[0])
        assert player is not None and player.athlete_id is not None
        source = NewsSource(
            name=f"Official fixture {uuid4()}",
            url=f"https://example.test/source/{uuid4()}",
            official=True,
        )
        db.add(source)
        db.flush()
        db.add_all(
            [
                NewsItem(
                    source_id=source.id,
                    canonical_url=f"https://example.test/linked/{uuid4()}",
                    content_hash=uuid4().hex * 2,
                    title="Explicitly linked status update",
                    excerpt="Practice status changed.",
                    players_json=json.dumps([{"athlete_id": player.athlete_id}]),
                    published_at=datetime.now(UTC),
                ),
                NewsItem(
                    source_id=source.id,
                    canonical_url=f"https://example.test/name-only/{uuid4()}",
                    content_hash=uuid4().hex * 2,
                    title="Name-only item must not attach",
                    excerpt=player.name,
                    players_json=json.dumps([{"name": player.name}]),
                    published_at=datetime.now(UTC),
                ),
            ]
        )
        db.commit()

    started = client.post(
        f"/api/v1/draft-sessions/{session['id']}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": f"start-{uuid4()}"},
    )
    assert started.status_code == 200
    recommendations = client.get(f"/api/v1/draft-sessions/{session['id']}/recommendations").json()
    candidate = next(
        item for item in recommendations["candidates"] if item["player_id"] == player_ids[0]
    )
    assert [item["title"] for item in candidate["evidence"]] == ["Explicitly linked status update"]
    assert candidate["evidence"][0]["score_effect"] == 0
