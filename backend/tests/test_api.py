from __future__ import annotations

from fastapi.testclient import TestClient


def test_manual_first_workflow(client: TestClient) -> None:
    status = client.get("/api/v1/onboarding/status")
    assert status.status_code == 200
    assert status.json()["configured"] is False
    assert status.json()["capabilities"]["draft_suite"] is True

    setup = client.post(
        "/api/v1/onboarding/setup",
        json={"username": "owner", "password": "correct-horse-battery-staple"},
    )
    assert setup.status_code == 201

    league = client.post(
        "/api/v1/leagues",
        json={
            "name": "Home League",
            "season": 2026,
            "scoring": {"receptions": 1},
            "roster_slots": ["QB", "RB", "WR", "FLEX", "BN"],
            "faab_budget": 100,
        },
    )
    assert league.status_code == 201
    league_id = league.json()["id"]

    for name, position, points, slot in [
        ("QB One", "QB", 20, "QB"),
        ("RB One", "RB", 15, "RB"),
        ("WR One", "WR", 17, "WR"),
        ("WR Two", "WR", 14, "BN"),
    ]:
        response = client.post(
            f"/api/v1/leagues/{league_id}/players",
            json={
                "name": name,
                "pro_team": "CHI",
                "position": position,
                "ownership": "TEAM",
                "rostered_by": "My Team",
                "current_slot": slot,
                "projected_points": points,
                "floor": points - 3,
                "ceiling": points + 4,
                "ros_value": points * 2,
                "risk": 0.2,
                "evidence": [],
            },
        )
        assert response.status_code == 201

    lineup = client.get(f"/api/v1/leagues/{league_id}/lineup")
    assert lineup.status_code == 200
    assert len(lineup.json()["assignments"]) == 4
    assert lineup.json()["projected_gain"] > 0

    scored = client.post(
        f"/api/v1/leagues/{league_id}/projections/score",
        json={
            "position": "WR",
            "history_games": 8,
            "raw_stats": {"receptions": 6, "receiving_yards": 80},
        },
    )
    assert scored.status_code == 200
    assert scored.json()["fantasy_score"] == 14

    pool = client.post(
        "/api/v1/pools",
        json={
            "name": "Sunday Survivor",
            "pool_type": "survivor",
            "season": 2026,
            "rules": {"direction": "winner", "basis": "straight_up", "max_team_uses": 1},
        },
    )
    assert pool.status_code == 201
    entry = client.post(f"/api/v1/pools/{pool.json()['id']}/entries", json={"name": "Main entry"})
    assert entry.status_code == 201

    dashboard = client.get("/api/v1/dashboard")
    assert dashboard.status_code == 200
    assert len(dashboard.json()["leagues"]) >= 1
    assert len(dashboard.json()["pools"]) == 1

    backup = client.post("/api/v1/backups")
    assert backup.status_code == 200
    temporary = client.post(
        "/api/v1/leagues",
        json={"name": "Temporary League", "season": 2026, "scoring": {}, "roster_slots": ["QB"]},
    )
    assert temporary.status_code == 201
    restored = client.post(f"/api/v1/backups/{backup.json()['filename']}/restore")
    assert restored.status_code == 200
    names = {item["name"] for item in client.get("/api/v1/leagues").json()}
    assert "Temporary League" not in names


def test_remove_provider_deletes_its_key_and_preserves_analysis_history(
    client: TestClient,
) -> None:
    from app.db import SessionLocal
    from app.models import AnalysisProvider, AnalysisRun, SecretSetting

    created = client.post(
        "/api/v1/providers",
        json={
            "name": "Removable provider",
            "provider_type": "openai",
            "model": "gpt-test",
            "api_key": "temporary-test-key",
            "task_defaults": ["chat"],
        },
    )
    assert created.status_code == 201
    provider_id = created.json()["id"]

    with SessionLocal() as db:
        provider = db.get(AnalysisProvider, provider_id)
        assert provider is not None
        setting_key = provider.api_key_setting
        run = AnalysisRun(
            task="chat",
            provider_id=provider_id,
            model="gpt-test",
            question="Which saved result should survive provider removal?",
            input_hash="remove-provider-test",
            snapshot_ids_json="[]",
            status="completed",
            output_json=(
                '{"summary":"Keep this answer","recommendations":["Keep it"],'
                '"risks":[],"missing_information":[],"citations":[]}'
            ),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id

    removed = client.delete(f"/api/v1/providers/{provider_id}")
    assert removed.status_code == 204
    assert all(item["id"] != provider_id for item in client.get("/api/v1/providers").json())

    with SessionLocal() as db:
        assert db.get(AnalysisProvider, provider_id) is None
        assert setting_key is not None
        assert db.get(SecretSetting, setting_key) is None
        preserved_run = db.get(AnalysisRun, run_id)
        assert preserved_run is not None
        assert preserved_run.provider_id is None

    history = client.get("/api/v1/analysis/runs")
    assert history.status_code == 200
    preserved_history = next(item for item in history.json() if item["id"] == run_id)
    assert preserved_history["question"] == "Which saved result should survive provider removal?"
    assert preserved_history["provider"] is None
    assert preserved_history["output"]["summary"] == "Keep this answer"

    assert client.delete(f"/api/v1/providers/{provider_id}").status_code == 404
