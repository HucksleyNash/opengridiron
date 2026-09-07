from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import Alert, AnalysisRun, Game, JobRun, NewsItem, NewsSource
from .services.backups import create_backup
from .services.job_locks import job_lock as _job_lock
from .services.news import fetch_source
from .services.nflverse import nfl_season_for_date, sync_rosters, sync_schedule
from .services.push import send_notification
from .services.yahoo_scraper import scraper_status, sync_scraped_leagues

scheduler = AsyncIOScheduler(timezone=settings.timezone)
SOURCE_JOBS = {
    "news_collection",
    "nflverse_sync",
    "nflverse_rosters",
    "yahoo_refresh",
    "backup",
    "football_sources",
}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def recover_source_jobs(db: Session) -> int:
    recovered = 0
    for kind in SOURCE_JOBS:
        with _job_lock(kind) as acquired:
            if not acquired:
                continue
            for run in db.query(JobRun).filter_by(job_type=kind, status="running"):
                run.status = "interrupted"
                run.completed_at = datetime.now(UTC)
                run.detail_json = json.dumps({"reason": "application_restarted"})
                recovered += 1
            db.commit()
    for run in db.query(AnalysisRun).filter_by(status="running"):
        with _job_lock(f"analysis-{run.id}") as acquired:
            if not acquired:
                continue
            run.status = "failed"
            run.error = "The application stopped before this analysis completed. Run it again."
            run.completed_at = datetime.now(UTC)
            db.commit()
            recovered += 1
    return recovered


async def _run_source_job(
    job_type: str,
    operation: Callable[[Session], Awaitable[dict]],
    *,
    interval_minutes: int = 0,
) -> dict:
    with _job_lock(job_type) as acquired:
        if not acquired:
            return {"status": "running", "reason": "already_running"}
        with SessionLocal() as db:
            now = datetime.now(UTC)
            previous = (
                db.query(JobRun).filter_by(job_type=job_type).order_by(JobRun.id.desc()).first()
            )
            if previous:
                retry = json.loads(previous.detail_json).get("next_retry_at")
                if retry and now < _utc(datetime.fromisoformat(retry)):
                    return {"status": "cooldown", "next_retry_at": retry}
                if (
                    previous.completed_at
                    and interval_minutes
                    and (now - _utc(previous.completed_at)).total_seconds() < interval_minutes * 60
                ):
                    return {"status": "not_due"}
            run = JobRun(job_type=job_type, started_at=now)
            db.add(run)
            db.commit()
            db.refresh(run)
            try:
                detail = await operation(db)
                status = str(detail.get("status", "completed"))
                run.status = status if status in {"partial", "failed", "skipped"} else "completed"
                if run.status in {"partial", "failed"}:
                    detail["next_retry_at"] = (
                        datetime.now(UTC)
                        + timedelta(minutes=settings.source_failure_cooldown_minutes)
                    ).isoformat()
            except asyncio.CancelledError:
                db.rollback()
                run.status = "interrupted"
                run.detail_json = json.dumps({"reason": "application_stopped"})
                run.completed_at = datetime.now(UTC)
                db.commit()
                raise
            except Exception as exc:
                db.rollback()
                retry_seconds = max(
                    settings.source_failure_cooldown_minutes * 60,
                    int(getattr(exc, "retry_after_seconds", 0)),
                )
                run.status = "failed"
                detail = {
                    "error": f"Source refresh failed ({type(exc).__name__}); cached data was kept.",
                    "next_retry_at": (
                        datetime.now(UTC) + timedelta(seconds=retry_seconds)
                    ).isoformat(),
                }
            run.detail_json = json.dumps(detail)
            run.completed_at = datetime.now(UTC)
            db.commit()
            return {"status": run.status, "job_id": run.id, **detail}


async def collect_news() -> dict:
    async def collect(db: Session) -> dict:
        sources = db.query(NewsSource).filter(NewsSource.enabled.is_(True)).all()
        results = []
        before_news_id = db.query(NewsItem.id).order_by(NewsItem.id.desc()).limit(1).scalar() or 0
        before_alert_id = db.query(Alert.id).order_by(Alert.id.desc()).limit(1).scalar() or 0
        for source in sources:
            source_id = source.id
            try:
                result = await fetch_source(db, source)
                results.append({"id": source_id, **result})
            except Exception as exc:
                # A failed fetch must not persist a new freshness timestamp when
                # another source succeeds later in the same collection.
                db.rollback()
                results.append({"id": source_id, "status": "failed", "error": type(exc).__name__})
        from .services.providers import classify_new_news

        new_ids = [row[0] for row in db.query(NewsItem.id).filter(NewsItem.id > before_news_id)]
        classification = await classify_new_news(db, new_ids)
        failures = sum(row["status"] == "failed" for row in results)
        notification_errors = []
        for alert in (
            db.query(Alert)
            .filter(Alert.id > before_alert_id, Alert.severity.in_(["warning", "urgent"]))
            .all()
        ):
            try:
                send_notification(db, alert.title, alert.message, alert.url)
            except Exception as exc:
                notification_errors.append(type(exc).__name__)
        partial = (
            failures or classification.get("status") in {"partial", "failed"} or notification_errors
        )
        return {
            "sources": results,
            "classification": classification,
            "notification_errors": notification_errors,
            "status": "failed"
            if results and failures == len(results)
            else "partial"
            if partial
            else "completed",
        }

    return await _run_source_job(
        "news_collection", collect, interval_minutes=settings.news_refresh_minutes
    )


