from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import DataSnapshot, Game, IdentityMap
from ..pool_errors import PoolDomainError, ScheduleSyncError
from .decision import no_vig_probability
from .pool_outcomes import settle_season
from .team_strength import schedule_forecasts

SCHEDULE_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
ROSTER_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_{season}.csv"
)
ACCEPTED_GAME_TYPES = {"REG", "WC", "DIV", "CON", "SB"}
logger = logging.getLogger(__name__)
_schedule_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def nfl_season_for_date(value: date | datetime) -> int:
    """Map January and February postseason dates to their starting NFL season."""
    return value.year - 1 if value.month <= 2 else value.year


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ParsedGame:
    season: int
    week: int
    away_team: str
    home_team: str
    kickoff: datetime
    home_win_probability: float
    home_cover_probability: float
    spread_home: float | None
    total: float | None
    source_game_key: str | None
    source_game_key_kind: str | None
    win_probability_kind: str
    cover_probability_kind: str
    home_score: int | None
    away_score: int | None
    model_json: str

    @property
    def identity(self) -> tuple[str, str] | None:
        if self.source_game_key and self.source_game_key_kind:
            return self.source_game_key_kind, self.source_game_key
        return None

    @property
    def matchup(self) -> tuple[int, int, str, str]:
        return self.season, self.week, self.away_team, self.home_team


def _float(value: str | None) -> float | None:
    try:
        result = float(value) if value not in {None, "", "NA"} else None
        return result if result is not None and math.isfinite(result) else None
    except ValueError:
        return None


def _american_probability(odds: float) -> float:
    if odds == 0:
        raise ValueError("American odds cannot be zero")
    return 100 / (odds + 100) if odds > 0 else -odds / (-odds + 100)


def _kickoff(row: dict[str, str]) -> datetime:
    date_raw = row.get("gameday") or row.get("game_date")
    if not date_raw:
        return datetime.now(UTC)
    game_date = datetime.fromisoformat(date_raw).date()
    clock_raw = row.get("gametime") or "12:00"
    try:
        clock = time.fromisoformat(clock_raw)
    except ValueError:
        clock = time(12)
    return datetime.combine(game_date, clock, ZoneInfo("America/New_York")).astimezone(UTC)


def _probabilities(row: dict[str, str]) -> tuple[float, float, str, str]:
    home_ml = _float(row.get("home_moneyline"))
    away_ml = _float(row.get("away_moneyline"))
    if home_ml is not None and away_ml is not None:
        home_raw = _american_probability(home_ml)
        away_raw = _american_probability(away_ml)
        home_win, _away_win = no_vig_probability(1 / home_raw, 1 / away_raw)
        win_kind = "market"
    else:
        home_win = 0.5
        win_kind = "unavailable"
    home_spread_odds = _float(row.get("home_spread_odds"))
    away_spread_odds = _float(row.get("away_spread_odds"))
    if home_spread_odds is not None and away_spread_odds is not None:
        home_raw = _american_probability(home_spread_odds)
        away_raw = _american_probability(away_spread_odds)
        home_cover, _away_cover = no_vig_probability(1 / home_raw, 1 / away_raw)
        cover_kind = "market"
    else:
        home_cover = 0.5
        cover_kind = "unavailable"
    return (
        min(0.99, max(0.01, home_win)),
        min(0.99, max(0.01, home_cover)),
        win_kind,
        cover_kind,
    )


def _source_identity(row: dict[str, str]) -> tuple[str | None, str | None]:
    for kind, column in (("gsis", "gsis"), ("game_id", "game_id")):
        value = (row.get(column) or "").strip()
        if value and value.upper() != "NA":
            return kind, value
    return None, None


