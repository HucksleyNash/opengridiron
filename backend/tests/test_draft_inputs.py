from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.db import SessionLocal
from app.draft.models import DraftInputPreview
from app.services import providers


def _league(client) -> tuple[int, list[dict]]:
    league = client.post(
        "/api/v1/leagues",
        json={
            "name": f"Draft inputs {uuid4()}",
            "season": 2026,
            "roster_slots": ["QB", "RB", "WR", "TE", "FLEX"],
        },
    ).json()
    players = []
    for index in range(8):
        players.append(
            client.post(
                f"/api/v1/leagues/{league['id']}/players",
                json={
                    "source_id": f"vendor-{index + 1}",
                    "name": f"Input Player {index + 1}",
                    "pro_team": "CHI",
                    "position": ["RB", "WR", "QB", "TE"][index % 4],
                    "projected_points": 100,
                },
            ).json()
        )
    return league["id"], players


def _csv(players: list[dict]) -> bytes:
    stream = io.StringIO()
    writer = csv.DictWriter(
        stream,
        fieldnames=[
            "player_id",
            "projected_points",
            "floor",
            "ceiling",
            "risk",
            "adp",
        ],
    )
    writer.writeheader()
    for index, player in enumerate(players):
        writer.writerow(
            {
                "player_id": player["id"],
                "projected_points": 260 - index * 9,
                "floor": 210 - index * 7,
                "ceiling": 300 - index * 7,
                "risk": 0.1 + index * 0.02,
                "adp": 3 + index * 10,
            }
        )
    return stream.getvalue().encode()


def test_input_preview_commit_and_binding_are_immutable(client) -> None:
    league_id, players = _league(client)
    preview_response = client.post(
        f"/api/v1/leagues/{league_id}/draft-inputs/preview",
        params={"input_type": "combined"},
        files={"file": ("draft.csv", _csv(players), "text/csv")},
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["can_commit"] is True
    assert preview["row_count"] == 8
    assert preview["blocking_errors"] == []
    assert preview["warnings"]

    warning_gate = client.post(
        f"/api/v1/leagues/{league_id}/draft-inputs/previews/{preview['preview_id']}/commit",
        json={"content_hash": preview["content_hash"]},
    )
    assert warning_gate.status_code == 422
    assert warning_gate.json()["error"] == "input_warning_acknowledgement_required"

    committed_response = client.post(
        f"/api/v1/leagues/{league_id}/draft-inputs/previews/{preview['preview_id']}/commit",
        json={
            "content_hash": preview["content_hash"],
            "acknowledge_warnings": True,
        },
    )
    assert committed_response.status_code == 200
    committed = committed_response.json()
    assert committed["projection_snapshot_id"]
    assert committed["ranking_snapshot_id"]

    committed_retry = client.post(
        f"/api/v1/leagues/{league_id}/draft-inputs/previews/{preview['preview_id']}/commit",
        json={
            "content_hash": preview["content_hash"],
            "acknowledge_warnings": True,
        },
    )
    assert committed_retry.status_code == 200
    assert committed_retry.json() == committed

    session = client.post(
        f"/api/v1/leagues/{league_id}/draft-sessions",
        json={
            "kind": "mock",
            "team_count": 8,
            "round_count": 2,
            "owner_team_slot": 1,
        },
    ).json()
    bound = client.post(
        f"/api/v1/draft-sessions/{session['id']}/input-snapshots",
        json={
            "expected_sequence": 0,
            "projection_snapshot_id": committed["projection_snapshot_id"],
            "ranking_snapshot_id": committed["ranking_snapshot_id"],
        },
    )
    assert bound.status_code == 200
    assert bound.json()["projection_snapshot_id"] == committed["projection_snapshot_id"]
    assert bound.json()["ranking_snapshot_id"] == committed["ranking_snapshot_id"]

    started = client.post(
        f"/api/v1/draft-sessions/{session['id']}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": f"start-{uuid4()}"},
    )
    assert started.status_code == 200
    recommendations = client.get(f"/api/v1/draft-sessions/{session['id']}/recommendations").json()
    assert recommendations["candidates"]
    assert recommendations["candidates"][0]["next_turn"]["status"] == "ordinal"
    assert recommendations["next_owner_pick"] == 1
    assert recommendations["candidates"][0]["next_turn"]["label"].endswith("pick 16")


