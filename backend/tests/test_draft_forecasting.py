import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.db import SessionLocal
from app.draft import forecasting
from app.draft.forecasting import availability_guidance, next_turn_owner_pick
from app.draft.models import DraftRecommendationSnapshot
from test_draft_mock import _automatic_mock_fixture


def test_next_turn_skips_the_owner_pick_currently_on_clock() -> None:
    assert next_turn_owner_pick(0, team_count=8, owner_slot=1, total=16) == 16
    assert next_turn_owner_pick(94, team_count=12, owner_slot=2, total=192) == 98


def test_next_turn_is_absent_after_the_owners_final_upcoming_pick() -> None:
    assert next_turn_owner_pick(190, team_count=12, owner_slot=2, total=192) is None


@pytest.mark.parametrize("paused", [False, True])
def test_live_rankings_unlock_next_turn_without_changing_picks_or_history(
    client, monkeypatch, paused
) -> None:
    from app.draft import router

    async def unexpected_network(*args, **kwargs):
        pytest.fail("Fresh cached Yahoo data must not trigger a provider scrape")

    monkeypatch.setattr(router, "sync_scraped_leagues", unexpected_network)
    monkeypatch.setattr(router, "sync_scraped_league", unexpected_network)
    monkeypatch.setattr(router, "sync_projection_ranges", unexpected_network)
    league, _ = _automatic_mock_fixture(client, rounds=3)
    session = client.post(
        f"/api/v1/leagues/{league['id']}/draft-sessions",
        json={"kind": "live", "team_count": 8, "round_count": 3, "owner_team_slot": 2},
    ).json()
    session_id = session["id"]
    session = client.post(
        f"/api/v1/draft-sessions/{session_id}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": str(uuid4())},
    ).json()["session"]
    initial = client.get(f"/api/v1/draft-sessions/{session_id}/recommendations").json()
    session = client.post(
        f"/api/v1/draft-sessions/{session_id}/events",
        json={
            "type": "pick_recorded",
            "expected_sequence": session["current_sequence"],
            "idempotency_key": str(uuid4()),
            "player_id": initial["candidates"][0]["player_id"],
        },
    ).json()["session"]
    if paused:
        session = client.post(
            f"/api/v1/draft-sessions/{session_id}/actions/pause",
            json={
                "expected_sequence": session["current_sequence"],
                "idempotency_key": str(uuid4()),
            },
        ).json()["session"]
    before = client.get(f"/api/v1/draft-sessions/{session_id}/recommendations").json()
    assert before["candidates"][0]["next_turn"]["status"] == "unavailable"
    board_before = client.get(f"/api/v1/draft-sessions/{session_id}/board").json()

    response = client.post(
        f"/api/v1/leagues/{league['id']}/draft-rankings/sync",
        json={"session_id": session_id, "expected_sequence": session["current_sequence"]},
    )
    assert response.status_code == 200, response.text
    synced = response.json()["session"]
    assert synced["status"] == session["status"]
    assert synced["projection_snapshot_id"] == session["projection_snapshot_id"]
    assert synced["current_sequence"] == session["current_sequence"] + 1
    assert synced["ranking_snapshot_id"] is not None
    after = client.get(f"/api/v1/draft-sessions/{session_id}/recommendations").json()
    assert after["snapshot_id"] != before["snapshot_id"]
    assert after["forecast_status"] == "ordinal"
    assert all(c["next_turn"]["status"] == "ordinal" for c in after["candidates"])
    assert all(c["next_turn"]["label"].endswith("pick 15") for c in after["candidates"])
    board_after = client.get(f"/api/v1/draft-sessions/{session_id}/board").json()
    assert board_after["picks"] == board_before["picks"]
    with SessionLocal() as db:
        historical = db.get(DraftRecommendationSnapshot, before["snapshot_id"])
        assert json.loads(historical.candidates_json) == before["candidates"]
        assert historical.ranking_snapshot_id is None

    repeated = client.post(
        f"/api/v1/leagues/{league['id']}/draft-rankings/sync",
        json={"session_id": session_id, "expected_sequence": synced["current_sequence"]},
    )
    assert repeated.status_code == 200
    assert repeated.json()["session"]["current_sequence"] == synced["current_sequence"]
    stale = client.post(
        f"/api/v1/leagues/{league['id']}/draft-rankings/sync",
        json={"session_id": session_id, "expected_sequence": session["current_sequence"]},
    )
    assert stale.status_code == 409

    if not paused:
        picked = client.post(
            f"/api/v1/draft-sessions/{session_id}/events",
            json={
                "type": "pick_recorded",
                "expected_sequence": synced["current_sequence"],
                "idempotency_key": str(uuid4()),
                "player_id": after["candidates"][0]["player_id"],
                "recommendation_snapshot_id": after["snapshot_id"],
            },
        )
        assert picked.status_code == 201
        advanced = client.get(f"/api/v1/draft-sessions/{session_id}/recommendations").json()
        assert all(c["next_turn"]["label"].endswith("pick 18") for c in advanced["candidates"])


