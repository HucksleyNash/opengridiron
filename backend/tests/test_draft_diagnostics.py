from __future__ import annotations

from uuid import uuid4


def test_diagnostics_are_versioned_and_redacted(client) -> None:
    league = client.post(
        "/api/v1/leagues",
        json={"name": f"Diagnostics {uuid4()}", "season": 2026},
    ).json()
    client.post(
        f"/api/v1/leagues/{league['id']}/players",
        json={
            "source_id": "diagnostic-player",
            "name": "Diagnostic Player",
            "pro_team": "CHI",
            "position": "RB",
            "projected_points": 200,
        },
    )
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

    response = client.get(f"/api/v1/draft-sessions/{session['id']}/diagnostics")
    assert response.status_code == 200
    diagnostics = response.json()
    assert diagnostics["schema_version"] == "draft-diagnostics-v1"
    assert diagnostics["session"]["current_sequence"] == 1
    assert diagnostics["counts"]["events"] == 1
    assert diagnostics["redaction"] == {
        "secrets_included": False,
        "provider_payloads_included": False,
        "notes_included": False,
    }
    serialized = response.text.lower()
    assert "cookie" not in serialized
    assert "access_token" not in serialized
