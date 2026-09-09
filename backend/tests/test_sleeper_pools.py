from __future__ import annotations

import copy
import itertools
import json
from datetime import UTC, datetime

import httpx
import pytest
from app.pool_errors import PoolDomainError
from app.schemas import PoolRules
from app.services.sleeper_pools import league_id_from_url

_ids = itertools.count(910000000000000000)
_async_client = httpx.AsyncClient


@pytest.fixture
def sleeper(monkeypatch):
    from app.services import sleeper_pools

    league_id = str(next(_ids))
    state = {
        "league": {
            "league_id": league_id,
            "name": "Example survivor pool",
            "season": "2095",
            "season_type": "regular",
            "sport": "pickem:nfl",
            "status": "in_season",
            "settings": {
                "leg": 1,
                "num_teams": 500,
                "pickem_type": 1,
                "use_spread": 0,
                "use_confidence": 0,
                "weekly_pick_limit": 1,
                "num_picks_allowed_per_team": 1,
                "num_revives_allowed": 0,
                "scoring_type": 0,
                "daily_advance": 0,
                "perfect_bonus": 0,
                "perfect_week_bonus": 0,
            },
            "scoring_settings": {"v1:regular:1": 1.0},
            "last_message_text_map": {"private": "Do not store pool chat"},
        },
        "users": [
            {"user_id": "111", "display_name": "PoolOwner", "is_owner": False},
            {"user_id": "222", "display_name": "Commissioner", "is_owner": True},
        ],
        "rosters": [
            {"owner_id": "222", "roster_id": 1, "metadata": {"is_eliminated": "false"}},
            {"owner_id": "111", "roster_id": 30, "metadata": {"is_eliminated": "false"}},
            {"owner_id": "111", "roster_id": 31, "metadata": {"is_eliminated": "true"}},
        ],
        "user": {"user_id": "111", "username": "poolowner", "display_name": "PoolOwner"},
        "status": 200,
        "requests": [],
    }

    def respond(request):
        assert request.method == "GET"
        assert request.url.host == "api.sleeper.app"
        state["requests"].append(str(request.url))
        if state["status"] != 200:
            return httpx.Response(state["status"], json={})
        key = {
            f"/v1/league/{league_id}": "league",
            f"/v1/league/{league_id}/users": "users",
            f"/v1/league/{league_id}/rosters": "rosters",
            "/v1/user/poolowner": "user",
        }[request.url.path]
        return httpx.Response(
            200, content=json.dumps(state[key]), headers={"Content-Type": "application/json"}
        )

    def client_factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        return _async_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(sleeper_pools.httpx, "AsyncClient", client_factory)
    state["payload"] = {"url": f"https://sleeper.com/leagues/{league_id}", "username": "poolowner"}
    return state


@pytest.mark.parametrize(
    "value",
    [
        "http://sleeper.com/leagues/123",
        "https://sleeper.com.evil.test/leagues/123",
        "https://sleeper.com@evil.test/leagues/123",
        "https://sleeper.com/leagues/../123",
        "https://sleeper.com:444/leagues/123",
        "https://sleeper.com/leagues/1e18",
        "https://sleeper.com/i/invite",
        "https://127.0.0.1/leagues/123",
        "１２３",
    ],
)
def test_rejects_arbitrary_hosts_paths_and_non_integer_ids(value):
    with pytest.raises(PoolDomainError) as error:
        league_id_from_url(value)
    assert error.value.code == "invalid_sleeper_url"


@pytest.mark.parametrize(
    "value",
    [
        "1234567890123456789",
        " https://sleeper.com/leagues/1234567890123456789/ ",
        "https://sleeper.com/leagues/1234567890123456789?tab=standings",
    ],
)
def test_preserves_large_league_ids_as_strings(value):
    assert league_id_from_url(value) == "1234567890123456789"


