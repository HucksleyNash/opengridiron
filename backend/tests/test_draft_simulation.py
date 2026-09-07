from __future__ import annotations

import time
from uuid import uuid4

from app.db import SessionLocal, engine
from app.draft.models import DraftComputationRun
from app.draft.simulation import recover_interrupted_computations
from sqlalchemy import event


def _live_session(client) -> tuple[int, int, list[int]]:
    league = client.post(
        "/api/v1/leagues",
        json={
            "name": f"Simulation {uuid4()}",
            "season": 2026,
            "roster_slots": ["QB", "RB", "WR", "TE", "FLEX", "BENCH"],
        },
    ).json()
    players = []
    for index in range(12):
        player = client.post(
            f"/api/v1/leagues/{league['id']}/players",
            json={
                "source_id": f"sim-{index}",
                "name": f"Scenario Player {index}",
                "pro_team": "CHI",
                "position": ["RB", "WR", "QB", "TE"][index % 4],
                "projected_points": 280 - index * 7,
                "risk": 0.1 + index * 0.02,
            },
        ).json()
        players.append(player["id"])
    session = client.post(
        f"/api/v1/leagues/{league['id']}/draft-sessions",
        json={
            "kind": "mock",
            "team_count": 8,
            "round_count": 2,
            "owner_team_slot": 3,
        },
    ).json()
    client.post(
        f"/api/v1/draft-sessions/{session['id']}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": f"start-{uuid4()}"},
    )
    return league["id"], session["id"], players


def test_bounded_simulation_compares_current_candidates_deterministically(client) -> None:
    _league_id, session_id, _players = _live_session(client)
    recommendations = client.get(f"/api/v1/draft-sessions/{session_id}/recommendations").json()
    candidate_ids = [candidate["player_id"] for candidate in recommendations["candidates"]]
    response = client.post(
        f"/api/v1/draft-sessions/{session_id}/simulations",
        json={
            "expected_sequence": 1,
            "player_ids": candidate_ids,
            "playouts": 100,
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["status"] in {"queued", "running", "ready"}
    run_id = result["run_id"]
    for _ in range(100):
        result = client.get(f"/api/v1/draft-sessions/{session_id}/computations/{run_id}").json()
        if result["status"] not in {"queued", "running"}:
            break
        time.sleep(0.02)
    assert result["status"] == "ready"
    assert result["playouts"] == 100
    assert {row["player_id"] for row in result["results"]} == set(candidate_ids)
    assert all(row["completed_playouts"] == 100 for row in result["results"])

    duplicate = client.post(
        f"/api/v1/draft-sessions/{session_id}/simulations",
        json={
            "expected_sequence": 1,
            "player_ids": candidate_ids,
            "playouts": 100,
        },
    ).json()
    assert duplicate["run_id"] == run_id
    assert duplicate["results"] == result["results"]


def test_startup_marks_unfinished_computations_interrupted(client) -> None:
    _league_id, session_id, _players = _live_session(client)
    db = SessionLocal()
    try:
        run = DraftComputationRun(
            session_id=session_id,
            kind="simulation",
            status="running",
            session_sequence=1,
            input_hash="a" * 64,
            request_json="{}",
        )
        db.add(run)
        db.commit()
        run_id = run.id
        assert recover_interrupted_computations(db) == 1
    finally:
        db.close()
    payload = client.get(f"/api/v1/draft-sessions/{session_id}/computations/{run_id}").json()
    assert payload["status"] == "unavailable"
    assert payload["reason"] == "application_restarted"


def test_exposure_uses_canonical_athlete_and_stays_informational(client) -> None:
    league_id, session_id, players = _live_session(client)
    other_league = client.post(
        "/api/v1/leagues",
        json={"name": f"Other {uuid4()}", "season": 2026},
    ).json()
    other_player = client.post(
        f"/api/v1/leagues/{other_league['id']}/players",
        json={
            "source_id": "other-same-athlete",
            "name": "Scenario Player 0",
            "pro_team": "CHI",
            "position": "RB",
            "rostered_by": "Other roster",
            "ownership": "TEAM",
        },
    ).json()

    from app.db import SessionLocal
    from app.models import Player

    db = SessionLocal()
    try:
        canonical = db.get(Player, players[0])
        duplicate = db.get(Player, other_player["id"])
        assert canonical is not None and duplicate is not None
        duplicate.athlete_id = canonical.athlete_id
        db.commit()
    finally:
        db.close()

    response = client.get(f"/api/v1/draft-sessions/{session_id}/exposure")
    assert response.status_code == 200
    exposure = response.json()
    player = next(row for row in exposure["players"] if row["player_id"] == players[0])
    assert player["league_count"] == 1
    assert player["leagues"][0]["id"] == other_league["id"]
    assert player["leagues"][0]["league_player_id"] == other_player["id"]
    assert player["leagues"][0]["source_id"] == "other-same-athlete"
    assert player["informational_only"] is True
    assert exposure["strategy_enabled"] is False
    assert league_id != other_league["id"]


def test_exposure_query_count_is_constant_per_board(client) -> None:
    _league_id, session_id, _players = _live_session(client)

    select_count = 0

    def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        response = client.get(f"/api/v1/draft-sessions/{session_id}/exposure")
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)

    assert response.status_code == 200
    assert len(response.json()["players"]) == 12
    assert select_count <= 5
