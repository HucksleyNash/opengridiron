from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from app.db import Base
from app.models import DataSnapshot, League, NewsItem, NewsSource, Player
from app.services import football_sources as sources
from app.services import job_locks, news
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'sources.sqlite3'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(sources, "SessionLocal", sessions)
    monkeypatch.setattr(job_locks, "settings", replace(job_locks.settings, data_dir=tmp_path))
    yield sessions
    engine.dispose()


def sleeper_payload():
    return {
        "4984": {
            "player_id": "4984",
            "full_name": "Josh Allen",
            "position": "QB",
            "team": "BUF",
            "active": True,
            "injury_status": "Questionable",
            "depth_chart_order": 1,
            "yahoo_id": 30977,
        }
    }


def adp_payload():
    today = datetime.now(UTC).date().isoformat()
    return {
        "status": "Success",
        "meta": {
            "type": "PPR",
            "teams": 12,
            "total_drafts": 100,
            "start_date": today,
            "end_date": today,
        },
        "players": [
            {
                "player_id": 1,
                "name": "Josh Allen",
                "position": "QB",
                "team": "BUF",
                "adp": 24.5,
                "times_drafted": 20,
                "stdev": 4.5,
            }
        ],
    }


def transport(monkeypatch, handler):
    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: client_class(**kw, transport=httpx.MockTransport(handler)),
    )


def seed(db, request, payload, *, hours=0):
    row = DataSnapshot(
        source=request.source,
        source_id=request.source_id,
        status="fresh",
        payload_json=json.dumps(payload),
        retrieved_at=datetime.now(UTC) - timedelta(hours=hours),
    )
    db.add(row)
    db.commit()
    return row


def test_news_defaults_are_idempotent_and_preserve_owner_preferences(database):
    with database() as db:
        news.ensure_default_sources(db)
        espn = db.query(NewsSource).filter_by(name="ESPN NFL").one()
        espn.enabled = False
        espn.name = "My ESPN feed"
        db.commit()
        news.ensure_default_sources(db)
        assert db.query(NewsSource).count() == 4
        assert espn.enabled is False and espn.name == "My ESPN feed"
        assert db.query(NewsSource).filter_by(name="CBS Sports NFL").one().official is False


@pytest.mark.asyncio
async def test_daily_cache_and_real_player_evidence(database, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=sleeper_payload())

    transport(monkeypatch, handler)
    request = sources.SourceRequest("sleeper", sources.nfl_season_for_date(datetime.now(UTC)))
    first = await sources.refresh_source(request)
    second = await sources.refresh_source(request)
    assert len(requests) == 1
    assert first == second
    assert first["status"] == "available" and first["row_count"] == 1
    with database() as db:
        row = sources.source_evidence(db, request)["rows"][0]
        assert row["name"] == "Josh Allen" and row["injury_status"] == "Questionable"
        assert "rows" not in first


@pytest.mark.asyncio
async def test_failure_keeps_old_evidence_and_has_durable_retry_cooldown(database, monkeypatch):
    request = sources.SourceRequest("sleeper", sources.nfl_season_for_date(datetime.now(UTC)))
    with database() as db:
        old = seed(db, request, sources.parse_sleeper(sleeper_payload()), hours=25)
        old_id, old_time = old.id, sources.utc(old.retrieved_at).isoformat()
    calls = []

    def failing(request):
        calls.append(request)
        return httpx.Response(429)

    transport(monkeypatch, failing)
    first = await sources.refresh_source(request)
    second = await sources.refresh_source(request)
    assert first["status"] == second["status"] == "stale"
    assert first["snapshot_id"] == old_id and first["received_at"] == old_time
    assert len(calls) == 1
    with database() as db:
        assert len(sources.source_evidence(db, request)["rows"]) == 1
        assert db.query(DataSnapshot).count() == 2


@pytest.mark.asyncio
async def test_200_html_is_a_failed_source_and_does_not_advance_freshness(database, monkeypatch):
    transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, text="<html>Unavailable</html>", headers={"content-type": "text/html"}
        ),
    )
    result = await sources.refresh_source(sources.SourceRequest("ffc", datetime.now(UTC).year))
    assert result["status"] == "unavailable" and result["snapshot_id"] is None
    with database() as db:
        feed = NewsSource(name="broken", url="https://example.test/feed", source_type="rss")
        db.add(feed)
        db.commit()
        with pytest.raises(ValueError, match="recognizable"):
            await news.fetch_source(db, feed)
        assert feed.last_fetched_at is None