def test_preview_maps_rules_membership_and_owner_without_saving(client, sleeper):
    before = len(client.get("/api/v1/pools").json())
    response = client.post("/api/v1/integrations/sleeper/pools/preview", json=sleeper["payload"])
    assert response.status_code == 200
    info = response.json()
    assert info["entry_count"] == 3
    assert info["participant_count"] == 2
    assert info["capacity"] == 500
    assert info["commissioners"] == ["Commissioner"]
    assert [entry["roster_id"] for entry in info["entries"]] == [30, 31]
    assert [entry["eliminated"] for entry in info["entries"]] == [False, True]
    assert info["rules"]["tie_result"] == "eliminate"
    assert info["rules"]["lock_mode"] == "game_start"
    assert info["rules"]["max_team_uses"] == 1
    assert info["rules"]["picks_per_week"] == 1
    assert info["unsupported"] == []
    assert "pick history are not imported" in info["warnings"][0]
    assert "Do not store pool chat" not in response.text
    assert len(client.get("/api/v1/pools").json()) == before
    assert len(sleeper["requests"]) == 4


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("num_revives_allowed", 1),
        ("use_spread", 1),
        ("use_confidence", 1),
        ("pickem_type", 0),
        ("scoring_type", 2),
        ("weekly_pick_limit", None),
        ("num_picks_allowed_per_team", 0),
        ("perfect_bonus", 1),
        ("daily_advance", 1),
    ],
)
def test_unsupported_settings_are_previewed_but_never_imported(client, sleeper, key, value):
    sleeper["league"]["settings"][key] = value
    preview = client.post("/api/v1/integrations/sleeper/pools/preview", json=sleeper["payload"])
    assert preview.status_code == 200
    assert preview.json()["rules"] is None
    assert preview.json()["unsupported"]
    response = client.post("/api/v1/integrations/sleeper/pools/import", json=sleeper["payload"])
    assert response.status_code == 422
    assert response.json()["error"] == "unsupported_sleeper_rules"
    assert not any(
        pool.get("sleeper", {}).get("league_id") == sleeper["league"]["league_id"]
        for pool in client.get("/api/v1/pools").json()
        if pool.get("sleeper")
    )


def test_import_refresh_are_idempotent_and_preserve_local_picks(client, sleeper):
    from app.db import SessionLocal
    from app.models import PoolEntry, PoolPick

    first = client.post("/api/v1/integrations/sleeper/pools/import", json=sleeper["payload"])
    assert first.status_code == 200
    pool = first.json()
    second = client.post("/api/v1/integrations/sleeper/pools/import", json=sleeper["payload"])
    assert second.status_code == 200
    assert second.json()["id"] == pool["id"]
    assert second.json()["entry_count"] == 2
    entries = client.get(f"/api/v1/pools/{pool['id']}/entries").json()
    with SessionLocal() as db:
        db.add(
            PoolPick(
                entry_id=entries[0]["id"], week=1, slot=1, team="BUF", saved_at=datetime.now(UTC)
            )
        )
        db.get(PoolEntry, entries[0]["id"]).name = "My local entry name"
        db.get(PoolEntry, entries[0]["id"]).active = False
        db.commit()
    sleeper["league"]["name"] = "Updated upstream pool"
    response = client.post(f"/api/v1/pools/{pool['id']}/sleeper/refresh")
    assert response.status_code == 200
    assert response.json()["name"] == "Updated upstream pool"
    with SessionLocal() as db:
        assert db.query(PoolPick).filter(PoolPick.entry_id == entries[0]["id"]).one().team == "BUF"
        assert db.get(PoolEntry, entries[0]["id"]).name == "My local entry name"
        assert db.get(PoolEntry, entries[0]["id"]).active is False
    overview = client.get("/api/v1/pools/overview").json()
    assert (
        next(p for p in overview["pools"] if p["id"] == pool["id"])["sleeper"]["name"]
        == "Updated upstream pool"
    )
    workspace = client.get(f"/api/v1/pools/{pool['id']}/weeks/1?entry_id={entries[1]['id']}")
    assert workspace.status_code == 200
    assert workspace.json()["pool"]["sleeper"]["username"] == "poolowner"
    prior = response.json()
    sleeper["league"]["settings"]["weekly_pick_limit"] = 2
    rejected = client.post(f"/api/v1/pools/{pool['id']}/sleeper/refresh")
    assert rejected.status_code == 409
    assert rejected.json()["error"] == "pool_rules_locked"
    after = next(p for p in client.get("/api/v1/pools").json() if p["id"] == pool["id"])
    assert after == prior