def test_league_scoring_rules_drive_draft_values_and_analyst_context(client) -> None:
    league = client.post(
        "/api/v1/leagues",
        json={
            "name": f"Scoring context {uuid4()}",
            "season": 2026,
            "scoring": {"rushing_yards": 0.1, "receiving_yards": 0.1},
            "roster_slots": ["RB", "WR"],
        },
    ).json()
    running_back = client.post(
        f"/api/v1/leagues/{league['id']}/players",
        json={
            "source_id": f"scoring-rb-{uuid4()}",
            "name": "Rule Scored Runner",
            "pro_team": "CHI",
            "position": "RB",
        },
    ).json()
    receiver = client.post(
        f"/api/v1/leagues/{league['id']}/players",
        json={
            "source_id": f"scoring-wr-{uuid4()}",
            "name": "Rule Scored Receiver",
            "pro_team": "GB",
            "position": "WR",
        },
    ).json()
    raw_projection = json.dumps(
        [
            {
                "player_id": running_back["id"],
                "raw_stats": {"rushing_yards": 1000},
                "risk": 0.2,
            },
            {
                "player_id": receiver["id"],
                "raw_stats": {"receiving_yards": 800},
                "risk": 0.2,
            },
        ]
    ).encode()
    preview = client.post(
        f"/api/v1/leagues/{league['id']}/draft-inputs/preview",
        params={"input_type": "projection"},
        files={"file": ("raw-projections.json", raw_projection, "application/json")},
    ).json()
    assert preview["blocking_errors"] == []
    assert preview["warnings"] == []
    assert [row["projected_points"] for row in preview["sample"]] == [100.0, 80.0]
    committed = client.post(
        f"/api/v1/leagues/{league['id']}/draft-inputs/previews/{preview['preview_id']}/commit",
        json={"content_hash": preview["content_hash"]},
    ).json()

    def start_session() -> tuple[dict, dict]:
        session = client.post(
            f"/api/v1/leagues/{league['id']}/draft-sessions",
            json={
                "kind": "mock",
                "team_count": 8,
                "round_count": 1,
                "owner_team_slot": 1,
            },
        ).json()
        bound = client.post(
            f"/api/v1/draft-sessions/{session['id']}/input-snapshots",
            json={
                "expected_sequence": 0,
                "projection_snapshot_id": committed["projection_snapshot_id"],
            },
        ).json()
        started = client.post(
            f"/api/v1/draft-sessions/{session['id']}/actions/start",
            json={"expected_sequence": 0, "idempotency_key": f"start-{uuid4()}"},
        )
        assert started.status_code == 200
        recommendations = client.get(
            f"/api/v1/draft-sessions/{session['id']}/recommendations"
        ).json()
        return bound, recommendations

    ten_yard_session, ten_yard_recommendations = start_session()
    top_ten_yard = ten_yard_recommendations["candidates"][0]
    assert top_ten_yard["name"] == "Rule Scored Runner"
    assert top_ten_yard["projected_points"] == 100.0
    assert top_ten_yard["scoring_source"] == "league_rules_recomputed"
    assert top_ten_yard["scoring_breakdown"] == {"rushing_yards": 100.0}
    assert "league-scored points" in top_ten_yard["why_now"]
    assert (
        ten_yard_recommendations["freshness"]["league_scoring"]["yardage_rates"]["rushing_yards"][
            "yards_per_point"
        ]
        == 10.0
    )

    updated = client.put(
        f"/api/v1/leagues/{league['id']}",
        json={
            "name": league["name"],
            "season": league["season"],
            "scoring": {"rushing_yards": 1 / 15, "receiving_yards": 0.1},
            "roster_slots": ["RB", "WR"],
        },
    )
    assert updated.status_code == 200

    _fifteen_yard_session, fifteen_yard_recommendations = start_session()
    assert fifteen_yard_recommendations["candidates"][0]["name"] == "Rule Scored Receiver"
    runner = next(
        item
        for item in [
            *fifteen_yard_recommendations["candidates"],
            *fifteen_yard_recommendations["alternatives"],
        ]
        if item["name"] == "Rule Scored Runner"
    )
    assert runner["projected_points"] == 66.67
    assert (
        fifteen_yard_recommendations["freshness"]["league_scoring"]["yardage_rates"][
            "rushing_yards"
        ]["yards_per_point"]
        == 15.0
    )

    with SessionLocal() as db:
        dossier = providers.build_dossier(
            db,
            league_id=league["id"],
            pool_id=None,
            draft_session_id=ten_yard_session["id"],
        )
    assert dossier["league"]["rules_source"] == "draft_session_snapshot"
    assert dossier["league"]["scoring"]["rushing_yards"] == 0.1
    draft_context = dossier["league"]["draft_context"]
    assert draft_context["league_rules"]["scoring"]["yardage_rates"]["rushing_yards"] == {
        "points_per_yard": 0.1,
        "yards_per_point": 10.0,
    }
    analyst_runner = next(
        item for item in draft_context["player_pool"] if item["name"] == "Rule Scored Runner"
    )
    assert analyst_runner["projection"] == 100.0
    assert analyst_runner["scoring_source"] == "league_rules_recomputed"


def test_preview_never_silently_name_matches_an_unidentified_row(client) -> None:
    league_id, _players = _league(client)
    content = b"name,projected_points,adp\nInput Player 1,250,4\n"
    response = client.post(
        f"/api/v1/leagues/{league_id}/draft-inputs/preview",
        params={"input_type": "combined"},
        files={"file": ("draft.csv", content, "text/csv")},
    )
    assert response.status_code == 200
    preview = response.json()
    assert preview["can_commit"] is False
    assert preview["blocking_errors"][0]["code"] == "identity_required"


def test_preview_is_durable_and_expires_with_a_typed_error(client) -> None:
    league_id, players = _league(client)
    response = client.post(
        f"/api/v1/leagues/{league_id}/draft-inputs/preview",
        params={"input_type": "combined"},
        files={"file": ("draft.csv", _csv(players), "text/csv")},
    )
    preview = response.json()
    assert preview["expires_at"]

    db = SessionLocal()
    try:
        stored = db.get(DraftInputPreview, preview["preview_id"])
        assert stored is not None
        assert stored.status == "staged"
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()

    expired = client.post(
        f"/api/v1/leagues/{league_id}/draft-inputs/previews/{preview['preview_id']}/commit",
        json={
            "content_hash": preview["content_hash"],
            "acknowledge_warnings": True,
        },
    )
    assert expired.status_code == 410
    assert expired.json()["error"] == "input_preview_expired"
