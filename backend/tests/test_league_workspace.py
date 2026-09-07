from __future__ import annotations

import json

import pytest
from app.db import SessionLocal
from app.models import League, Player
from app.services.projection_context import context_for
from app.services.yahoo_scraper import ScrapedPlayer, _upsert_player


@pytest.fixture
def league_id(client):
    response = client.post(
        "/api/v1/leagues",
        json={
            "name": "Projection workspace test",
            "season": 2026,
            "scoring": {"receptions": 1},
            "roster_slots": ["QB", "RB", "RB", "WR", "FLEX"],
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def player_payload(**overrides):
    return {
        "name": "Test Runner",
        "pro_team": "CHI",
        "position": "RB",
        "projected_points": 20,
        **overrides,
    }


def test_import_retains_period_scoring_and_distinguishes_zero_from_missing(client, league_id):
    context = {
        "source": "Test source",
        "period": "week",
        "season": 2026,
        "week": 1,
        "source_updated_at": "2026-09-02T12:00:00Z",
        "scoring_basis": "league_rules",
        "scoring": {"receptions": 1},
    }
    rows = [
        player_payload(name="Zero", ros_value=0, projection=context),
        player_payload(name="Missing", ros_value=None, projection=context),
    ]
    response = client.post(
        f"/api/v1/leagues/{league_id}/players/import",
        files={"file": ("players.json", json.dumps(rows), "application/json")},
    )
    assert response.status_code == 200, response.text
    saved = {p["name"]: p for p in client.get(f"/api/v1/leagues/{league_id}/players").json()}
    assert saved["Zero"]["ros_value"] == 0
    assert saved["Zero"]["projection"]["ros_value_state"] == "provided"
    assert saved["Missing"]["ros_value"] is None
    assert saved["Missing"]["projection"]["ros_value_state"] == "missing"
    for item in saved.values():
        assert item["projection"]["week"] == 1
        assert item["projection"]["scoring"] == {"receptions": 1}
        assert item["projection"]["received_at"] != item["projection"]["source_updated_at"]


def test_csv_context_preserves_zero_risk_and_missing_ros(client, league_id):
    content = (
        "name,pro_team,position,projected_points,ros_value,risk,projection_source,"
        "projection_period,projection_season\nCSV Runner,CHI,RB,10,,0,Model A,season,2026\n"
    )
    result = client.post(
        f"/api/v1/leagues/{league_id}/players/import",
        files={"file": ("players.csv", content, "text/csv")},
    )
    assert result.status_code == 200, result.text
    player = client.get(f"/api/v1/leagues/{league_id}/players").json()[0]
    assert player["risk"] == 0
    assert player["ros_value"] is None
    assert player["projection"]["period"] == "season"
    assert player["projection"]["source_updated_at"] is None


@pytest.mark.parametrize(
    "context",
    [
        {"period": "week", "season": 2026},
        {"period": "season"},
        {"source_updated_at": "2026-09-02T12:00:00"},
        {"scoring_basis": "league_rules"},
    ],
)
def test_invalid_context_is_rejected_atomically(client, league_id, context):
    rows = [player_payload(name="Valid"), player_payload(name="Invalid", projection=context)]
    result = client.post(
        f"/api/v1/leagues/{league_id}/players/import",
        files={"file": ("players.json", json.dumps(rows), "application/json")},
    )
    assert result.status_code == 422
    assert client.get(f"/api/v1/leagues/{league_id}/players").json() == []


def test_yahoo_projection_context_does_not_refresh_on_roster_only_updates(client, league_id):
    with SessionLocal() as db:
        league = db.get(League, league_id)
        player = _upsert_player(
            db,
            league,
            ScrapedPlayer(
                source_id="yahoo.p.777",
                name="Yahoo Runner",
                pro_team="CHI",
                position="RB",
                projected_points=200,
                projection_period="season",
                projection_season=2026,
                projection_received_at="2026-09-03T12:00:00Z",
            ),
        )
        db.flush()
        before = context_for(player)
        assert before.period == "season"
        assert before.source_updated_at is None
        assert before.ros_value_state == "missing"
        assert before.scoring == {"receptions": 1}
        _upsert_player(
            db,
            league,
            ScrapedPlayer(
                source_id="yahoo.p.777",
                name="Yahoo Runner",
                pro_team="CHI",
                position="RB",
                status="Questionable",
            ),
        )
        assert context_for(player) == before
        assert player.projected_points == 200


@pytest.fixture
def populated(client, league_id):
    with SessionLocal() as db:
        for i in range(1200):
            db.add(
                Player(
                    league_id=league_id,
                    name=f"Player {i:04}",
                    pro_team="CHI" if i % 2 else "BUF",
                    position=["QB", "RB", "WR", "D/ST"][i % 4],
                    projected_points=float(1200 - i),
                    ros_value=float(i % 12),
                    ownership="TEAM" if i < 15 else "W" if i % 3 else "FA",
                    rostered_by="Team A" if i < 15 else None,
                    current_slot="BN" if i < 15 else None,
                    status="Questionable" if i % 5 == 0 else "Active",
                    evidence_json=json.dumps([{"detail": "Large evidence " * 100}]),
                )
            )
        db.commit()
    return league_id


def test_pagination_matches_full_rankings_and_preserves_overall_rank(client, populated):
    prefix = f"/api/v1/leagues/{populated}"
    full = client.get(f"{prefix}/waivers").json()
    first = client.get(f"{prefix}/waivers/page").json()
    second = client.get(f"{prefix}/waivers/page?offset=10").json()
    assert first["available"] == first["total"] == 1185
    assert len(first["items"]) == len(second["items"]) == 10
    assert first["next_offset"] == 10
    assert [p["player_id"] for p in first["items"] + second["items"]] == [
        p["player_id"] for p in full[:20]
    ]
    assert [p["expected_value"] for p in first["items"]] == [p["expected_value"] for p in full[:10]]
    filtered = client.get(f"{prefix}/waivers/page?role=RB&team=CHI&availability=waivers").json()
    rank_by_id = {p["player_id"]: p["rank"] for p in full}
    for item in filtered["items"]:
        assert item["rank"] == rank_by_id[item["player_id"]]
        assert item["player"]["position"] == "RB"
        assert item["player"]["ownership"] == "W"
    assert client.get(f"{prefix}/waivers/page?role=DEF").json()["total"] > 0
    assert client.get(f"{prefix}/waivers/page?search=%25").json()["total"] == 0
    assert client.get(f"{prefix}/waivers/page?limit=500").status_code == 422
    assert client.get(f"{prefix}/waivers/page?offset=-1").status_code == 422


def test_compact_routes_and_compression_have_payload_budgets(client, populated):
    prefix = f"/api/v1/leagues/{populated}"
    roster = client.get(f"{prefix}/roster")
    assert len(roster.json()) == 15
    assert len(roster.content) < 20_000
    assert roster.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in roster.headers["vary"]
    assert all(not p["evidence"] for p in roster.json())
    page = client.get(f"{prefix}/waivers/page")
    assert len(page.content) < 15_000
    assert len(client.get(f"{prefix}/projection-leaders").json()) == 10
    assert (
        client.get(f"{prefix}/roster", headers={"Accept-Encoding": "identity"}).headers.get(
            "content-encoding"
        )
        is None
    )


def test_legacy_zero_is_preserved_not_reclassified_as_missing(client, league_id):
    with SessionLocal() as db:
        db.add(
            Player(
                league_id=league_id, name="Legacy Zero", pro_team="CHI", position="RB", ros_value=0
            )
        )
        db.commit()
    player = client.get(f"/api/v1/leagues/{league_id}/players").json()[0]
    assert player["ros_value"] == 0
    assert player["projection"]["ros_value_state"] == "legacy_unknown"
    assert player["projection"]["received_at"] is None


def test_waiver_query_supports_empty_roster_slot_settings(client, league_id):
    with SessionLocal() as db:
        db.get(League, league_id).roster_slots_json = "[]"
        db.add(Player(league_id=league_id, name="No slot runner", pro_team="CHI", position="RB"))
        db.commit()
    result = client.get(f"/api/v1/leagues/{league_id}/waivers/page")
    assert result.status_code == 200, result.text
    assert result.json()["total"] == 1
