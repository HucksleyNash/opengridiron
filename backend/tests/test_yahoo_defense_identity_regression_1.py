from __future__ import annotations

from uuid import uuid4

from app.db import SessionLocal
from app.draft.models import DraftEvent, DraftSession
from app.models import DraftPick, League, Player
from app.services.yahoo_scraper import _upsert_draft_picks, parse_draft_page

# Regression: ISSUE-001 — Yahoo defense picks lost their player identity and stayed draftable
# Found by /qa on 2026-09-02
# Report: .gstack/qa-reports/qa-report-yahoo-cockpit-2026-09-02.md


def _defense_draft_html() -> str:
    rows = []
    for slot in range(1, 9):
        player = (
            '<a class="name" href="https://sports.yahoo.com/nfl/teams/la-rams/">Rams</a>'
            "<span>(LAR - DEF)</span>"
            if slot == 1
            else "—"
        )
        rows.append(
            f'<tr><td class="first">{slot}.</td><td class="player">{player}</td>'
            f'<td class="last">Team {slot}</td></tr>'
        )
    return (
        "<table><thead><tr><th>Round 1</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def test_draft_parser_preserves_identity_fields_for_team_defense_urls() -> None:
    pick = parse_draft_page(_defense_draft_html())[0]

    assert pick.player_name == "Rams"
    assert pick.pro_team == "LAR"
    assert pick.position == "DEF"
    assert pick.player_source_id is None


def test_draft_upsert_matches_idless_defense_to_existing_league_player(client) -> None:
    league = client.post(
        "/api/v1/leagues",
        json={
            "name": f"Yahoo defense import {uuid4()}",
            "season": 2026,
            "roster_slots": ["QB", "RB", "WR", "TE", "FLEX", "DEF"],
        },
    ).json()
    defense = client.post(
        f"/api/v1/leagues/{league['id']}/players",
        json={
            "source_id": "yahoo.p.100014",
            "name": "Rams",
            "pro_team": "LAR",
            "position": "DEF",
            "projected_points": 126.7,
        },
    ).json()
    scraped_pick = parse_draft_page(_defense_draft_html())[0]

    db = SessionLocal()
    try:
        stored_league = db.get(League, league["id"])
        assert stored_league is not None
        _upsert_draft_picks(db, stored_league, [scraped_pick])
        db.commit()
        stored_pick = db.query(DraftPick).filter_by(league_id=league["id"], overall=1).one()
        matching_players = (
            db.query(Player)
            .filter_by(league_id=league["id"], name="Rams", pro_team="LAR", position="DEF")
            .all()
        )
    finally:
        db.close()

    assert stored_pick.player_id == defense["id"]
    assert len(matching_players) == 1


def test_authoritative_sync_replaces_matching_draft_placeholder_identity(client) -> None:
    league = client.post(
        "/api/v1/leagues",
        json={
            "name": f"Yahoo defense identity {uuid4()}",
            "season": 2026,
            "roster_slots": ["QB", "RB", "WR", "TE", "FLEX", "DEF"],
        },
    ).json()
    player_ids = []
    for index in range(8):
        is_defense = index == 0
        player = client.post(
            f"/api/v1/leagues/{league['id']}/players",
            json={
                "source_id": "yahoo.p.100014" if is_defense else f"yahoo.p.{index + 1}",
                "name": "Rams" if is_defense else f"Ranked Player {index + 1}",
                "pro_team": "LAR" if is_defense else "CHI",
                "position": "DEF" if is_defense else ["RB", "WR", "QB", "TE"][index % 4],
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
    ).json()["session"]
    db = SessionLocal()
    try:
        placeholder = Player(
            league_id=league["id"],
            source_id="draft.1",
            name="Rams",
            pro_team="LAR",
            position="DEF",
        )
        db.add(placeholder)
        db.flush()
        db.refresh(placeholder)
        placeholder_id = placeholder.id
        stored_session = db.get(DraftSession, session["id"])
        assert stored_session is not None
        stored_session.current_sequence = started["current_sequence"] + 1
        db.add(
            DraftEvent(
                session_id=stored_session.id,
                sequence=stored_session.current_sequence,
                type="pick_recorded",
                overall_pick=1,
                round=1,
                team_slot=1,
                player_id=placeholder_id,
                source="yahoo_scrape_shadow",
                idempotency_key="placeholder-defense-pick",
            )
        )
        db.commit()
        recorded_sequence = stored_session.current_sequence
    finally:
        db.close()
    promoted = client.post(
        f"/api/v1/draft-sessions/{session['id']}/source-mode",
        json={
            "expected_sequence": recorded_sequence,
            "mode": "yahoo_scrape_authoritative",
            "owner_confirmed": True,
        },
    ).json()

    synchronized = client.post(
        f"/api/v1/draft-sessions/{session['id']}/sync/yahoo",
        json={
            "expected_sequence": promoted["current_sequence"],
            "fixture_observations": [
                {
                    "provider_key": "defense-pick-1",
                    "provider_revision": "real-yahoo-id",
                    "overall_pick": 1,
                    "team_slot": 1,
                    "player_id": player_ids[0],
                }
            ],
        },
    )

    assert synchronized.status_code == 200
    assert synchronized.json()["counts"] == {"applied": 1, "confirmed": 0, "proposed": 0}
    board = client.get(f"/api/v1/draft-sessions/{session['id']}/board").json()
    assert board["picks"][0]["player_id"] == player_ids[0]
    assert player_ids[0] not in {player["id"] for player in board["available_players"]}
    recommendations = client.get(f"/api/v1/draft-sessions/{session['id']}/recommendations").json()
    assert player_ids[0] not in {
        candidate["player_id"]
        for candidate in recommendations["candidates"] + recommendations["alternatives"]
    }
