from __future__ import annotations


def test_small_draft_fixture_is_deterministic_and_snake_ordered(client) -> None:
    response = client.get(
        "/api/v1/testing/draft-fixtures/small",
        params={"now": "2026-08-30T20:00:00Z", "source_state": "healthy"},
    )

    assert response.status_code == 200
    fixture = response.json()
    assert fixture["fixture_version"] == 1
    assert fixture["clock"]["now"] == "2026-08-30T20:00:00+00:00"
    assert fixture["session"] == {
        "id": 7001,
        "kind": "mock",
        "format": "snake",
        "status": "LIVE",
        "team_count": 8,
        "round_count": 4,
        "owner_team_slot": 3,
        "current_sequence": 3,
        "source_mode": "yahoo_scrape_shadow",
    }
    assert [event["team_slot"] for event in fixture["events"]] == [1, 2]
    assert fixture["yahoo_observations"][-1]["result"] == "proposal"
    assert len(fixture["players"]) == 32


def test_fixture_clock_and_outage_are_controllable(client) -> None:
    response = client.get(
        "/api/v1/testing/draft-fixtures/standard",
        params={"now": "2026-09-01T01:02:03Z", "source_state": "outage"},
    )

    assert response.status_code == 200
    fixture = response.json()
    assert fixture["clock"]["now"] == "2026-09-01T01:02:03+00:00"
    assert fixture["source"]["state"] == "outage"
    assert fixture["source"]["error"] == "Yahoo fixture outage"
    assert fixture["session"]["team_count"] == 12
    assert fixture["session"]["round_count"] == 16
