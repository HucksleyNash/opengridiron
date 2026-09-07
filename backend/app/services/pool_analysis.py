"""Pool check-in: collect sources, freeze evidence, propose, then validate and save."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import AnalysisProvider, AnalysisRun, DataSnapshot, Game, NewsItem, NewsSource, Pool
from ..pool_errors import PoolDomainError
from ..schemas import WeeklyCardUpdate
from .football_sources import SourceRequest, pool_evidence, refresh_source
from .job_locks import job_lock
from .news import fetch_source
from .nflverse import sync_schedule
from .pool_strategy import season_strategy
from .pool_week import _utc, _validate_requested_card, get_pool_week, save_weekly_card
from .providers import AnalysisOutput, run_analysis

REFRESH_REUSE_SECONDS = 60
PROPOSAL_MAX_AGE = timedelta(minutes=5)


class PoolAnalysisRequest(BaseModel):
    provider_id: int | None = None


class PoolApplyRequest(BaseModel):
    run_id: int


class AnalystPick(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    slot: int | None
    game_id: int
    team: str
    confidence: int | None


class PoolAnalysisOutput(AnalysisOutput):
    picks: list[AnalystPick] = Field(max_length=40)


def reject(code: str, message: str) -> None:
    raise PoolDomainError(status_code=409, code=code, message=message)


def require_current_proposal(proposal: dict) -> None:
    if datetime.now(UTC) - datetime.fromisoformat(proposal["created_at"]) > PROPOSAL_MAX_AGE:
        reject(
            "analysis_expired", "This analysis is over five minutes old. Refresh and analyze again."
        )


def source_config(db: Session) -> list[tuple]:
    return [
        (s.id, s.url, s.source_type)
        for s in db.query(NewsSource).filter(NewsSource.enabled.is_(True)).order_by(NewsSource.id)
    ]


async def refresh_pool_sources(db: Session, season: int, force: bool = False) -> dict:
    """Share a short check-in cooldown across entries; never disguise failures as fresh."""
    config = jsonable_encoder(source_config(db))
    latest = (
        db.query(DataSnapshot)
        .filter(DataSnapshot.source == "pool.check-in", DataSnapshot.source_id == str(season))
        .order_by(DataSnapshot.id.desc())
        .first()
    )
    if latest and not force:
        previous = json.loads(latest.payload_json)
        if (datetime.now(UTC) - _utc(latest.retrieved_at)).total_seconds() < REFRESH_REUSE_SECONDS:
            if previous.get("source_config") == config:
                return previous

    # News feeds are shared by every pool and season. This lease spans workers too.
    with job_lock("pool-source-refresh") as acquired:
        if not acquired:
            return {"status": "running", "checked_at": None, "sources": []}
        sources = []
        try:
            counts = await sync_schedule(db, season, trigger="pool-check-in")
            if not (counts.get("created", 0) or counts.get("updated", 0)):
                reject("empty_schedule", "The source returned no games for this season.")
            sources.append(
                {
                    "name": "NFL schedule, odds and results",
                    "status": "refreshed",
                    "checked_at": datetime.now(UTC).isoformat(),
                    "detail": "Latest available nflverse release; this is not a live odds feed.",
                }
            )
        except Exception:
            db.rollback()
            sources.append(
                {
                    "name": "NFL schedule, odds and results",
                    "status": "unavailable",
                    "checked_at": None,
                    "detail": "Refresh failed. Cached games and picks were kept.",
                }
            )

        async def refresh_news(source_id: int) -> dict:
            with SessionLocal() as news_db:
                source = news_db.get(NewsSource, source_id)
                name, url = source.name, source.url
                last_success = source.last_fetched_at
                try:
                    await fetch_source(news_db, source)
                    return {
                        "name": name,
                        "url": url,
                        "status": "refreshed",
                        "checked_at": _utc(source.last_fetched_at).isoformat(),
                    }
                except Exception:
                    news_db.rollback()
                    return {
                        "name": name,
                        "url": url,
                        "status": "unavailable",
                        "checked_at": _utc(last_success).isoformat() if last_success else None,
                        "detail": "Refresh failed. Cached evidence may be out of date.",
                    }

        for offset in range(0, len(config), 4):
            sources.extend(
                await asyncio.gather(*(refresh_news(row[0]) for row in config[offset : offset + 4]))
            )
        if not config:
            sources.append(
                {
                    "name": "News and injury sources",
                    "status": "unavailable",
                    "checked_at": None,
                    "detail": "No enabled sources are configured.",
                }
            )
        db.expire_all()
        sources.append(await refresh_source(SourceRequest("sleeper", season)))
        manual = db.query(Game).filter(Game.season == season, Game.source != "nflverse").all()
        if manual:
            sources.append(
                {
                    "name": "Manual game inputs",
                    "status": "manual",
                    "checked_at": min(_utc(g.source_timestamp) for g in manual).isoformat(),
                    "detail": "Manually entered probabilities and lines require your own updates.",
                }
            )
        result = {
            "status": "partial"
            if any(s["status"] == "unavailable" and s.get("required", True) for s in sources)
            else "ready",
            "checked_at": datetime.now(UTC).isoformat(),
            "sources": sources,
            "source_config": config,
        }
        db.add(
            DataSnapshot(
                source="pool.check-in",
                source_id=str(season),
                status=result["status"],
                payload_json=json.dumps(result),
            )
        )
        db.commit()
        return result


def pool_dossier(db: Session, pool_id: int, entry_id: int, week: int) -> dict:
    workspace = get_pool_week(db, pool_id=pool_id, entry_id=entry_id, week=week)
    pool = db.get(Pool, pool_id)
    sources = source_config(db)
    news = (
        db.query(NewsItem)
        .filter(NewsItem.source_id.in_([s[0] for s in sources]))
        .order_by(NewsItem.retrieved_at.desc(), NewsItem.id.desc())
        .limit(100)
        .all()
    )
    return jsonable_encoder(
        {
            "pool": {
                "id": pool_id,
                "weekly_card": workspace,
                "season_strategy": season_strategy(db, pool, week) if week <= 18 else None,
            },
            "season_games": [
                {
                    "id": g.id,
                    "week": g.week,
                    "away": g.away_team,
                    "home": g.home_team,
                    "kickoff": _utc(g.kickoff),
                    "home_win": g.home_win_probability,
                    "home_cover": g.home_cover_probability,
                    "win_kind": g.win_probability_kind,
                    "cover_kind": g.cover_probability_kind,
                    "spread_home": g.spread_home,
                    "total": g.total,
                    "home_score": g.home_score,
                    "away_score": g.away_score,
                    "completed": g.completed,
                    "source": g.source,
                    "source_timestamp": g.source_timestamp,
                }
                for g in db.query(Game).filter(Game.season == pool.season).order_by(Game.id)
            ],
            "entry_history": [
                {
                    "id": e.id,
                    "active": e.active,
                    "picks": [
                        {
                            "week": p.week,
                            "game_id": p.game_id,
                            "team": p.team,
                            "slot": p.slot,
                            "confidence": p.confidence,
                            "result": p.result,
                        }
                        for p in sorted(e.picks, key=lambda p: p.id)
                    ],
                }
                for e in sorted(pool.entries, key=lambda e: e.id)
            ],
            "news": [
                {
                    "id": n.id,
                    "title": n.title,
                    "excerpt": n.excerpt,
                    "url": n.canonical_url,
                    "source_name": n.source.name if n.source else None,
                    "source_official": n.source.official if n.source else False,
                    "published_at": n.published_at,
                    "retrieved_at": n.retrieved_at,
                    "content_hash": n.content_hash,
                }
                for n in news
            ],
            "coverage": "Up to 100 latest items from enabled news/injury sources. Check "
            "publication "
            "dates: a successful collection does not make an old article current. "
            "Odds are from nflverse's latest release; no real-time market feed is connected.",
            "source_config": sources,
            "supporting_player_status": pool_evidence(
                db,
                pool.season,
                {
                    team
                    for g in db.query(Game).filter_by(season=pool.season, week=week)
                    for team in (g.home_team, g.away_team)
                },
            ),
        }
    )


def evidence_hash(dossier: dict) -> str:
    # Ignore collection times when a refresh returns identical evidence.
    stable = json.loads(json.dumps(dossier))
    stable["pool"]["weekly_card"]["schedule"].pop("last_success_at", None)
    stable["pool"].pop("season_strategy", None)
    for game in stable["season_games"]:
        game.pop("source_timestamp", None)
    for key in ("freshness", "proposal", "data_access"):
        stable.pop(key, None)
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


def validate_proposal(db: Session, workspace: dict, output: PoolAnalysisOutput) -> WeeklyCardUpdate:
    """Use the same rules as a manual save, plus completeness and evidence requirements."""
    if workspace["entry"]["read_only"] or workspace["configuration_errors"]:
        reject("pool_not_editable", "This entry cannot accept picks. Check its status and rules.")
    payload = WeeklyCardUpdate(
        version=workspace["card"]["version"], picks=[pick.model_dump() for pick in output.picks]
    )
    if not output.picks or len(output.picks) != workspace["card"]["required_count"]:
        reject(
            "incomplete_ai_card", "AI did not provide a complete card. Review the missing evidence."
        )
    pool = db.get(Pool, workspace["pool"]["id"])
    entry = next(e for e in pool.entries if e.id == workspace["entry"]["id"])
    games = (
        db.query(Game)
        .filter(Game.season == pool.season, Game.week == workspace["week"]["number"])
        .all()
    )
    findings, conflicts = _validate_requested_card(
        payload=payload,
        pool=pool,
        entry=entry,
        week=workspace["week"]["number"],
        games=games,
        existing=[p for p in entry.picks if p.week == workspace["week"]["number"]],
        now=datetime.now(UTC),
    )
    if findings or conflicts:
        reject(
            "invalid_ai_card",
            "AI picks violate eligibility, weights or game locks. Run analysis again.",
        )
    by_id = {g["id"]: g for g in workspace["games"]}
    for pick in payload.picks:
        game = by_id[pick.game_id]
        if pool.pool_type == "confidence" and pick.confidence is None:
            reject("incomplete_ai_card", "AI must assign every confidence weight.")
        if not game["locked"] and not any(r["team"] == pick.team for r in game["recommendations"]):
            reject(
                "missing_pick_evidence",
                "A proposed pick has missing or stale probability evidence.",
            )
    return payload


async def analyze_pool(
    db: Session, pool_id: int, entry_id: int, week: int, provider_id: int | None
) -> dict:
    workspace = get_pool_week(db, pool_id=pool_id, entry_id=entry_id, week=week)
    provider = (
        db.get(AnalysisProvider, provider_id)
        if provider_id is not None
        else next(
            (
                p
                for p in db.query(AnalysisProvider)
                .filter(AnalysisProvider.enabled.is_(True))
                .order_by(AnalysisProvider.id)
                if "recommendation" in json.loads(p.task_defaults_json)
            ),
            None,
        )
    )
    if provider is None or not provider.enabled:
        reject("provider_unavailable", "Configure an enabled recommendation analyst in Settings.")
    with job_lock(f"pool-analysis-{pool_id}-{entry_id}") as acquired:
        if not acquired:
            reject("analysis_running", "An analysis is already running for this entry.")
        freshness = await refresh_pool_sources(db, workspace["pool"]["season"])
        if freshness["status"] == "running":
            reject(
                "refresh_running", "Sources are refreshing. Run analysis after the check finishes."
            )
        dossier = pool_dossier(db, pool_id, entry_id, week)
        dossier["freshness"] = freshness
        dossier["proposal"] = {
            "evidence_hash": evidence_hash(dossier),
            "version": dossier["pool"]["weekly_card"]["card"]["version"],
            "created_at": datetime.now(UTC).isoformat(),
        }
        run = await run_analysis(
            db,
            provider,
            "pool_picks",
            "Analyze this pool entry and propose its complete weekly card in picks. Use only "
            "the supplied evidence, explain choices and risks, and respect winner/loser and ATS "
            "rules. Preserve locked picks exactly. Survivor slots must be unique, use distinct "
            "games and eligible teams within usage limits. Confidence picks need every game and "
            "unique allowed weights. Use null confidence for survivor and null slot "
            "for confidence. "
            "Do not invent probabilities, injury details or missing choices; return empty picks "
            "if a valid complete card is unavailable. Source failures must be disclosed; no "
            "actionable picks when freshness is partial. No picks are saved by this analysis.",
            None,
            pool_id,
            dossier_override=dossier,
            pool_entry_id=entry_id,
            week=week,
            output_model=PoolAnalysisOutput,
        )
        output = (
            PoolAnalysisOutput.model_validate_json(run.output_json) if run.output_json else None
        )
        reason = run.error
        can_apply = False
        if output:
            try:
                require_current_proposal(dossier["proposal"])
                if freshness["status"] != "ready":
                    reject(
                        "sources_unavailable",
                        "Some sources could not refresh. Retry before setting AI picks.",
                    )
                db.expire_all()
                current = pool_dossier(db, pool_id, entry_id, week)
                if evidence_hash(current) != dossier["proposal"]["evidence_hash"]:
                    reject(
                        "evidence_changed",
                        "The card or evidence changed during analysis. Run it again.",
                    )
                validate_proposal(db, current["pool"]["weekly_card"], output)
                can_apply = True
            except (PoolDomainError, ValueError) as exc:
                reason = str(exc)
        return {
            "run_id": run.id,
            "output": output.model_dump() if output else None,
            "can_apply": can_apply,
            "reason": reason,
            "freshness": freshness,
            "version": dossier["proposal"]["version"],
        }


async def apply_pool_analysis(
    db: Session, pool_id: int, entry_id: int, week: int, run_id: int
) -> dict:
    workspace = get_pool_week(db, pool_id=pool_id, entry_id=entry_id, week=week)
    run = db.get(AnalysisRun, run_id)
    if run is None or run.task != "pool_picks" or run.status != "completed":
        reject("analysis_unavailable", "Choose a completed pool analysis.")
    dossier = json.loads(run.input_dossier_json)
    scope = dossier.get("data_access", {}).get("scope", {})
    if (scope.get("pool_id"), scope.get("pool_entry_id"), scope.get("week")) != (
        pool_id,
        entry_id,
        week,
    ):
        reject(
            "analysis_scope_mismatch", "This analysis belongs to a different pool, entry or week."
        )
    proposal = dossier["proposal"]
    require_current_proposal(proposal)
    freshness = await refresh_pool_sources(db, workspace["pool"]["season"])
    require_current_proposal(proposal)
    if freshness["status"] != "ready" or dossier["freshness"]["status"] != "ready":
        reject(
            "sources_unavailable",
            "Sources are not current. Refresh and analyze again before saving AI picks.",
        )
    db.expire_all()
    current = pool_dossier(db, pool_id, entry_id, week)
    if evidence_hash(current) != proposal["evidence_hash"]:
        reject(
            "evidence_changed", "The card, locks or source evidence changed. Run analysis again."
        )
    payload = validate_proposal(
        db, current["pool"]["weekly_card"], PoolAnalysisOutput.model_validate_json(run.output_json)
    )
    payload.version = proposal["version"]
    return save_weekly_card(db, entry_id=entry_id, week=week, payload=payload)