def _parsed_schedule(content: str, season: int) -> list[ParsedGame]:
    parsed: list[ParsedGame] = []
    forecasts, benchmark = schedule_forecasts(content, season)
    identities: dict[tuple[str, str], tuple[int, int, str, str]] = {}
    matchups: set[tuple[int, int, str, str]] = set()
    for row in csv.DictReader(io.StringIO(content)):
        if int(row.get("season") or 0) != season:
            continue
        if (row.get("game_type") or "").upper() not in ACCEPTED_GAME_TYPES:
            continue
        away = (row.get("away_team") or "").strip().upper()
        home = (row.get("home_team") or "").strip().upper()
        week = int(row.get("week") or 0)
        if not away or not home or away == home or not week:
            continue
        home_win, home_cover, win_kind, cover_kind = _probabilities(row)
        model_probability = forecasts.get((season, week, away, home))
        if win_kind == "unavailable" and model_probability is not None:
            home_win, win_kind = model_probability, "model"
        home_score, away_score = _float(row.get("home_score")), _float(row.get("away_score"))
        spread = _float(row.get("spread_line"))
        source_kind, source_key = _source_identity(row)
        item = ParsedGame(
            season=season,
            week=week,
            away_team=away,
            home_team=home,
            kickoff=_kickoff(row),
            home_win_probability=home_win,
            home_cover_probability=home_cover,
            spread_home=-spread if spread is not None else None,
            total=_float(row.get("total_line")),
            source_game_key=source_key,
            source_game_key_kind=source_kind,
            win_probability_kind=win_kind,
            cover_probability_kind=cover_kind,
            home_score=int(home_score) if home_score is not None and home_score >= 0 else None,
            away_score=int(away_score) if away_score is not None and away_score >= 0 else None,
            model_json=json.dumps({**benchmark, "home_win_probability": model_probability}),
        )
        if item.matchup in matchups:
            raise ScheduleSyncError(
                status_code=409,
                code="schedule_identity_conflict",
                message="The schedule contains conflicting game identities.",
            )
        matchups.add(item.matchup)
        if item.identity:
            prior_matchup = identities.get(item.identity)
            if prior_matchup and prior_matchup != item.matchup:
                raise ScheduleSyncError(
                    status_code=409,
                    code="schedule_identity_conflict",
                    message="The schedule contains conflicting game identities.",
                )
            identities[item.identity] = item.matchup
        parsed.append(item)
    return parsed


def parse_schedule(db: Session, content: str, season: int) -> dict[str, int]:
    parsed = _parsed_schedule(content, season)
    existing = db.query(Game).filter(Game.season == season).all()
    by_identity = {
        (game.source_game_key_kind, game.source_game_key): game
        for game in existing
        if game.source_game_key_kind and game.source_game_key
    }
    by_matchup = {
        (game.season, game.week, game.away_team, game.home_team): game for game in existing
    }
    plans: list[tuple[ParsedGame, Game | None]] = []

    for item in parsed:
        game = by_identity.get(item.identity) if item.identity else None
        occupant = by_matchup.get(item.matchup)
        if game is not None and occupant is not None and occupant is not game:
            raise ScheduleSyncError(
                status_code=409,
                code="schedule_identity_conflict",
                message="The imported schedule conflicts with a cached game identity.",
            )
        if game is None and occupant is not None:
            if item.identity and occupant.source_game_key:
                raise ScheduleSyncError(
                    status_code=409,
                    code="schedule_identity_conflict",
                    message="The imported schedule conflicts with a cached game identity.",
                )
            game = occupant
        plans.append((item, game))

    created = updated = 0
    source_timestamp = datetime.now(UTC)
    for item, game in plans:
        values = {
            "week": item.week,
            "away_team": item.away_team,
            "home_team": item.home_team,
            "kickoff": item.kickoff,
            "home_win_probability": item.home_win_probability,
            "home_cover_probability": item.home_cover_probability,
            "spread_home": item.spread_home,
            "total": item.total,
            "source": "nflverse",
            "source_timestamp": source_timestamp,
            "source_game_key": item.source_game_key,
            "source_game_key_kind": item.source_game_key_kind,
            "win_probability_kind": item.win_probability_kind,
            "cover_probability_kind": item.cover_probability_kind,
            "home_score": item.home_score,
            "away_score": item.away_score,
            "completed": item.home_score is not None
            and item.away_score is not None
            and item.kickoff < source_timestamp,
            "model_json": item.model_json,
        }
        if game:
            if game.locked_at is None and _as_utc(game.kickoff) <= source_timestamp:
                game.locked_at = source_timestamp
            for key, value in values.items():
                setattr(game, key, value)
            updated += 1
        else:
            db.add(Game(season=season, **values))
            created += 1
    db.flush()
    settle_season(db, season)
    return {"created": created, "updated": updated}