def backup_database() -> None:
    with _job_lock("backup") as acquired:
        if not acquired:
            return
        with SessionLocal() as db:
            run = JobRun(job_type="backup")
            db.add(run)
            db.commit()
            try:
                path = create_backup()
                run.status = "completed"
                run.detail_json = json.dumps({"filename": path.name, "scope": "database_only"})
            except Exception as exc:
                db.rollback()
                run.status = "failed"
                run.detail_json = json.dumps({"error": f"Backup failed ({type(exc).__name__})."})
            run.completed_at = datetime.now(UTC)
            db.commit()


def _schedule_interval(db: Session, now: datetime) -> int:
    # Poll around kickoff and final results more often; the source can still lag
    # live play and is never represented as a live score feed.
    imminent = (
        db.query(Game.id)
        .filter(
            Game.kickoff >= now - timedelta(hours=8),
            Game.kickoff <= now + timedelta(hours=24),
        )
        .first()
    )
    return settings.nflverse_live_refresh_minutes if imminent else settings.nflverse_refresh_minutes


async def refresh_open_data() -> dict:
    async def refresh(db: Session) -> dict:
        season = nfl_season_for_date(datetime.now(UTC))
        return {"schedule": {str(season): await sync_schedule(db, season, trigger="scheduled")}}

    with SessionLocal() as db:
        interval = _schedule_interval(db, datetime.now(UTC))
    return await _run_source_job("nflverse_sync", refresh, interval_minutes=interval)


async def refresh_roster_identities() -> dict:
    async def refresh(db: Session) -> dict:
        season = nfl_season_for_date(datetime.now(UTC))
        return {"rosters": await sync_rosters(db, season)}

    return await _run_source_job("nflverse_rosters", refresh, interval_minutes=24 * 60)


async def refresh_yahoo() -> dict:
    if not settings.yahoo_refresh_enabled:
        return {"status": "disabled"}

    async def refresh(db: Session) -> dict:
        if not scraper_status(db)["configured"]:
            return {"status": "skipped", "reason": "not_configured"}
        result = await sync_scraped_leagues(db)
        return {
            **result,
            "status": "partial" if result.get("errors") or result.get("partial") else "completed",
        }

    return await _run_source_job(
        "yahoo_refresh", refresh, interval_minutes=settings.yahoo_refresh_minutes
    )


async def refresh_football_sources() -> dict:
    from .draft.models import DraftSession
    from .models import League
    from .services.football_sources import SourceRequest, draft_adp_request, refresh_source

    async def refresh(db: Session) -> dict:
        season = nfl_season_for_date(datetime.now(UTC))
        requests = {SourceRequest("sleeper", season), SourceRequest("ffc", season)}
        for session in db.query(DraftSession).filter(
            DraftSession.status.in_(["SETUP", "READY", "LIVE", "PAUSED"])
        ):
            league = db.get(League, session.league_id)
            request = draft_adp_request(session, league.season)
            if request and league.season == season:
                requests.add(request)
        results = []
        for request in sorted(requests, key=lambda r: (r.key, r.source_id)):
            results.append(await refresh_source(request))
        return {
            "sources": results,
            "status": "partial"
            if any(r["status"] != "available" for r in results)
            else "completed",
        }

    return await _run_source_job("football_sources", refresh, interval_minutes=60)


def start_scheduler() -> None:
    if scheduler.running or not settings.scheduler_enabled:
        return
    now = datetime.now(UTC)
    for job, name, minutes, initial_delay in (
        (collect_news, "collect-news", settings.news_refresh_minutes, 15),
        (
            refresh_open_data,
            "nflverse-sync",
            min(settings.nflverse_refresh_minutes, settings.nflverse_live_refresh_minutes),
            30,
        ),
        (refresh_roster_identities, "nflverse-rosters", 24 * 60, 45),
        (refresh_yahoo, "yahoo-refresh", settings.yahoo_refresh_minutes, 60),
        (refresh_football_sources, "football-sources", 60, 75),
    ):
        scheduler.add_job(
            job,
            "interval",
            minutes=minutes,
            id=name,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            next_run_time=now + timedelta(seconds=initial_delay),
        )
    scheduler.add_job(
        backup_database,
        "cron",
        hour=3,
        minute=15,
        id="daily-backup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