@pytest.mark.parametrize(
    ("condition", "reason", "status"),
    [
        ("missing", "ranking_missing", "unavailable"),
        ("partial", "ranking_incomplete", "unavailable"),
        ("coverage", "ranking_coverage", "unavailable"),
        ("stale", "ranking_stale", "stale"),
        ("disabled", "forecast_disabled", "unavailable"),
        ("unranked", "player_unranked", "unavailable"),
    ],
)
def test_forecast_reports_the_actual_unavailability_reason(monkeypatch, condition, reason, status):
    monkeypatch.setattr(
        forecasting, "settings", SimpleNamespace(draft_forecast_enabled=condition != "disabled")
    )
    ranking = SimpleNamespace(
        status="partial" if condition == "partial" else "ready",
        canonical_coverage=0.8 if condition == "coverage" else 1.0,
        retrieved_at=datetime.now(UTC) - timedelta(hours=49 if condition == "stale" else 1),
        rows_json=json.dumps([] if condition == "unranked" else [{"player_id": 1, "adp": 20}]),
        metadata_json="{}",
    )
    session = SimpleNamespace(
        ranking_snapshot_id=None if condition == "missing" else 1,
        team_count=8,
        round_count=3,
        owner_team_slot=1,
    )
    result = availability_guidance(
        SimpleNamespace(get=lambda *_: ranking), session, [], player_ids=[1]
    )[1]
    assert result["reason"] == reason
    assert result["status"] == status
    assert "passes its gate" not in result["label"]


def test_final_owner_turn_does_not_require_rankings():
    session = SimpleNamespace(
        ranking_snapshot_id=None,
        team_count=8,
        round_count=1,
        owner_team_slot=1,
    )
    result = availability_guidance(None, session, [], player_ids=[1])[1]
    assert result["reason"] == "no_later_owner_pick"
    assert result["label"] == "No later owner pick in this draft"


@pytest.mark.parametrize("outcome", ["ready", "rate_limit", "partial", "changed"])
def test_live_ranking_refresh_is_league_scoped_and_sequence_safe(client, monkeypatch, outcome):
    from app.draft import router
    from app.draft.ranking_sources import latest_scrape_snapshot
    from app.draft.schemas import DraftActionRequest
    from app.draft.session import transition_session
    from app.models import League
    from app.services.yahoo_scraper import YahooScraperRateLimited

    league, _ = _automatic_mock_fixture(client)
    session = client.post(
        f"/api/v1/leagues/{league['id']}/draft-sessions",
        json={"kind": "live", "team_count": 8, "round_count": 2, "owner_team_slot": 2},
    ).json()
    session_id = session["id"]
    client.post(
        f"/api/v1/draft-sessions/{session_id}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": str(uuid4())},
    )
    calls = []

    async def refresh_league(db, league_id):
        calls.append(league_id)
        if outcome == "rate_limit":
            raise YahooScraperRateLimited(120)
        if outcome == "partial":
            source = latest_scrape_snapshot(db, db.get(League, league_id))
            payload = json.loads(source.payload_json)
            payload["player_pool_complete"] = False
            source.payload_json = json.dumps(payload)
            db.commit()
        if outcome == "changed":
            with SessionLocal() as concurrent:
                transition_session(
                    concurrent,
                    session_id,
                    "pause",
                    DraftActionRequest(expected_sequence=1, idempotency_key=str(uuid4())),
                )

    monkeypatch.setattr(router, "sync_scraped_league", refresh_league)
    response = client.post(
        f"/api/v1/leagues/{league['id']}/draft-rankings/sync",
        json={"session_id": session_id, "expected_sequence": 1, "refresh": True},
    )
    assert calls == [league["id"]]
    if outcome == "ready":
        assert response.status_code == 200, response.text
        assert response.json()["session"]["status"] == "LIVE"
    else:
        code, error = {
            "rate_limit": (429, "yahoo_rate_limited"),
            "partial": (422, "ranking_incomplete"),
            "changed": (409, "draft_conflict"),
        }[outcome]
        assert response.status_code == code, response.text
        assert response.json()["error"] == error
        current = client.get(f"/api/v1/draft-sessions/{session_id}").json()
        assert current["ranking_snapshot_id"] is None
        assert current["current_sequence"] == (2 if outcome == "changed" else 1)