def test_import_without_username_then_link_owner_and_block_manual_rule_edits(client, sleeper):
    payload = {**sleeper["payload"], "username": ""}
    pool = client.post("/api/v1/integrations/sleeper/pools/import", json=payload).json()
    assert pool["entry_count"] == 0
    linked = client.post("/api/v1/integrations/sleeper/pools/import", json=sleeper["payload"])
    assert linked.status_code == 200
    assert linked.json()["id"] == pool["id"]
    assert linked.json()["entry_count"] == 2
    rules = copy.deepcopy(pool["rules"])
    rules["tie_result"] = "push"
    changed = client.put(f"/api/v1/pools/{pool['id']}", json={**pool, "rules": rules})
    assert changed.status_code == 409
    assert changed.json()["error"] == "sleeper_managed_rules"


@pytest.mark.parametrize("status", [302, 429, 500])
def test_provider_errors_preserve_snapshot(client, sleeper, status):
    saved = client.post("/api/v1/integrations/sleeper/pools/import", json=sleeper["payload"]).json()
    sleeper["status"] = status
    response = client.post(f"/api/v1/pools/{saved['id']}/sleeper/refresh")
    assert response.status_code == 502
    assert response.json()["error"] == "sleeper_unavailable"
    assert next(p for p in client.get("/api/v1/pools").json() if p["id"] == saved["id"]) == saved


def test_refresh_does_not_recreate_pool_deleted_while_fetching(client, sleeper, monkeypatch):
    from app.db import SessionLocal
    from app.models import Pool, PoolEntry
    from app.routes import pools
    from sqlalchemy import delete

    saved = client.post("/api/v1/integrations/sleeper/pools/import", json=sleeper["payload"]).json()
    original_preview = pools.preview_pool

    async def delete_during_fetch(payload):
        info = await original_preview(payload)
        with SessionLocal() as db:
            db.execute(delete(Pool).where(Pool.id == saved["id"]))
            db.commit()
        return info

    monkeypatch.setattr(pools, "preview_pool", delete_during_fetch)
    response = client.post(f"/api/v1/pools/{saved['id']}/sleeper/refresh")

    assert response.status_code == 404
    assert response.json()["error"] == "sleeper_pool_not_found"
    with SessionLocal() as db:
        assert (
            db.query(Pool).filter(Pool.sleeper_league_id == sleeper["league"]["league_id"]).count()
            == 0
        )
        assert db.query(PoolEntry).filter(PoolEntry.pool_id == saved["id"]).count() == 0


def test_missing_user_and_non_member_have_actionable_errors(client, sleeper):
    sleeper["user"] = None
    missing = client.post("/api/v1/integrations/sleeper/pools/preview", json=sleeper["payload"])
    assert missing.status_code == 404
    assert missing.json()["error"] == "sleeper_user_not_found"
    sleeper["user"] = {"user_id": "999"}
    absent = client.post("/api/v1/integrations/sleeper/pools/preview", json=sleeper["payload"])
    assert absent.status_code == 422
    assert absent.json()["error"] == "sleeper_entry_not_found"


def test_null_or_malformed_league_fails_cleanly(client, sleeper):
    sleeper["league"] = None
    missing = client.post("/api/v1/integrations/sleeper/pools/preview", json=sleeper["payload"])
    assert missing.status_code == 404
    sleeper["league"] = {
        "league_id": league_id_from_url(sleeper["payload"]["url"]),
        "settings": [1],
    }
    malformed = client.post("/api/v1/integrations/sleeper/pools/preview", json=sleeper["payload"])
    assert malformed.status_code == 502


def test_sleeper_tie_eliminates_without_changing_manual_default():
    from app.models import Game, PoolPick
    from app.services.pool_outcomes import pick_result

    game = Game(
        week=1, home_team="BUF", away_team="CHI", completed=True, home_score=20, away_score=20
    )
    pick = PoolPick(week=1, team="BUF")
    assert pick_result(pick, game, PoolRules(tie_result="eliminate")) == "loss"
    assert pick_result(pick, game, PoolRules()) == "push"
