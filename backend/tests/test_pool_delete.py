from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from app.db import SessionLocal
from app.models import Game, Pool, PoolEntry, PoolEntryWeek, PoolPick
from app.schemas import PoolRules


def test_delete_pool_cascades_entries_cards_and_picks_only(client):
    with SessionLocal() as db:
        game = Game(
            season=2097,
            week=1,
            away_team="CHI",
            home_team="BUF",
            kickoff=datetime(2097, 9, 8, tzinfo=UTC),
        )
        unwanted = Pool(
            name="Manual duplicate",
            season=2097,
            pool_type="survivor",
            rules_json=PoolRules().model_dump_json(),
        )
        keep = Pool(
            name="Keep my pool",
            season=2097,
            pool_type="survivor",
            rules_json=PoolRules().model_dump_json(),
        )
        db.add_all([game, unwanted, keep])
        db.flush()
        entries = [
            PoolEntry(pool_id=unwanted.id, name="Main"),
            PoolEntry(pool_id=unwanted.id, name="Inactive", active=False),
            PoolEntry(pool_id=keep.id, name="Keep my entry"),
        ]
        db.add_all(entries)
        db.flush()
        for entry in entries:
            db.add(PoolEntryWeek(entry_id=entry.id, week=1, version=1))
            db.add(PoolPick(entry_id=entry.id, game_id=game.id, week=1, slot=1, team="BUF"))
        db.commit()
        pool_id, keep_id, game_id = unwanted.id, keep.id, game.id
        removed_ids = [entry.id for entry in entries[:2]]
        kept_entry_id = entries[2].id

    response = client.delete(f"/api/v1/pools/{pool_id}")
    assert response.status_code == 204
    assert response.content == b""
    with SessionLocal() as db:
        assert db.get(Pool, pool_id) is None
        assert db.query(PoolEntry).filter(PoolEntry.id.in_(removed_ids)).count() == 0
        assert db.query(PoolEntryWeek).filter(PoolEntryWeek.entry_id.in_(removed_ids)).count() == 0
        assert db.query(PoolPick).filter(PoolPick.entry_id.in_(removed_ids)).count() == 0
        assert db.get(Pool, keep_id) is not None
        assert db.get(Game, game_id) is not None
        assert db.query(PoolPick).filter(PoolPick.entry_id == kept_entry_id).one().team == "BUF"
        assert (
            db.query(PoolEntryWeek).filter(PoolEntryWeek.entry_id == kept_entry_id).one().version
            == 1
        )
    assert pool_id not in {p["id"] for p in client.get("/api/v1/pools").json()}
    assert pool_id not in {p["id"] for p in client.get("/api/v1/pools/overview").json()["pools"]}
    assert client.get(f"/api/v1/pools/{pool_id}/entries").status_code == 404
    assert (
        client.put(
            f"/api/v1/entries/{removed_ids[0]}/weeks/1/picks", json={"version": 1, "picks": []}
        ).status_code
        == 404
    )
    again = client.delete(f"/api/v1/pools/{pool_id}")
    assert again.status_code == 404
    assert again.json()["error"] == "pool_not_found"


def test_delete_empty_pool(client):
    pool = client.post(
        "/api/v1/pools",
        json={
            "name": "Unused manual pool",
            "season": 2097,
            "pool_type": "survivor",
        },
    ).json()
    assert client.delete(f"/api/v1/pools/{pool['id']}").status_code == 204


def test_delete_imported_pool_is_local_only(client, monkeypatch):
    import httpx

    def no_network(*args, **kwargs):
        raise AssertionError("Deleting a local pool must not contact Sleeper")

    monkeypatch.setattr(httpx.AsyncClient, "request", no_network)
    with SessionLocal() as db:
        pool = Pool(
            name="Imported pool",
            season=2097,
            pool_type="survivor",
            rules_json=PoolRules().model_dump_json(),
            sleeper_league_id="123456789",
        )
        db.add(pool)
        db.commit()
        pool_id = pool.id
    assert client.delete(f"/api/v1/pools/{pool_id}").status_code == 204
    with SessionLocal() as db:
        assert db.get(Pool, pool_id) is None


def test_delete_pool_requires_owner_authentication(client, monkeypatch):
    from app import dependencies

    pool = client.post(
        "/api/v1/pools",
        json={
            "name": "Protected pool",
            "season": 2097,
            "pool_type": "survivor",
        },
    ).json()
    monkeypatch.setattr(
        dependencies, "settings", replace(dependencies.settings, auth_required=True)
    )
    response = client.delete(f"/api/v1/pools/{pool['id']}", headers={"cookie": ""})
    assert response.status_code == 401
    with SessionLocal() as db:
        assert db.get(Pool, pool["id"]) is not None
