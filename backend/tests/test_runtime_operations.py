from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from app import scheduler
from app.config import Settings
from app.db import Base
from app.main import bootstrap_owner
from app.models import Alert, AnalysisRun, DataSnapshot, Game, JobRun, NewsItem, NewsSource, Owner
from app.services import job_locks, providers, yahoo_scraper
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    configuration = replace(scheduler.settings, data_dir=tmp_path / "data")
    monkeypatch.setattr(scheduler, "settings", configuration)
    monkeypatch.setattr(job_locks, "settings", configuration)
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime.sqlite3'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(scheduler, "SessionLocal", sessions)
    yield sessions
    engine.dispose()


def test_owner_bootstrap_requires_password_only_for_an_empty_authenticated_install(runtime):
    configuration = replace(Settings(), auth_required=True, owner_password=None)
    with runtime() as db:
        with pytest.raises(RuntimeError, match="OWNER_PASSWORD"):
            bootstrap_owner(db, configuration)
        assert db.query(Owner).count() == 0
        bootstrap_owner(db, replace(configuration, owner_password="a-test-bootstrap-password"))
        stored = db.query(Owner).one().password_hash
        bootstrap_owner(db, configuration)
        assert db.query(Owner).one().password_hash == stored
        bootstrap_owner(db, replace(configuration, owner_password="a-different-password"))
        assert db.query(Owner).one().password_hash == stored


def test_local_ownerless_start_remains_supported(runtime):
    with runtime() as db:
        bootstrap_owner(db, replace(Settings(), auth_required=False, owner_password=None))
        assert db.query(Owner).count() == 0


@pytest.mark.asyncio
async def test_source_failure_cooldown_is_durable_and_prevents_second_request(runtime):
    calls = 0

    async def failing(_db):
        nonlocal calls
        calls += 1
        raise yahoo_scraper.YahooScraperRateLimited(7200)

    first = await scheduler._run_source_job("yahoo_refresh", failing)
    second = await scheduler._run_source_job("yahoo_refresh", failing)
    assert first["status"] == "failed" and second["status"] == "cooldown"
    assert calls == 1
    with runtime() as db:
        run = db.query(JobRun).one()
        assert run.status == "failed" and run.completed_at
        assert datetime.fromisoformat(json.loads(run.detail_json)["next_retry_at"]) > datetime.now(
            UTC
        ) + timedelta(minutes=119)


@pytest.mark.asyncio
async def test_source_jobs_do_not_overlap(runtime):
    started, release = asyncio.Event(), asyncio.Event()

    async def operation(_db):
        started.set()
        await release.wait()
        return {"created": 1}

    first = asyncio.create_task(scheduler._run_source_job("news_collection", operation))
    await started.wait()
    try:
        second = await scheduler._run_source_job("news_collection", operation)
        assert second == {"status": "running", "reason": "already_running"}
    finally:
        release.set()
    assert (await first)["status"] == "completed"
    with runtime() as db:
        assert db.query(JobRun).count() == 1


def test_recovery_preserves_another_workers_active_analysis(runtime):
    with runtime() as db:
        abandoned = AnalysisRun(task="chat", model="fixture", status="running", input_hash="a")
        active = AnalysisRun(task="chat", model="fixture", status="running", input_hash="b")
        db.add_all([abandoned, active, JobRun(job_type="yahoo_refresh", status="running")])
        db.commit()
        with job_locks.job_lock(f"analysis-{active.id}") as acquired:
            assert acquired
            assert scheduler.recover_source_jobs(db) == 2
            assert active.status == "running" and abandoned.status == "failed"
        assert scheduler.recover_source_jobs(db) == 1
        assert active.status == "failed"


def test_schedule_polling_tightens_only_around_saved_kickoffs(runtime):
    now = datetime.now(UTC)
    with runtime() as db:
        assert scheduler._schedule_interval(db, now) == scheduler.settings.nflverse_refresh_minutes
        db.add(
            Game(
                season=2026,
                week=1,
                home_team="CHI",
                away_team="GB",
                kickoff=now + timedelta(hours=1),
            )
        )
        db.commit()
        assert (
            scheduler._schedule_interval(db, now)
            == scheduler.settings.nflverse_live_refresh_minutes
        )


