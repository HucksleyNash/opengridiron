"""Player-scoped weekly comparisons with immutable, pregame forecast evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from ..models import DataSnapshot, Game, League, LeagueAnalysis, Player
from .league_analysis import fetch_weekly_inputs
from .nflverse_draft import _normalized_name, _normalized_scoring
from .player_points_scoring import finite, weekly_actuals
from .weekly_forecast import (
    MODEL_VERSION,
    forecast_players,
    match_identities,
    parse_weekly_stats,
    position,
    team_code,
    utc,
)
from .yahoo_weekly import refresh_weekly_projections

CACHE_SOURCE = "player_points.forecast"
_locks: dict[int, asyncio.Lock] = {}


def now() -> datetime:
    return datetime.now(UTC)


def decode(value: str | None) -> dict:
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def timestamp(value: str | datetime | None) -> datetime | None:
    try:
        return utc(value if isinstance(value, datetime) else datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return None


def rules_key(scoring: dict | None) -> dict | None:
    return _normalized_scoring(scoring) if scoring else None


def metric(
    source: str,
    state: str,
    reason: str | None = None,
    *,
    points: float | None = None,
    captured_at: datetime | None = None,
    warnings: list[str] | None = None,
) -> dict:
    return {
        "points": points,
        "state": state,
        "reason": reason,
        "source": source,
        "captured_at": captured_at.isoformat() if captured_at else None,
        "warnings": warnings or [],
    }


def current_week(games: list[Game], at: datetime) -> int:
    def start(value: datetime):
        day = utc(value).astimezone(ZoneInfo("America/Chicago")).date()
        return day - timedelta(days=(day.weekday() - 1) % 7)

    ordered = sorted(games, key=lambda game: utc(game.kickoff))
    started = [game for game in ordered if utc(game.kickoff) <= at]
    if started and start(started[-1].kickoff) == start(at):
        return started[-1].week
    return next(
        (game.week for game in ordered if utc(game.kickoff) > at),
        ordered[-1].week if ordered else 1,
    )


def _candidate(
    payload: dict,
    player: Player,
    league: League,
    week: int,
    identity: str | None,
    captured: datetime | None,
) -> dict | None:
    kickoff = timestamp(payload.get("kickoff"))
    points = finite(payload.get("points"))
    if (
        payload.get("player_id") != player.id
        or payload.get("season") != league.season
        or payload.get("week") != week
        or not captured
        or not kickoff
        or captured >= kickoff
        or payload.get("method") in {None, "source_weekly_fallback", "unavailable"}
        or points is None
        or not payload.get("team")
    ):
        return None
    if identity and payload.get("gsis_id") and identity != payload["gsis_id"]:
        return None
    if payload.get("source_id") and payload["source_id"] != player.source_id:
        return None
    if payload.get("name") and _normalized_name(payload["name"]) != _normalized_name(player.name):
        return None
    warnings = list(payload.get("warnings") or [])
    if rules_key(payload.get("scoring")) != rules_key(decode(league.scoring_json)):
        warnings.append("Scoring changed: this saved projection uses different league rules.")
    elif not payload.get("scoring"):
        warnings.append("Scoring basis was not recorded.")
    return {
        **payload,
        "value": metric(
            "Open Gridiron", "available", points=points, captured_at=captured, warnings=warnings
        ),
    }


def _model_archive(
    db: Session, player: Player, league: League, week: int, identity: str | None, at: datetime
) -> dict | None:
    key = f"{league.id}:{player.id}:{league.season}:{week}"
    compact = None
    # DataSnapshot.payload_json is deferred: ordering/paging reads metadata only.
    snapshots = db.scalars(
        select(DataSnapshot)
        .where(
            DataSnapshot.source == CACHE_SOURCE,
            DataSnapshot.source_id == key,
            DataSnapshot.retrieved_at <= at,
        )
        .order_by(DataSnapshot.retrieved_at.desc(), DataSnapshot.id.desc())
    ).yield_per(25)
    for snapshot in snapshots:
        compact = _candidate(
            decode(snapshot.payload_json),
            player,
            league,
            week,
            identity,
            utc(snapshot.retrieved_at),
        )
        if compact:
            break

    # Legacy reports can be large. Find candidate metadata, then extract only this
    # player's forecast and scoring by primary key, never materializing report_json.
    reports = db.execute(
        select(LeagueAnalysis.id, LeagueAnalysis.completed_at)
        .where(
            LeagueAnalysis.league_id == league.id,
            LeagueAnalysis.season == league.season,
            LeagueAnalysis.week == week,
            LeagueAnalysis.completed_at.isnot(None),
            LeagueAnalysis.completed_at <= at,
            LeagueAnalysis.report_json.isnot(None),
        )
        .order_by(LeagueAnalysis.completed_at.desc(), LeagueAnalysis.id.desc())
    ).yield_per(25)
    forecasts = func.json_each(LeagueAnalysis.report_json, "$.forecasts").table_valued("value")
    for report in reports:
        captured = utc(report.completed_at)
        if compact and timestamp(compact["value"]["captured_at"]) >= captured:
            break
        row = db.execute(
            select(
                forecasts.c.value,
                func.json_extract(LeagueAnalysis.report_json, "$.scoring"),
                func.json_extract(LeagueAnalysis.report_json, "$.generated_at"),
                func.json_extract(LeagueAnalysis.report_json, "$.model_version"),
            )
            .select_from(LeagueAnalysis)
            .join(forecasts, true())
            .where(
                LeagueAnalysis.id == report.id,
                func.json_extract(forecasts.c.value, "$.player_id") == player.id,
            )
            .limit(1)
        ).first()
        if not row:
            continue
        payload = {
            **decode(row[0]),
            "season": league.season,
            "week": week,
            "scoring": decode(row[1]),
            "model_version": row[3],
        }
        generated, kickoff = timestamp(row[2]), timestamp(payload.get("kickoff"))
        if not generated or not kickoff or not generated <= captured < kickoff:
            continue
        candidate = _candidate(payload, player, league, week, identity, captured)
        if candidate:
            return candidate
    return compact


def _yahoo_settings(db: Session, league: League, captured: datetime, scoring: dict) -> dict | None:
    if not league.yahoo_key or not scoring:
        return None
    row = db.execute(
        select(DataSnapshot.id, DataSnapshot.retrieved_at)
        .where(
            DataSnapshot.source == "yahoo_scrape",
            DataSnapshot.source_id == league.yahoo_key,
            DataSnapshot.status.in_(["fresh", "partial"]),
            DataSnapshot.retrieved_at <= captured,
        )
        .order_by(DataSnapshot.retrieved_at.desc(), DataSnapshot.id.desc())
        .limit(1)
    ).first()
    if not row or captured - utc(row.retrieved_at) > timedelta(hours=6):
        return None
    saved = db.execute(
        select(
            func.json_extract(DataSnapshot.payload_json, "$.league.scoring"),
            func.json_extract(DataSnapshot.payload_json, "$.league.season"),
        ).where(DataSnapshot.id == row.id)
    ).one()
    if saved[1] == league.season and rules_key(decode(saved[0])) == rules_key(scoring):
        return {"snapshot_id": row.id, "captured_at": utc(row.retrieved_at).isoformat()}
    return None


def _yahoo_projection(
    db: Session,
    player: Player,
    league: League,
    week: int,
    game: Game | None,
    historical: bool,
    at: datetime,
) -> dict:
    empty = metric(
        "Yahoo",
        "not_saved" if historical else "not_published",
        "No eligible weekly Yahoo projection was saved before kickoff."
        if historical
        else "No verified Yahoo projection has been collected for this week.",
    )
    if not league.source.startswith("yahoo"):
        return metric("Yahoo", "unavailable", "This league is not connected to Yahoo.")
    provider = re.search(r"(?:\.p\.|:|^)(\d+)$", player.source_id or "")
    if not provider:
        return metric("Yahoo", "unavailable", "No Yahoo player identity is available.")
    if historical and game is None:
        return metric("Yahoo", "not_saved", "Historical kickoff could not be verified.")
    conditions = [
        DataSnapshot.source == "yahoo_weekly_projection",
        DataSnapshot.source_id == f"{league.id}:{league.season}:{week}",
        DataSnapshot.status == "fresh",
        DataSnapshot.retrieved_at <= at,
    ]
    if game:
        conditions.append(DataSnapshot.retrieved_at < game.kickoff)
    rows = db.execute(
        select(DataSnapshot.id, DataSnapshot.retrieved_at)
        .where(
            *conditions,
        )
        .order_by(DataSnapshot.retrieved_at.desc(), DataSnapshot.id.desc())
    ).yield_per(25)
    for row in rows:
        saved = db.execute(
            select(
                func.json_extract(DataSnapshot.payload_json, "$.context"),
                func.json_extract(DataSnapshot.payload_json, f'$.players."{provider[1]}".points'),
            ).where(DataSnapshot.id == row.id)
        ).one()
        context, points = decode(saved[0]), finite(saved[1])
        if (
            points is None
            or context.get("period") != "week"
            or context.get("season") != league.season
            or context.get("week") != week
        ):
            continue
        captured = utc(row.retrieved_at)
        warnings = []
        evidence = _yahoo_settings(db, league, captured, context.get("scoring"))
        if not evidence:
            warnings.append(
                "Yahoo scoring unverified: the saved table copies local scoring settings."
            )
        if rules_key(context.get("scoring")) != rules_key(decode(league.scoring_json)):
            warnings.append("Scoring changed: Yahoo's saved projection uses different settings.")
        if not historical and at - captured > timedelta(hours=6):
            warnings.append("Out of date: showing the last saved Yahoo projection.")
        return {
            **metric("Yahoo", "available", points=points, captured_at=captured, warnings=warnings),
            "scoring_evidence": evidence,
        }
    return empty


def _week_game(games: list[Game], team: str, week: int, opposing: str = "") -> Game | None:
    matches = [
        game
        for game in games
        if game.week == week
        and team in {team_code(game.home_team), team_code(game.away_team)}
        and (not opposing or opposing in {team_code(game.home_team), team_code(game.away_team)})
    ]
    return matches[0] if len(matches) == 1 else None


def _team_for_week(
    player: Player,
    week: int,
    active: int,
    actuals: dict,
    archive: dict | None,
    games: list[Game],
    at: datetime,
) -> str:
    if actuals.get(week, {}).get("team"):
        return actuals[week]["team"]
    current_team = team_code(player.pro_team)
    current_game = _week_game(games, current_team, week)
    current_schedule = week >= active and any(
        utc(game.kickoff) >= at - timedelta(days=7) for game in games if game.week >= week
    )
    # A future archive may predate a trade. Use the current club for games still
    # ahead (including its bye); retain recorded teams for historical games.
    if current_schedule and (
        current_game is None or (not current_game.completed and utc(current_game.kickoff) > at)
    ):
        return current_team
    if archive:
        return team_code(archive["team"])
    # Historical team continuity can establish a past bye without applying a
    # traded player's current club to all earlier weeks.
    earlier = [
        value["team"] for key, value in sorted(actuals.items()) if key < week and value["team"]
    ]
    later = [
        value["team"] for key, value in sorted(actuals.items()) if key > week and value["team"]
    ]
    if earlier and later and earlier[-1] == later[0]:
        return earlier[-1]
    if current_schedule:
        return current_team
    return ""


def _bye(games: list[Game], team: str, week: int) -> bool:
    official = [
        game
        for game in games
        if game.source == "nflverse"
        and team in {team_code(game.home_team), team_code(game.away_team)}
    ]
    weeks = {game.week for game in official}
    return bool(team and len(official) == len(weeks) == 17 and week not in weeks)


def _input_key(player: Player, league: League, game: Game, sources: list[dict]) -> str:
    value = [
        MODEL_VERSION,
        player.source_id,
        player.pro_team,
        player.status,
        league.scoring_json,
        game.id,
        game.home_team,
        game.away_team,
        utc(game.kickoff).isoformat(),
        [[source.get("name"), source.get("sha256"), source.get("status")] for source in sources],
    ]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


async def player_points(db: Session, player: Player, refresh: bool = False) -> dict:
    async with _locks.setdefault(player.id, asyncio.Lock()):
        return await _player_points(db, player, refresh)


async def _player_points(db: Session, player: Player, refresh: bool) -> dict:
    league = db.get(League, player.league_id)
    scoring = decode(league.scoring_json)
    games = list(
        db.scalars(
            select(Game)
            .where(
                Game.season == league.season,
                Game.week.between(1, 18),
            )
            .order_by(Game.kickoff)
        )
    )
    roster, contents, sources = await fetch_weekly_inputs(league.season)
    active = current_week(games, now())
    if refresh and league.source.startswith("yahoo"):
        _, yahoo_source = await refresh_weekly_projections(db, league, active)
        sources.append(yahoo_source)
    at = now()  # Capture time after any network activity, including Yahoo refresh.
    identities = match_identities([player], roster)
    identity = identities.get(player.id)
    role = position(player.position)
    actuals = weekly_actuals(contents, league.season, identity, role, scoring)
    parsed = parse_weekly_stats(contents, scoring) if scoring else {}
    # Current-season incomplete/future games must not enter upcoming forecasts.
    completed = {
        (game.week, team_code(team))
        for game in games
        if game.completed and utc(game.kickoff) <= at
        for team in (game.home_team, game.away_team)
    }
    stats = {
        key: [
            row
            for row in values
            if row["season"] < league.season or (row["week"], row.get("team")) in completed
        ]
        for key, values in parsed.items()
    }
    rows = []
    for week in range(1, 19):
        archive = _model_archive(db, player, league, week, identity, at)
        team = _team_for_week(player, week, active, actuals, archive, games, at)
        game = _week_game(games, team, week, actuals.get(week, {}).get("opponent", ""))
        bye = not game and _bye(games, team, week)
        historical = week < active or bool(game and utc(game.kickoff) <= at)
        if archive and (
            game is None
            or team_code(archive["team"]) != team
            or timestamp(archive["value"]["captured_at"]) >= utc(game.kickoff)
        ):
            archive = None
        model = (
            archive["value"]
            if archive
            else metric(
                "Open Gridiron",
                "not_saved" if historical else "unavailable",
                "No eligible independent forecast was saved before kickoff."
                if historical
                else "No game or reliable independent forecast is available.",
            )
        )
        if game and utc(game.kickoff) > at and not game.completed:
            input_key = _input_key(player, league, game, sources)
            cached = (
                archive
                and archive.get("input_key") == input_key
                and at - timestamp(archive["value"]["captured_at"]) < timedelta(minutes=5)
            )
            if not cached:
                forecast = forecast_players(league, [player], games, week, stats, identities, at)[0]
                captured = now()
                model = metric("Open Gridiron", "unavailable", forecast["reason"])
                if not scoring:
                    model["reason"] = "League scoring has not been configured."
                elif forecast["method"] == "source_weekly_fallback":
                    model["reason"] = (
                        "An independent forecast is unavailable; Yahoo is shown separately."
                    )
                elif forecast["method"] == "availability_zero" and week > active:
                    model["reason"] = (
                        "Availability unresolved: today's status cannot predict this later week."
                    )
                elif captured >= utc(game.kickoff):
                    model = (
                        archive["value"]
                        if archive
                        else metric(
                            "Open Gridiron",
                            "not_saved",
                            "Kickoff passed before a forecast could be saved.",
                        )
                    )
                elif finite(forecast["points"]) is not None:
                    payload = {
                        key: forecast.get(key)
                        for key in (
                            "player_id",
                            "gsis_id",
                            "name",
                            "team",
                            "method",
                            "warnings",
                            "points",
                        )
                    }
                    payload.update(
                        {
                            "league_id": league.id,
                            "season": league.season,
                            "week": week,
                            "source_id": player.source_id,
                            "model_version": MODEL_VERSION,
                            "scoring": scoring,
                            "scoring_provenance": "configured_league",
                            "kickoff": utc(game.kickoff).isoformat(),
                            "input_key": input_key,
                        }
                    )
                    # Identical evidence does not create another historical snapshot.
                    if not archive or any(
                        archive.get(key) != value for key, value in payload.items()
                    ):
                        db.add(
                            DataSnapshot(
                                source=CACHE_SOURCE,
                                source_id=f"{league.id}:{player.id}:{league.season}:{week}",
                                retrieved_at=captured,
                                status="fresh",
                                payload_json=json.dumps(payload),
                            )
                        )
                    model = metric(
                        "Open Gridiron",
                        "available",
                        points=forecast["points"],
                        captured_at=captured,
                        warnings=forecast["warnings"],
                    )
        yahoo = _yahoo_projection(db, player, league, week, game, historical, at)
        state = (
            "bye"
            if bye
            else "final"
            if game and game.completed
            else (
                "pending" if game and utc(game.kickoff) <= at else "upcoming" if game else "unknown"
            )
        )
        actual = metric(
            "NFLverse · league scoring",
            "pending" if game else "unavailable",
            "Waiting for final game statistics."
            if game
            else "Historical game could not be verified.",
        )
        if state == "final":
            value = actuals.get(week)
            source_name = f"NFL {'defense ' if role == 'DEF' else ''}statistics {league.season}"
            source = next((item for item in sources if item["name"] == source_name), {})
            points = value.get("points") if value else None
            actual = metric(
                "NFLverse · league scoring",
                "available" if points is not None else "unavailable",
                value.get("reason") if value else "No complete player stat row has been published.",
                points=points,
                captured_at=timestamp(source.get("received_at")),
                warnings=["Statistics are out of date; showing cached results."]
                if source.get("status") == "stale"
                else [],
            )
        if bye:
            model, yahoo, actual = [
                metric(source, "bye", "Confirmed bye week.")
                for source in ("Open Gridiron", "Yahoo", "NFLverse · league scoring")
            ]
        rows.append(
            {
                "week": week,
                "game_id": game.id if game else None,
                "team": team or None,
                "opponent": (
                    game.away_team if team_code(game.home_team) == team else game.home_team
                )
                if game
                else None,
                "home": team_code(game.home_team) == team if game else None,
                "kickoff": utc(game.kickoff).isoformat() if game else None,
                "state": state,
                "opengridiron": model,
                "yahoo": yahoo,
                "actual": actual,
            }
        )
    db.commit()
    return {
        "player_id": player.id,
        "league_id": league.id,
        "league_name": league.name,
        "season": league.season,
        "current_week": active,
        "scoring": scoring,
        "generated_at": now().isoformat(),
        "sources": sources,
        "weeks": rows,
    }