def parse_rosters(db: Session, content: str) -> dict[str, int]:
    created = updated = 0
    for row in csv.DictReader(io.StringIO(content)):
        gsis_id = row.get("gsis_id") or row.get("player_id")
        name = (
            row.get("full_name")
            or row.get("player_name")
            or " ".join(value for value in [row.get("first_name"), row.get("last_name")] if value)
        )
        team = (row.get("team") or "FA").upper()
        position = (row.get("position") or "UNK").upper()
        if not gsis_id or not name.strip():
            continue
        identity = db.query(IdentityMap).filter(IdentityMap.gsis_id == gsis_id).one_or_none()
        if identity:
            identity.canonical_name = name.strip()
            identity.pro_team = team
            identity.position = position
            updated += 1
        else:
            db.add(
                IdentityMap(
                    canonical_name=name.strip(),
                    pro_team=team,
                    position=position,
                    gsis_id=gsis_id,
                    confidence=1.0,
                )
            )
            created += 1
    return {"created": created, "updated": updated}


async def _download(url: str, kind: str, season: int) -> tuple[str, Path, str]:
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "OpenGridiron/0.1"})
    response.raise_for_status()
    content = response.text
    digest = hashlib.sha256(response.content).hexdigest()
    target_dir = settings.data_dir / "cache" / "nflverse"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{kind}-{season}-{digest[:12]}.csv"
    target.write_text(content, encoding="utf-8")
    return content, target, digest


async def sync_schedule(db: Session, season: int, trigger: str = "retry") -> dict[str, int | str]:
    latest_before = (
        db.query(DataSnapshot.id)
        .filter(
            DataSnapshot.source == "nflverse.schedule",
            DataSnapshot.source_id == str(season),
        )
        .order_by(DataSnapshot.id.desc())
        .limit(1)
        .scalar()
    )
    had_games = db.query(Game.id).filter(Game.season == season).first() is not None
    async with _schedule_locks[season]:
        db.expire_all()
        latest_now = (
            db.query(DataSnapshot.id)
            .filter(
                DataSnapshot.source == "nflverse.schedule",
                DataSnapshot.source_id == str(season),
            )
            .order_by(DataSnapshot.id.desc())
            .limit(1)
            .scalar()
        )
        has_games = db.query(Game.id).filter(Game.season == season).first() is not None
        if trigger == "missing" and has_games and (not had_games or latest_now != latest_before):
            return {"created": 0, "updated": 0, "status": "coalesced"}
        try:
            content, target, digest = await _download(SCHEDULE_URL, "schedule", season)
            counts = parse_schedule(db, content, season)
            db.add(
                DataSnapshot(
                    source="nflverse.schedule",
                    source_id=str(season),
                    effective_at=datetime.now(UTC),
                    payload_json=json.dumps(
                        {"sha256": digest, "cache_file": target.name, "trigger": trigger, **counts}
                    ),
                )
            )
            db.commit()
            return {**counts, "sha256": digest, "status": "completed"}
        except PoolDomainError:
            db.rollback()
            raise
        except (httpx.HTTPError, OSError) as exc:
            db.rollback()
            logger.exception("nflverse schedule download failed for season %s", season)
            raise ScheduleSyncError(
                status_code=502,
                code="schedule_download_failed",
                message="The NFL schedule could not be downloaded. Your cached games were kept.",
            ) from exc
        except Exception as exc:
            db.rollback()
            logger.exception("nflverse schedule import failed for season %s", season)
            raise ScheduleSyncError(
                status_code=502,
                code="schedule_import_failed",
                message="The NFL schedule could not be imported. Your cached games were kept.",
            ) from exc


async def sync_rosters(db: Session, season: int) -> dict[str, int | str]:
    content, target, digest = await _download(ROSTER_URL.format(season=season), "rosters", season)
    counts = parse_rosters(db, content)
    db.add(
        DataSnapshot(
            source="nflverse.rosters",
            source_id=str(season),
            effective_at=datetime.now(UTC),
            payload_json=json.dumps({"sha256": digest, "cache_file": target.name, **counts}),
        )
    )
    db.commit()
    return {**counts, "sha256": digest}
