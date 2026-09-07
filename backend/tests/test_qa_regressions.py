from __future__ import annotations

from datetime import UTC, datetime

from app.db import SessionLocal
from app.models import Game, NewsItem, NewsSource
from app.services.news import _parse_html_items
from fastapi.testclient import TestClient


def _create_league(client: TestClient) -> int:
    response = client.post(
        "/api/v1/leagues",
        json={
            "name": "QA Regression League",
            "season": 2098,
            "scoring": {},
            "roster_slots": ["QB", "RB", "WR"],
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def _player_payload(name: str = "Regression Runner") -> dict[str, object]:
    return {
        "name": name,
        "pro_team": "CHI",
        "position": "RB",
        "ownership": "FA",
        "projected_points": 13,
        "floor": 8,
        "ceiling": 19,
        "ros_value": 28,
        "risk": 0.3,
        "evidence": [],
    }


def test_manual_inputs_reject_blank_credentials_and_invalid_games(client: TestClient) -> None:
    # Regression: ISSUE-001 and ISSUE-002, found by /qa on 2026-08-30.
    # Report: .gstack/qa-reports/qa-report-localhost-8787-2026-08-30.md
    blank_yahoo = client.post(
        "/api/v1/integrations/yahoo/settings",
        json={"client_id": "   ", "client_secret": "", "redirect_uri": "   "},
    )
    assert blank_yahoo.status_code == 422

    invalid_game = {
        "season": 2098,
        "week": 1,
        "away_team": " ",
        "home_team": " ",
        "kickoff": datetime.now(UTC).isoformat(),
    }
    assert client.post("/api/v1/games", json=invalid_game).status_code == 422
    invalid_game.update({"away_team": "CHI", "home_team": "chi"})
    assert client.post("/api/v1/games", json=invalid_game).status_code == 422


def test_duplicate_players_are_rejected_and_draft_picks_include_player_name(
    client: TestClient,
) -> None:
    # Regression: ISSUE-003 and ISSUE-006, found by /qa on 2026-08-30.
    # Report: .gstack/qa-reports/qa-report-localhost-8787-2026-08-30.md
    league_id = _create_league(client)
    created = client.post(f"/api/v1/leagues/{league_id}/players", json=_player_payload())
    assert created.status_code == 201
    duplicate = client.post(
        f"/api/v1/leagues/{league_id}/players",
        json=_player_payload("  regression   runner  "),
    )
    assert duplicate.status_code == 409

    drafted = client.post(
        f"/api/v1/leagues/{league_id}/draft",
        json={
            "overall": 1,
            "round": 1,
            "team_name": "Regression Team",
            "player_id": created.json()["id"],
        },
    )
    assert drafted.status_code == 201
    assert drafted.json()["player_name"] == "Regression Runner"


def test_confidence_recommendations_ignore_legacy_blank_games(client: TestClient) -> None:
    # Regression: ISSUE-009, found by /qa on 2026-08-30.
    # Report: .gstack/qa-reports/qa-report-localhost-8787-2026-08-30.md
    pool = client.post(
        "/api/v1/pools",
        json={
            "name": "QA Regression Confidence",
            "pool_type": "confidence",
            "season": 2097,
            "rules": {"direction": "winner", "basis": "straight_up"},
        },
    )
    assert pool.status_code == 201
    db = SessionLocal()
    try:
        db.add(
            Game(
                season=2097,
                week=1,
                away_team="",
                home_team="",
                kickoff=datetime.now(UTC),
                source="legacy-test",
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get(f"/api/v1/pools/{pool.json()['id']}/confidence-recommendations?week=1")
    assert response.status_code == 200
    assert response.json() == []


def test_html_news_fallback_extracts_attributed_items() -> None:
    # Regression: ISSUE-007, found by /qa on 2026-08-30.
    # Report: .gstack/qa-reports/qa-report-localhost-8787-2026-08-30.md
    db = SessionLocal()
    try:
        source = NewsSource(
            name="QA HTML Source",
            url="https://example.test/news",
            source_type="rss",
            official=True,
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        html = """
        <article>
          <a href="/news/runner-status"><h2>Runner returns to practice</h2></a>
          <p>The starter was limited during Sunday practice.</p>
          <time datetime="2026-08-30T12:00:00Z"></time>
        </article>
        """
        assert _parse_html_items(db, source, html) == 1
        db.commit()
        item = db.query(NewsItem).filter(NewsItem.source_id == source.id).one()
        assert item.canonical_url == "https://example.test/news/runner-status"
        assert item.excerpt == "The starter was limited during Sunday practice."
        assert item.category == "injury"
    finally:
        db.close()


def test_completed_live_draft_projects_canonical_rosters_into_league_players(
    client: TestClient,
) -> None:
    league = client.post(
        "/api/v1/leagues",
        json={
            "name": "Live roster handoff regression",
            "season": 2095,
            "scoring": {},
            "roster_slots": ["QB"],
        },
    )
    assert league.status_code == 201
    league_id = league.json()["id"]
    player_ids: list[int] = []
    for index in range(8):
        player = client.post(
            f"/api/v1/leagues/{league_id}/players",
            json={
                **_player_payload(f"Live Draft Player {index + 1}"),
                "source_id": f"live-roster-handoff-{index + 1}",
                "position": "QB",
                "projected_points": 100 - index,
            },
        )
        assert player.status_code == 201
        player_ids.append(player.json()["id"])

    team_names = ["Owner Team", *[f"Opponent {index}" for index in range(2, 9)]]
    session = client.post(
        f"/api/v1/leagues/{league_id}/draft-sessions",
        json={
            "kind": "live",
            "team_count": 8,
            "round_count": 1,
            "owner_team_slot": 1,
            "owner_team_name": "Owner Team",
            "team_names": team_names,
        },
    )
    assert session.status_code == 201
    session_id = session.json()["id"]
    started = client.post(
        f"/api/v1/draft-sessions/{session_id}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": "start-roster-handoff"},
    )
    assert started.status_code == 200
    sequence = started.json()["session"]["current_sequence"]
    recommendation = client.get(f"/api/v1/draft-sessions/{session_id}/recommendations").json()

    final_session = started.json()["session"]
    for index, player_id in enumerate(player_ids):
        payload = {
            "type": "pick_recorded",
            "expected_sequence": sequence,
            "idempotency_key": f"roster-handoff-pick-{index + 1}",
            "player_id": player_id,
        }
        if index == 0:
            payload["recommendation_snapshot_id"] = recommendation["snapshot_id"]
        recorded = client.post(f"/api/v1/draft-sessions/{session_id}/events", json=payload)
        assert recorded.status_code == 201
        final_session = recorded.json()["session"]
        sequence = final_session["current_sequence"]

    assert final_session["status"] == "COMPLETE"
    players = client.get(f"/api/v1/leagues/{league_id}/players").json()
    roster_by_id = {player["id"]: player["rostered_by"] for player in players}
    assert [roster_by_id[player_id] for player_id in player_ids] == team_names
    owner_lineup = client.get(
        f"/api/v1/leagues/{league_id}/lineup", params={"team_name": "Owner Team"}
    )
    assert owner_lineup.status_code == 200
    assert [row["player"]["id"] for row in owner_lineup.json()["assignments"]] == [player_ids[0]]