@pytest.mark.asyncio
async def test_rss_dates_attribution_and_duplicate_html_fallback(database, monkeypatch):
    body = b"""<rss version="2.0"><channel><title>NFL</title><item>
        <title>Josh Allen injury update</title><link>https://example.test/story</link>
        <description>Limited in practice</description>
        <pubDate>Sun, 06 Sep 2026 12:00:00 GMT</pubDate></item></channel></rss>"""
    transport(
        monkeypatch,
        lambda request: httpx.Response(200, content=body, headers={"content-type": "text/xml"}),
    )
    with database() as db:
        feed = NewsSource(name="ESPN fixture", url="https://example.test/feed", source_type="rss")
        db.add(feed)
        db.commit()
        assert (await news.fetch_source(db, feed))["created"] == 1
        assert (await news.fetch_source(db, feed))["created"] == 0
        item = db.query(NewsItem).one()
        assert item.source_id == feed.id and item.published_at.year == 2026
        html = '<article><h2>Josh Allen</h2><a href="/html">More</a></article>'
        assert news._parse_html_items(db, feed, html, require_items=True) == 1
        db.commit()
        assert news._parse_html_items(db, feed, html, require_items=True) == 0


@pytest.mark.parametrize(
    "field,value",
    [("type", "Non-PPR"), ("teams", 10), ("end_date", "2025-09-01"), ("total_drafts", 0)],
)
def test_adp_rejects_wrong_market_or_sample(field, value):
    payload = adp_payload()
    payload["meta"][field] = value
    with pytest.raises(ValueError):
        sources.parse_adp(payload, sources.SourceRequest("ffc", datetime.now(UTC).year))


def test_adp_rejects_nonfinite_values_and_retains_sample_dates():
    payload = adp_payload()
    request = sources.SourceRequest("ffc", datetime.now(UTC).year)
    parsed = sources.parse_adp(payload, request)
    assert parsed["rows"][0]["adp"] == 24.5
    assert parsed["sample"]["total_drafts"] == 100
    payload["players"][0]["adp"] = float("nan")
    with pytest.raises(ValueError, match="no usable"):
        sources.parse_adp(payload, request)


def test_old_adp_sample_is_stale_even_after_successful_download(database):
    request = sources.SourceRequest("ffc", datetime.now(UTC).year)
    parsed = sources.parse_adp(adp_payload(), request)
    parsed["source_date"] = (datetime.now(UTC).date() - timedelta(days=15)).isoformat()
    with database() as db:
        seed(db, request, parsed)
        assert sources.source_evidence(db, request)["status"] == "stale"


def test_identity_ambiguity_and_cross_team_matches_are_withheld():
    evidence = sources.parse_sleeper(sleeper_payload())
    players = [
        Player(id=1, name="Josh Allen", position="QB", pro_team="BUF"),
        Player(id=2, name="Josh Allen", position="QB", pro_team="JAX"),
    ]
    result = sources.matched_evidence(evidence, players)
    assert [r["player_id"] for r in result["rows"]] == [1]
    evidence["rows"].append(evidence["rows"][0].copy())
    assert sources.matched_evidence(evidence, players)["rows"] == []


def test_draft_market_uses_frozen_rules_and_rejects_superflex():
    session = SimpleNamespace(
        scoring_snapshot_json='{"rec":0.5}',
        roster_slots_snapshot_json='["QB","RB","FLEX"]',
        team_count=10,
    )
    request = sources.draft_adp_request(session, 2026)
    assert request.scoring_format == "half-ppr" and request.teams == 10
    session.roster_slots_snapshot_json = '["QB","Q/W/R/T"]'
    assert sources.draft_adp_request(session, 2026) is None
    session.roster_slots_snapshot_json = '["QB"]'
    session.team_count = 16
    assert sources.draft_adp_request(session, 2026) is None


def test_league_and_pool_dossiers_include_attributed_sources_without_mutations(database):
    from app.services.providers import build_dossier

    season = sources.nfl_season_for_date(datetime.now(UTC))
    with database() as db:
        league = League(name="Fixture", season=season)
        db.add(league)
        db.flush()
        player = Player(
            league_id=league.id,
            name="Josh Allen",
            pro_team="BUF",
            position="QB",
            status="Active",
            projected_points=100,
        )
        db.add(player)
        db.commit()
        seed(db, sources.SourceRequest("sleeper", season), sources.parse_sleeper(sleeper_payload()))
        dossier = build_dossier(db, league.id, None)
        evidence = dossier["supporting_sources"]["player_status"]
        assert evidence["url"] == sources.SLEEPER_URL
        assert evidence["rows"][0]["player_id"] == player.id
        assert player.status == "Active" and player.projected_points == 100
        assert sources.pool_evidence(db, season, {"BUF"})["included"] == 1
        assert sources.pool_evidence(db, season, {"CHI"})["included"] == 0


def test_registry_api_and_input_validation(client):
    response = client.get("/api/v1/data-sources")
    assert response.status_code == 200
    assert {r["key"] for r in response.json()} == {"sleeper", "ffc"}
    assert all("rows" not in r for r in response.json())
    assert client.get("/api/v1/data-sources?teams=7").status_code == 422
    assert client.get("/api/v1/data-sources?scoring_format=madeup").status_code == 422
    assert client.post("/api/v1/data-sources/unknown/fetch").status_code == 422
