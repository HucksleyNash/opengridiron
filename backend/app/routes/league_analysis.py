from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies import current_owner
from ..models import AnalysisProvider, Game, League, LeagueAnalysis, Player
from ..services.decision_freshness import (
    recommendation_gate,
    withhold_decisions,
    yahoo_roster_source,
)
from ..services.league_analysis import ACTIVE, execute_league_analysis, fetch_weekly_inputs
from ..services.weekly_forecast import (
    BENCH,
    MODEL_VERSION,
    forecast_players,
    input_fingerprint,
    match_identities,
    parse_weekly_stats,
    team_decisions,
    utc,
)
from ..services.yahoo_weekly import saved_weekly_projections

router = APIRouter(prefix="/api/v1/leagues", dependencies=[Depends(current_owner)])
Db = Annotated[Session, Depends(get_db)]


class AnalysisStart(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    team_name: str = Field(min_length=1, max_length=160)
    week: int = Field(ge=1, le=18)
    provider_id: int | None = Field(default=None, ge=1)


def league_or_404(db: Session, league_id: int) -> League:
    league = db.get(League, league_id)
    if league is None:
        raise HTTPException(404, "League not found")
    return league


def summary(run: LeagueAnalysis) -> dict:
    return {
        "id": run.id,
        "league_id": run.league_id,
        "team_name": run.team_name,
        "season": run.season,
        "week": run.week,
        "status": run.status,
        "created_at": utc(run.created_at),
        "completed_at": utc(run.completed_at) if run.completed_at else None,
        "has_report": bool(run.report_json),
        "error": run.error,
    }


@router.get("/{league_id}/analysis-context")
def analysis_context(league_id: int, db: Db) -> dict:
    league = league_or_404(db, league_id)
    teams = [
        name
        for (name,) in db.query(Player.rostered_by)
        .filter(
            Player.league_id == league_id, Player.rostered_by.isnot(None), Player.rostered_by != ""
        )
        .distinct()
        .order_by(Player.rostered_by)
        .all()
    ]
    games = (
        db.query(Game)
        .filter(Game.season == league.season, Game.week <= 18)
        .order_by(Game.kickoff)
        .all()
    )
    now = datetime.now(UTC)
    upcoming = next((g for g in games if utc(g.kickoff) > now), None)
    return {
        "teams": teams,
        "suggested_week": upcoming.week if upcoming else (games[-1].week if games else 1),
        "schedule_available": bool(games),
    }


@router.get("/{league_id}/weekly-lineup")
async def weekly_lineup(
    league_id: int,
    db: Db,
    team_name: str = Query(min_length=1, max_length=160),
    week: int | None = Query(default=None, ge=1, le=18),
    mode: Literal["floor", "balanced", "ceiling"] = "balanced",
) -> dict:
    """Use independent weekly inputs for both roster rows and lineup decisions."""
    league = league_or_404(db, league_id)
    roster = (
        db.query(Player)
        .filter(Player.league_id == league_id, Player.rostered_by == team_name)
        .order_by(Player.id)
        .all()
    )
    if not roster:
        raise HTTPException(422, "Select a fantasy team with a saved roster in this league")
    games = (
        db.query(Game)
        .filter(Game.season == league.season, Game.week.between(1, 18))
        .order_by(Game.kickoff)
        .all()
    )
    now = datetime.now(UTC)
    if week is None:
        upcoming = next((game for game in games if utc(game.kickoff) > now), None)
        week = upcoming.week if upcoming else (games[-1].week if games else 1)
    roster_csv, contents, sources = await fetch_weekly_inputs(league.season)
    forecasts = forecast_players(
        league,
        roster,
        games,
        week,
        parse_weekly_stats(contents, json.loads(league.scoring_json)),
        match_identities(roster, roster_csv),
        now,
        saved_weekly_projections(db, league, week)[0],
    )
    # Keep the central weekly forecast for roster display; change only the objective.
    objective = "points" if mode == "balanced" else mode
    scored = [{**player, "points": player[objective]} for player in forecasts]
    decisions = team_decisions(scored, json.loads(league.roster_slots_json), team_name)
    roster_source = yahoo_roster_source(db, league, now)
    if roster_source:
        sources.append(roster_source)
    stale_reasons = recommendation_gate(sources, league.season, week)
    decisions = withhold_decisions(decisions, stale_reasons)
    assignments = [{**item, "score": item["points"]} for item in decisions["assignments"]]
    has_current = any(
        player["points"] is not None
        and player["current_slot"]
        and player["current_slot"].upper() not in BENCH
        for player in scored
    )
    has_recommended = any(item["score"] is not None for item in assignments)
    current_total = decisions.get("current_points") if has_current else None
    projected_total = decisions.get("recommended_points") if has_recommended else None
    return {
        "mode": mode,
        "season": league.season,
        "week": week,
        "source": "Open Gridiron weekly model",
        "model_version": MODEL_VERSION,
        "forecasts": forecasts,
        "assignments": assignments,
        "current_total": current_total,
        "projected_total": projected_total,
        "projected_gain": round(projected_total - current_total, 2)
        if current_total is not None and projected_total is not None
        else None,
        "partial_total": decisions.get("partial_total", True),
        "unfilled_slots": decisions.get("unfilled_slots", []),
        "error": decisions.get("error"),
        "sources": sources,
        "stale_reasons": stale_reasons,
        "data_as_of": now,
    }


@router.post("/{league_id}/analyses", status_code=202)
def start_analysis(
    league_id: int, payload: AnalysisStart, background: BackgroundTasks, db: Db
) -> dict:
    league = league_or_404(db, league_id)
    if (
        not db.query(Player.id)
        .filter(Player.league_id == league_id, Player.rostered_by == payload.team_name)
        .first()
    ):
        raise HTTPException(422, "Select a fantasy team with a saved roster in this league")
    active = (
        db.query(LeagueAnalysis)
        .filter(LeagueAnalysis.league_id == league_id, LeagueAnalysis.status.in_(ACTIVE))
        .first()
    )
    if active:
        if (
            active.team_name == payload.team_name
            and active.week == payload.week
            and (payload.provider_id is None or payload.provider_id == active.provider_id)
        ):
            return summary(active)
        raise HTTPException(
            409, "An analysis is already running for this league. Wait for it to finish."
        )
    provider = db.get(AnalysisProvider, payload.provider_id) if payload.provider_id else None
    if payload.provider_id and (provider is None or not provider.enabled):
        raise HTTPException(422, "Select an enabled analysis provider")
    if provider is None:
        providers = (
            db.query(AnalysisProvider)
            .filter(AnalysisProvider.enabled.is_(True))
            .order_by(AnalysisProvider.id)
            .all()
        )
        provider = next(
            (
                p
                for task in ("recommendation", "chat")
                for p in providers
                if task in json.loads(p.task_defaults_json)
            ),
            None,
        )
    run = LeagueAnalysis(
        league_id=league.id,
        team_name=payload.team_name,
        season=league.season,
        week=payload.week,
        provider_id=provider.id if provider else None,
        status="queued",
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "An analysis is already running for this league") from exc
    db.refresh(run)
    background.add_task(execute_league_analysis, run.id)
    return summary(run)


@router.get("/{league_id}/analyses")
def list_analyses(
    league_id: int, db: Db, team_name: str | None = Query(default=None, max_length=160)
) -> list[dict]:
    league_or_404(db, league_id)
    query = db.query(LeagueAnalysis).filter(LeagueAnalysis.league_id == league_id)
    if team_name:
        query = query.filter(LeagueAnalysis.team_name == team_name)
    return [summary(run) for run in query.order_by(LeagueAnalysis.id.desc()).limit(20).all()]


@router.get("/{league_id}/analyses/{run_id}")
def get_analysis(league_id: int, run_id: int, db: Db) -> dict:
    league = league_or_404(db, league_id)
    run = db.get(LeagueAnalysis, run_id)
    if run is None or run.league_id != league_id:
        raise HTTPException(404, "Analysis not found")
    report = json.loads(run.report_json) if run.report_json else None
    stale_reasons = []
    if report:
        players = db.query(Player).filter(Player.league_id == league_id).all()
        games = db.query(Game).filter(Game.season == run.season, Game.week <= 18).all()
        if input_fingerprint(league, players, games) != run.input_hash:
            stale_reasons.append(
                "Roster, availability, scoring, or schedule changed since this run."
            )
        generated = datetime.fromisoformat(report["generated_at"])
        now = datetime.now(UTC)
        if now - generated > timedelta(hours=24):
            stale_reasons.append("This report is more than 24 hours old.")
        if any(
            not p["locked"] and p["kickoff"] and datetime.fromisoformat(p["kickoff"]) <= now
            for p in report["forecasts"]
        ):
            stale_reasons.append("Games have started since this run. Recheck lineup locks.")
    return {**summary(run), "report": report, "stale_reasons": stale_reasons}