@pytest.mark.asyncio
async def test_news_failure_retains_success_timestamp_and_classifies_only_new_items(
    runtime, monkeypatch
):
    before = datetime(2026, 1, 1)
    with runtime() as db:
        broken = NewsSource(
            name="broken", url="https://example.test/broken", last_fetched_at=before
        )
        good = NewsSource(name="good", url="https://example.test/good")
        db.add_all([broken, good])
        db.flush()
        old = NewsItem(
            source_id=good.id,
            title="Old",
            canonical_url="https://example.test/old",
            content_hash="old",
            category="general",
            severity="info",
        )
        db.add(old)
        db.commit()
        broken_id, good_id, old_id = broken.id, good.id, old.id
    classified_ids, notifications = [], []

    async def fetch(db, source):
        source.last_fetched_at = datetime.now(UTC)
        if source.id == broken_id:
            raise RuntimeError("failed fixture")
        db.add(
            NewsItem(
                source_id=good_id,
                title="New",
                canonical_url="https://example.test/new",
                content_hash="new",
                category="general",
                severity="info",
            )
        )
        db.commit()
        return {"status": "ok", "created": 1}

    async def classify(db, ids):
        classified_ids.extend(ids)
        db.add(
            Alert(
                fingerprint="classified",
                title="Classified",
                message="Verify news",
                severity="urgent",
            )
        )
        db.commit()
        return {"status": "completed", "classified": len(ids)}

    monkeypatch.setattr(scheduler, "fetch_source", fetch)
    monkeypatch.setattr(providers, "classify_new_news", classify)
    monkeypatch.setattr(
        scheduler, "send_notification", lambda _db, title, *_args: notifications.append(title)
    )
    result = await scheduler.collect_news()
    assert result["status"] == "partial"
    assert len(classified_ids) == 1 and old_id not in classified_ids
    assert notifications == ["Classified"]
    with runtime() as db:
        assert db.get(NewsSource, broken_id).last_fetched_at == before
        assert db.get(NewsSource, good_id).last_fetched_at > before


@pytest.mark.asyncio
async def test_news_classification_partial_failure_is_not_reported_complete(runtime, monkeypatch):
    async def classify(_db, _ids):
        return {"status": "partial", "classified": 20, "error": "Fixture later batch failed"}

    monkeypatch.setattr(providers, "classify_new_news", classify)
    first = await scheduler.collect_news()
    second = await scheduler.collect_news()
    assert first["status"] == "partial" and second["status"] == "cooldown"
    with runtime() as db:
        assert db.query(JobRun).one().status == "partial"


@pytest.mark.asyncio
async def test_news_success_coalesces_across_scheduler_invocations(runtime, monkeypatch):
    calls = 0

    async def classify(_db, _ids):
        nonlocal calls
        calls += 1
        return {"status": "disabled", "classified": 0}

    monkeypatch.setattr(providers, "classify_new_news", classify)
    first = await scheduler.collect_news()
    second = await scheduler.collect_news()
    assert first["status"] == "completed" and second["status"] == "not_due"
    assert calls == 1


@pytest.mark.asyncio
async def test_yahoo_throttle_stops_full_import_and_survives_new_session(runtime, monkeypatch):
    calls = []

    async def scrape(_db, _client, url, **_kwargs):
        calls.append(url)
        raise yahoo_scraper.YahooScraperRateLimited(1800)

    monkeypatch.setattr(yahoo_scraper, "_scrape_one", scrape)
    with runtime() as db:
        with pytest.raises(yahoo_scraper.YahooScraperRateLimited):
            await yahoo_scraper._sync_scraped_urls(
                db, ["https://example.test/one", "https://example.test/two"], "A1=fixture"
            )
        assert len(calls) == 1
        assert db.query(DataSnapshot).filter_by(source="yahoo_scrape.cooldown").count() == 1
    with runtime() as db:
        with pytest.raises(yahoo_scraper.YahooScraperRateLimited):
            await yahoo_scraper._sync_scraped_urls(db, ["https://example.test/one"], "A1=fixture")
        assert len(calls) == 1
