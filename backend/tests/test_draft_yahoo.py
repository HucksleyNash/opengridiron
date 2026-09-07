from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4


def _draft(client) -> tuple[int, list[int]]:
    league = client.post(
        "/api/v1/leagues",
        json={
            "name": f"Yahoo shadow {uuid4()}",
            "season": 2026,
            "roster_slots": ["QB", "RB", "WR", "TE", "FLEX"],
        },
    ).json()
    player_ids = []
    for index in range(8):
        player = client.post(
            f"/api/v1/leagues/{league['id']}/players",
            json={
                "source_id": f"yahoo.p.{index + 1}",
                "name": f"Yahoo Player {index + 1}",
                "pro_team": "CHI",
                "position": ["RB", "WR", "QB", "TE"][index % 4],
                "projected_points": 250 - index * 8,
                "ros_value": 25 - index,
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
            "source_mode": "yahoo_scrape_shadow",
        },
    ).json()
    started = client.post(
        f"/api/v1/draft-sessions/{session['id']}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": f"start-{uuid4()}"},
    )
    assert started.status_code == 200
    return session["id"], player_ids


def test_yahoo_shadow_confirms_exact_and_proposes_missing_pick(client) -> None:
    session_id, player_ids = _draft(client)
    first = client.post(
        f"/api/v1/draft-sessions/{session_id}/events",
        json={
            "type": "pick_recorded",
            "expected_sequence": 1,
            "idempotency_key": "manual-first",
            "player_id": player_ids[0],
        },
    )
    assert first.status_code == 201

    sync = client.post(
        f"/api/v1/draft-sessions/{session_id}/sync/yahoo",
        json={
            "expected_sequence": 2,
            "fixture_observations": [
                {
                    "provider_key": "yahoo-pick-1",
                    "overall_pick": 1,
                    "team_slot": 1,
                    "player_id": player_ids[0],
                },
                {
                    "provider_key": "yahoo-pick-2",
                    "overall_pick": 2,
                    "team_slot": 2,
                    "player_id": player_ids[1],
                },
            ],
        },
    )
    assert sync.status_code == 200
    assert sync.json()["counts"] == {"applied": 0, "confirmed": 1, "proposed": 1}
    assert sync.json()["current_sequence"] == 3

    board = client.get(f"/api/v1/draft-sessions/{session_id}/board").json()
    assert board["completed_picks"] == 1
    conflicts = client.get(f"/api/v1/draft-sessions/{session_id}/conflicts").json()
    assert len(conflicts) == 1
    assert conflicts[0]["status"] == "unresolved"
    assert conflicts[0]["incoming"]["player_id"] == player_ids[1]

    accepted = client.post(
        f"/api/v1/draft-sessions/{session_id}/conflicts/{conflicts[0]['id']}/resolve",
        json={"expected_sequence": 3, "action": "accept_incoming"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["current_sequence"] == 4
    board = client.get(f"/api/v1/draft-sessions/{session_id}/board").json()
    assert [pick["player_id"] for pick in board["picks"]] == player_ids[:2]


def test_resolved_yahoo_conflict_reopens_after_canonical_pick_changes(client) -> None:
    session_id, player_ids = _draft(client)
    recorded = client.post(
        f"/api/v1/draft-sessions/{session_id}/events",
        json={
            "type": "pick_recorded",
            "expected_sequence": 1,
            "idempotency_key": "manual-original",
            "player_id": player_ids[0],
        },
    )
    assert recorded.status_code == 201

    def synchronize(revision: str, player_id: int, expected_sequence: int):
        return client.post(
            f"/api/v1/draft-sessions/{session_id}/sync/yahoo",
            json={
                "expected_sequence": expected_sequence,
                "fixture_observations": [
                    {
                        "provider_key": "stable-yahoo-pick-1",
                        "provider_revision": revision,
                        "overall_pick": 1,
                        "team_slot": 1,
                        "player_id": player_id,
                    }
                ],
            },
        )

    first_proposal = synchronize("1", player_ids[1], 2)
    first_conflict_id = first_proposal.json()["proposal_ids"][0]
    first_resolution = client.post(
        f"/api/v1/draft-sessions/{session_id}/conflicts/{first_conflict_id}/resolve",
        json={"expected_sequence": 2, "action": "accept_incoming"},
    )
    assert first_resolution.status_code == 200
    assert first_resolution.json()["current_sequence"] == 4

    second_proposal = synchronize("2", player_ids[0], 4)
    second_conflict_id = second_proposal.json()["proposal_ids"][0]
    second_resolution = client.post(
        f"/api/v1/draft-sessions/{session_id}/conflicts/{second_conflict_id}/resolve",
        json={"expected_sequence": 4, "action": "accept_incoming"},
    )
    assert second_resolution.status_code == 200
    assert second_resolution.json()["current_sequence"] == 6

    repeated_original = synchronize("1", player_ids[1], 6)

    assert repeated_original.status_code == 200
    assert repeated_original.json()["proposal_ids"] == [first_conflict_id]
    conflicts = client.get(f"/api/v1/draft-sessions/{session_id}/conflicts").json()
    reopened = next(item for item in conflicts if item["id"] == first_conflict_id)
    assert reopened["status"] == "unresolved"
    assert reopened["resolution_action"] is None
    assert reopened["resolved_at"] is None

    repeated_resolution = client.post(
        f"/api/v1/draft-sessions/{session_id}/conflicts/{first_conflict_id}/resolve",
        json={"expected_sequence": 6, "action": "accept_incoming"},
    )
    assert repeated_resolution.status_code == 200
    assert repeated_resolution.json()["current_sequence"] == 8


def test_previous_confirmation_does_not_hide_later_canonical_mismatch(client) -> None:
    session_id, player_ids = _draft(client)
    recorded = client.post(
        f"/api/v1/draft-sessions/{session_id}/events",
        json={
            "type": "pick_recorded",
            "expected_sequence": 1,
            "idempotency_key": "manual-confirmed-original",
            "player_id": player_ids[0],
        },
    )
    assert recorded.status_code == 201

    def synchronize(revision: str, player_id: int, expected_sequence: int):
        return client.post(
            f"/api/v1/draft-sessions/{session_id}/sync/yahoo",
            json={
                "expected_sequence": expected_sequence,
                "fixture_observations": [
                    {
                        "provider_key": "confirmed-yahoo-pick-1",
                        "provider_revision": revision,
                        "overall_pick": 1,
                        "team_slot": 1,
                        "player_id": player_id,
                    }
                ],
            },
        )

    confirmed = synchronize("1", player_ids[0], 2)
    assert confirmed.json()["counts"] == {"applied": 0, "confirmed": 1, "proposed": 0}
    assert confirmed.json()["current_sequence"] == 3

    replacement = synchronize("2", player_ids[1], 3)
    replacement_conflict_id = replacement.json()["proposal_ids"][0]
    accepted = client.post(
        f"/api/v1/draft-sessions/{session_id}/conflicts/{replacement_conflict_id}/resolve",
        json={"expected_sequence": 3, "action": "accept_incoming"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["current_sequence"] == 5

    repeated_confirmation = synchronize("1", player_ids[0], 5)

    assert repeated_confirmation.status_code == 200
    assert repeated_confirmation.json()["counts"] == {
        "applied": 0,
        "confirmed": 0,
        "proposed": 1,
    }
    conflicts = client.get(f"/api/v1/draft-sessions/{session_id}/conflicts").json()
    unresolved = [item for item in conflicts if item["status"] == "unresolved"]
    assert len(unresolved) == 1
    assert unresolved[0]["incoming"]["player_id"] == player_ids[0]


def test_live_scraper_sync_targets_only_the_session_league(client, monkeypatch) -> None:
    from app.draft import router

    session_id, _player_ids = _draft(client)
    session = client.get(f"/api/v1/draft-sessions/{session_id}").json()
    synchronized_leagues: list[int] = []

    async def sync_one(_db, league_id: int) -> dict[str, int]:
        synchronized_leagues.append(league_id)
        return {"resources": 1, "draft_picks": 0}

    monkeypatch.setattr(router, "sync_scraped_draft_results", sync_one)

    response = client.post(
        f"/api/v1/draft-sessions/{session_id}/sync/yahoo",
        json={"expected_sequence": session["current_sequence"]},
    )

    assert response.status_code == 200
    assert synchronized_leagues == [session["league_id"]]
    assert response.json()["counts"] == {"applied": 0, "confirmed": 0, "proposed": 0}


def test_live_scraper_rate_limit_returns_typed_retry(client, monkeypatch) -> None:
    from app.draft import router
    from app.services.yahoo_scraper import YahooScraperRateLimited

    session_id, _player_ids = _draft(client)
    session = client.get(f"/api/v1/draft-sessions/{session_id}").json()

    async def blocked(_db, _league_id: int) -> dict[str, int]:
        raise YahooScraperRateLimited(123)

    monkeypatch.setattr(router, "sync_scraped_draft_results", blocked)

    response = client.post(
        f"/api/v1/draft-sessions/{session_id}/sync/yahoo",
        json={"expected_sequence": session["current_sequence"]},
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": "yahoo_rate_limited",
        "message": (
            "Yahoo temporarily blocked automated requests. Live Yahoo sync is paused for about "
            "3 minutes; keep recording picks manually."
        ),
        "retry_after_seconds": 123,
    }


def test_yahoo_authority_requires_passing_fifty_pick_evidence(client) -> None:
    session_id, _player_ids = _draft(client)
    from app.db import SessionLocal
    from app.draft.models import DraftSession, YahooAuthorityEvidence

    db = SessionLocal()
    try:
        session = db.get(DraftSession, session_id)
        assert session is not None
        evidence = YahooAuthorityEvidence(
            league_id=session.league_id,
            transport="scrape",
            provider_version="fixture-v1",
            controlled_draft_id="rehearsal-1",
            first_observed_at=datetime.now(UTC),
            last_observed_at=datetime.now(UTC),
            consecutive_pick_count=49,
            passed=True,
        )
        db.add(evidence)
        db.commit()
        db.refresh(evidence)
        evidence_id = evidence.id
    finally:
        db.close()

    rejected = client.post(
        f"/api/v1/draft-sessions/{session_id}/source-mode",
        json={
            "expected_sequence": 1,
            "mode": "yahoo_scrape_authoritative",
            "authority_evidence_id": evidence_id,
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"] == "authority_evidence_required"

    db = SessionLocal()
    try:
        evidence = db.get(YahooAuthorityEvidence, evidence_id)
        assert evidence is not None
        evidence.consecutive_pick_count = 50
        db.commit()
    finally:
        db.close()

    accepted = client.post(
        f"/api/v1/draft-sessions/{session_id}/source-mode",
        json={
            "expected_sequence": 1,
            "mode": "yahoo_scrape_authoritative",
            "authority_evidence_id": evidence_id,
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["source_mode"] == "yahoo_scrape_authoritative"
    assert accepted.json()["current_sequence"] == 2


def test_owner_can_promote_validated_shadow_feed_to_safe_auto_approve(client) -> None:
    session_id, player_ids = _draft(client)

    unconfirmed = client.post(
        f"/api/v1/draft-sessions/{session_id}/source-mode",
        json={
            "expected_sequence": 1,
            "mode": "yahoo_scrape_authoritative",
        },
    )
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["error"] == "authority_evidence_required"

    promoted = client.post(
        f"/api/v1/draft-sessions/{session_id}/source-mode",
        json={
            "expected_sequence": 1,
            "mode": "yahoo_scrape_authoritative",
            "owner_confirmed": True,
        },
    )
    assert promoted.status_code == 200
    assert promoted.json()["source_mode"] == "yahoo_scrape_authoritative"
    assert promoted.json()["current_sequence"] == 2

    synchronized = client.post(
        f"/api/v1/draft-sessions/{session_id}/sync/yahoo",
        json={
            "expected_sequence": 2,
            "fixture_observations": [
                {
                    "provider_key": "auto-pick-1",
                    "overall_pick": 1,
                    "team_slot": 1,
                    "player_id": player_ids[0],
                },
                {
                    "provider_key": "auto-pick-2",
                    "overall_pick": 2,
                    "team_slot": 2,
                    "player_id": player_ids[1],
                },
                {
                    "provider_key": "out-of-order-pick-4",
                    "overall_pick": 4,
                    "team_slot": 4,
                    "player_id": player_ids[3],
                },
            ],
        },
    )
    assert synchronized.status_code == 200
    assert synchronized.json()["counts"] == {"applied": 2, "confirmed": 0, "proposed": 1}

    board = client.get(f"/api/v1/draft-sessions/{session_id}/board").json()
    assert [pick["player_id"] for pick in board["picks"]] == player_ids[:2]
    conflicts = client.get(f"/api/v1/draft-sessions/{session_id}/conflicts").json()
    assert len([item for item in conflicts if item["status"] == "unresolved"]) == 1


def test_owner_confirmation_requires_matching_shadow_mode(client) -> None:
    session_id, _player_ids = _draft(client)
    manual = client.post(
        f"/api/v1/draft-sessions/{session_id}/source-mode",
        json={"expected_sequence": 1, "mode": "manual"},
    )
    assert manual.status_code == 200

    rejected = client.post(
        f"/api/v1/draft-sessions/{session_id}/source-mode",
        json={
            "expected_sequence": 2,
            "mode": "yahoo_scrape_authoritative",
            "owner_confirmed": True,
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"] == "shadow_validation_required"


def test_auto_approve_resolves_prior_proposal_and_completes_full_board(client) -> None:
    session_id, player_ids = _draft(client)
    observations = [
        {
            "provider_key": f"full-auto-pick-{index + 1}",
            "overall_pick": index + 1,
            "team_slot": index + 1,
            "player_id": player_id,
        }
        for index, player_id in enumerate(player_ids)
    ]

    proposed = client.post(
        f"/api/v1/draft-sessions/{session_id}/sync/yahoo",
        json={"expected_sequence": 1, "fixture_observations": observations[:1]},
    )
    assert proposed.status_code == 200
    assert proposed.json()["counts"]["proposed"] == 1

    promoted = client.post(
        f"/api/v1/draft-sessions/{session_id}/source-mode",
        json={
            "expected_sequence": 1,
            "mode": "yahoo_scrape_authoritative",
            "owner_confirmed": True,
        },
    )
    assert promoted.status_code == 200

    synchronized = client.post(
        f"/api/v1/draft-sessions/{session_id}/sync/yahoo",
        json={"expected_sequence": 2, "fixture_observations": observations},
    )
    assert synchronized.status_code == 200
    assert synchronized.json()["counts"] == {"applied": 8, "confirmed": 0, "proposed": 0}

    session = client.get(f"/api/v1/draft-sessions/{session_id}").json()
    assert session["status"] == "COMPLETE"
    conflicts = client.get(f"/api/v1/draft-sessions/{session_id}/conflicts").json()
    assert conflicts[0]["status"] == "resolved"
    assert conflicts[0]["resolution_action"] == "auto_applied"
