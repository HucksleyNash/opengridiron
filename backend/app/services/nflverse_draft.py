from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import math
import re
import statistics
import unicodedata
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import DataSnapshot, IdentityMap, League, Player

MODEL_VERSION = "nflverse-range-v1"
MODEL_SOURCE_URL = "https://github.com/nflverse/nflverse-data"
ROSTER_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_{season}.csv"
)
STATS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "stats_player/stats_player_week_{season}.csv"
)
LOOKBACK_SEASONS = 3
PERCENTILE_Z = 0.8416212335729143

logger = logging.getLogger(__name__)
_sync_locks: dict[int, asyncio.Lock] = {}

DEFAULT_SCORING = {
    "passing_yards": 0.04,
    "passing_tds": 4.0,
    "interceptions": -1.0,
    "rushing_yards": 0.1,
    "rushing_tds": 6.0,
    "receiving_yards": 0.1,
    "receiving_tds": 6.0,
    "fumbles_lost": -2.0,
    "two_point_conversions": 2.0,
}

SCORING_ALIASES = {
    "passing_touchdowns": "passing_tds",
    "passing_td": "passing_tds",
    "passing_attempts": "attempts",
    "pass_attempts": "attempts",
    "rushing_attempts": "carries",
    "rush_attempts": "carries",
    "rushing_touchdowns": "rushing_tds",
    "rushing_td": "rushing_tds",
    "receiving_touchdowns": "receiving_tds",
    "receiving_td": "receiving_tds",
    "two_point_conversion": "two_point_conversions",
    "2_point_conversions": "two_point_conversions",
}

POSITION_PRIOR_WIDTH = {
    "QB": 0.20,
    "RB": 0.30,
    "WR": 0.28,
    "TE": 0.32,
    "K": 0.24,
    "DEF": 0.20,
    "DST": 0.20,
}


@dataclass(frozen=True)
class RosterIdentity:
    yahoo_id: str
    gsis_id: str | None
    name: str
    team: str
    position: str
    years_exp: int | None
    draft_number: int | None
    status: str


@dataclass
class HistoricalProfile:
    gsis_id: str
    weekly_points: list[float]
    weekly_usage: list[float]
    season_points: dict[int, float]
    season_games: dict[int, int]


@dataclass(frozen=True)
class RangeEstimate:
    floor: float
    ceiling: float
    risk: float
    metadata: dict[str, object]


@dataclass(frozen=True)
class CachedAsset:
    content: str
    sha256: str
    cache_file: str
    stale: bool


def _number(value: str | None) -> float:
    try:
        return float(value) if value not in {None, "", "NA"} else 0.0
    except ValueError:
        return 0.0


def _integer(value: str | None) -> int | None:
    try:
        return int(float(value)) if value not in {None, "", "NA"} else None
    except ValueError:
        return None


def _normalized_name(value: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_name.lower())


def _normalized_scoring(scoring: dict[str, float]) -> dict[str, float]:
    if not scoring:
        return dict(DEFAULT_SCORING)
    normalized: dict[str, float] = {}
    for raw_name, value in scoring.items():
        key = re.sub(r"[^a-z0-9]+", "_", raw_name.lower()).strip("_")
        key = re.sub(r"_yahoo_default$", "", key)
        key = SCORING_ALIASES.get(key, key)
        normalized[key] = float(value)
    return normalized


def parse_roster_identities(content: str) -> list[RosterIdentity]:
    identities: list[RosterIdentity] = []
    for row in csv.DictReader(io.StringIO(content)):
        yahoo_id = (row.get("yahoo_id") or "").strip()
        name = (row.get("full_name") or row.get("football_name") or "").strip()
        if not yahoo_id or not name:
            continue
        identities.append(
            RosterIdentity(
                yahoo_id=yahoo_id,
                gsis_id=(row.get("gsis_id") or "").strip() or None,
                name=name,
                team=(row.get("team") or "FA").strip().upper(),
                position=(row.get("position") or "UNK").strip().upper(),
                years_exp=_integer(row.get("years_exp")),
                draft_number=_integer(row.get("draft_number")),
                status=(row.get("status") or "").strip().upper(),
            )
        )
    return identities


def _fantasy_points(row: dict[str, str], scoring: dict[str, float]) -> float:
    values = {
        "completions": _number(row.get("completions")),
        "attempts": _number(row.get("attempts")),
        "passing_yards": _number(row.get("passing_yards")),
        "passing_tds": _number(row.get("passing_tds")),
        "interceptions": _number(row.get("passing_interceptions") or row.get("interceptions")),
        "passing_first_downs": _number(row.get("passing_first_downs")),
        "carries": _number(row.get("carries")),
        "rushing_yards": _number(row.get("rushing_yards")),
        "rushing_tds": _number(row.get("rushing_tds")),
        "rushing_first_downs": _number(row.get("rushing_first_downs")),
        "receptions": _number(row.get("receptions")),
        "targets": _number(row.get("targets")),
        "receiving_yards": _number(row.get("receiving_yards")),
        "receiving_tds": _number(row.get("receiving_tds")),
        "receiving_first_downs": _number(row.get("receiving_first_downs")),
        "two_point_conversions": sum(
            _number(row.get(column))
            for column in (
                "passing_2pt_conversions",
                "rushing_2pt_conversions",
                "receiving_2pt_conversions",
            )
        ),
        "fumbles_lost": sum(
            _number(row.get(column))
            for column in (
                "sack_fumbles_lost",
                "rushing_fumbles_lost",
                "receiving_fumbles_lost",
            )
        ),
        "return_touchdowns": _number(row.get("special_teams_tds")),
        "kickoff_return_yards": _number(row.get("kickoff_return_yards")),
        "punt_return_yards": _number(row.get("punt_return_yards")),
        "offensive_fumble_return_td": _number(row.get("fumble_recovery_tds")),
        "field_goals_0_19_yards": _number(row.get("fg_made_0_19")),
        "field_goals_20_29_yards": _number(row.get("fg_made_20_29")),
        "field_goals_30_39_yards": _number(row.get("fg_made_30_39")),
        "field_goals_40_49_yards": _number(row.get("fg_made_40_49")),
        "field_goals_50_yards": (
            _number(row.get("fg_made_50_59")) + _number(row.get("fg_made_60_"))
        ),
        "point_after_attempt_made": _number(row.get("pat_made")),
    }
    return sum(scoring.get(stat, 0.0) * value for stat, value in values.items())


def parse_historical_profiles(
    contents_by_season: dict[int, str], scoring: dict[str, float]
) -> dict[str, HistoricalProfile]:
    normalized_scoring = _normalized_scoring(scoring)
    points_by_week: dict[str, dict[tuple[int, int], float]] = {}
    usage_by_week: dict[str, dict[tuple[int, int], float]] = {}
    for expected_season, content in contents_by_season.items():
        for row in csv.DictReader(io.StringIO(content)):
            if (row.get("season_type") or "REG").upper() != "REG":
                continue
            gsis_id = (row.get("player_id") or row.get("gsis_id") or "").strip()
            season = _integer(row.get("season")) or expected_season
            week = _integer(row.get("week"))
            if not gsis_id or week is None:
                continue
            key = (season, week)
            points_by_week.setdefault(gsis_id, {}).setdefault(key, 0.0)
            points_by_week[gsis_id][key] += _fantasy_points(row, normalized_scoring)
            usage = sum(_number(row.get(column)) for column in ("attempts", "carries", "targets"))
            usage_by_week.setdefault(gsis_id, {}).setdefault(key, 0.0)
            usage_by_week[gsis_id][key] += usage

    profiles: dict[str, HistoricalProfile] = {}
    for gsis_id, weeks in points_by_week.items():
        season_points: dict[int, float] = {}
        season_games: dict[int, int] = {}
        ordered_keys = sorted(weeks)
        for season, _week in ordered_keys:
            season_points[season] = season_points.get(season, 0.0) + weeks[(season, _week)]
            season_games[season] = season_games.get(season, 0) + 1
        profiles[gsis_id] = HistoricalProfile(
            gsis_id=gsis_id,
            weekly_points=[weeks[key] for key in ordered_keys],
            weekly_usage=[usage_by_week.get(gsis_id, {}).get(key, 0.0) for key in ordered_keys],
            season_points=season_points,
            season_games=season_games,
        )
    return profiles


def _weighted_recent(values: dict[int, float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values.items(), reverse=True)
    weighted = [(value, max(1, 3 - index)) for index, (_season, value) in enumerate(ordered)]
    return sum(value * weight for value, weight in weighted) / sum(weight for _, weight in weighted)


def _status_penalty(status: str) -> float:
    value = status.strip().lower()
    if not value or value in {"active", "act", "healthy", "na"}:
        return 0.0
    if any(token in value for token in ("ir", "out", "pup", "suspend")):
        return 0.16
    if any(token in value for token in ("doubt", "question", "injur")):
        return 0.08
    return 0.04


def estimate_range(
    *,
    projected_points: float,
    position: str,
    profile: HistoricalProfile | None,
    years_exp: int | None,
    draft_number: int | None,
    status: str,
    matched: bool,
) -> RangeEstimate:
    mean = max(0.0, projected_points)
    normalized_position = position.upper()
    prior_width = POSITION_PRIOR_WIDTH.get(normalized_position, 0.30)
    total_games = len(profile.weekly_points) if profile else 0
    recent_total = _weighted_recent(profile.season_points) if profile else None
    average_games = (
        statistics.fmean(profile.season_games.values()) if profile and profile.season_games else 0.0
    )
    availability_gap = max(0.0, 1.0 - min(17.0, average_games) / 17.0)

    weekly_values = list(profile.weekly_points) if profile else []
    weekly_mean = statistics.fmean(weekly_values) if weekly_values else 0.0
    weekly_cv = (
        statistics.pstdev(weekly_values) / weekly_mean
        if len(weekly_values) >= 2 and weekly_mean > 0
        else prior_width * 2.0
    )
    weekly_cv = max(0.15, min(1.75, weekly_cv))
    season_noise = weekly_cv / math.sqrt(max(1.0, average_games or 10.0))
    role_shift = (
        abs(mean - recent_total) / max(mean, recent_total, 1.0) if recent_total is not None else 0.0
    )
    role_shift = min(1.0, role_shift)
    empirical_width = max(
        0.12,
        min(0.50, 0.10 + season_noise + 0.28 * availability_gap + 0.12 * role_shift),
    )
    sample_weight = min(0.82, total_games / 38.0)
    width = prior_width * (1.0 - sample_weight) + empirical_width * sample_weight

    rookie = years_exp == 0
    limited_sample = years_exp is None or years_exp <= 1 or total_games < 12
    rookie_width = 0.0
    if rookie:
        rookie_width = 0.035 if draft_number is not None and draft_number <= 64 else 0.065
    elif limited_sample:
        rookie_width = 0.025
    status_width = _status_penalty(status)
    floor_width = min(0.72, width + 0.18 * availability_gap + rookie_width + status_width)
    ceiling_width = min(0.65, width + 0.05 * role_shift + rookie_width * 0.8)

    floor = max(0.0, mean * (1.0 - floor_width))
    ceiling = max(mean, mean * (1.0 + ceiling_width))
    downside_sigma = max(1.0, mean * floor_width / PERCENTILE_Z)
    downside_z = (0.8 * mean - mean) / downside_sigma if mean else 0.0
    downside_probability = 0.5 * (1.0 + math.erf(downside_z / math.sqrt(2.0)))
    risk = min(0.90, max(0.05, downside_probability + status_width * 0.45))

    risk_factors: list[str] = []
    confidence_signals: list[str] = []
    if status_width:
        risk_factors.append(f"current Yahoo status: {status}")
    if rookie:
        risk_factors.append("rookie with no NFL season sample")
    elif limited_sample:
        risk_factors.append("limited NFL sample")
    if availability_gap >= 0.18:
        risk_factors.append("limited games or weekly role history")
    if weekly_cv >= 0.85 and total_games >= 8:
        risk_factors.append("volatile weekly production")
    if role_shift >= 0.28:
        risk_factors.append("Yahoo projection differs from recent scored output")
    if not risk_factors:
        confidence_signals.append("stable multi-season production sample")

    if matched and total_games >= 24:
        confidence = "high"
    elif matched and (total_games or rookie):
        confidence = "medium"
    else:
        confidence = "low"

    metadata: dict[str, object] = {
        "kind": "projection_range_model",
        "source": "nflverse",
        "source_url": MODEL_SOURCE_URL,
        "license": "CC-BY-4.0",
        "model_version": MODEL_VERSION,
        "range_definition": "empirical P20-P80 season-points estimate around Yahoo mean",
        "risk_definition": "estimated probability of finishing below 80% of Yahoo projection",
        "confidence": confidence,
        "historical_games": total_games,
        "historical_seasons": sorted(profile.season_points) if profile else [],
        "average_games_with_stats": round(average_games, 2),
        "weekly_cv": round(weekly_cv, 4),
        "recent_scored_points": round(recent_total, 2) if recent_total is not None else None,
        "projection_shift": round(role_shift, 4),
        "risk_factors": risk_factors,
        "confidence_signals": confidence_signals,
        "matched": matched,
    }
    return RangeEstimate(
        floor=round(floor, 2),
        ceiling=round(ceiling, 2),
        risk=round(risk, 4),
        metadata=metadata,
    )


def _player_yahoo_id(player: Player) -> str | None:
    if not player.source_id:
        return None
    match = re.search(r"(\d+)$", player.source_id)
    return match.group(1) if match else None


def _identity_for_player(
    player: Player,
    by_yahoo_id: dict[str, RosterIdentity],
    by_name_position: dict[tuple[str, str], list[RosterIdentity]],
) -> RosterIdentity | None:
    yahoo_id = _player_yahoo_id(player)
    if yahoo_id and yahoo_id in by_yahoo_id:
        return by_yahoo_id[yahoo_id]
    candidates = by_name_position.get((_normalized_name(player.name), player.position.upper()), [])
    if len(candidates) == 1:
        return candidates[0]
    team_matches = [item for item in candidates if item.team == player.pro_team.upper()]
    return team_matches[0] if len(team_matches) == 1 else None


def _replace_model_evidence(player: Player, model: dict[str, object]) -> None:
    try:
        existing = json.loads(player.evidence_json or "[]")
    except json.JSONDecodeError:
        existing = []
    if not isinstance(existing, list):
        existing = []
    retained = [
        item
        for item in existing
        if not isinstance(item, dict) or item.get("kind") != "projection_range_model"
    ]
    player.evidence_json = json.dumps([*retained, model])


def _upsert_identity_map(db: Session, identity: RosterIdentity) -> None:
    if not identity.gsis_id:
        return
    row = db.query(IdentityMap).filter(IdentityMap.gsis_id == identity.gsis_id).one_or_none()
    yahoo_conflict = (
        db.query(IdentityMap).filter(IdentityMap.yahoo_key == identity.yahoo_id).one_or_none()
    )
    if yahoo_conflict is not None and yahoo_conflict is not row:
        return
    if row is None:
        row = IdentityMap(
            canonical_name=identity.name,
            pro_team=identity.team,
            position=identity.position,
            yahoo_key=identity.yahoo_id,
            gsis_id=identity.gsis_id,
            confidence=1.0,
        )
        db.add(row)
    else:
        row.canonical_name = identity.name
        row.pro_team = identity.team
        row.position = identity.position
        row.yahoo_key = identity.yahoo_id


def apply_projection_ranges(
    db: Session,
    league: League,
    *,
    roster_content: str,
    stats_contents: dict[int, str],
) -> dict[str, object]:
    identities = parse_roster_identities(roster_content)
    by_yahoo_id = {item.yahoo_id: item for item in identities}
    by_name_position: dict[tuple[str, str], list[RosterIdentity]] = {}
    for identity in identities:
        key = (_normalized_name(identity.name), identity.position)
        by_name_position.setdefault(key, []).append(identity)
    scoring = json.loads(league.scoring_json or "{}")
    profiles = parse_historical_profiles(stats_contents, scoring)

    modeled = matched = historical = 0
    confidence_counts = {"high": 0, "medium": 0, "low": 0}
    players = db.query(Player).filter(Player.league_id == league.id).all()
    for player in players:
        if player.projected_points <= 0:
            continue
        identity = _identity_for_player(player, by_yahoo_id, by_name_position)
        profile = profiles.get(identity.gsis_id) if identity and identity.gsis_id else None
        estimate = estimate_range(
            projected_points=player.projected_points,
            position=player.position,
            profile=profile,
            years_exp=identity.years_exp if identity else None,
            draft_number=identity.draft_number if identity else None,
            status=player.status,
            matched=identity is not None,
        )
        metadata = dict(estimate.metadata)
        metadata.update(
            {
                "gsis_id": identity.gsis_id if identity else None,
                "yahoo_id": identity.yahoo_id if identity else _player_yahoo_id(player),
            }
        )
        player.floor = estimate.floor
        player.ceiling = estimate.ceiling
        player.risk = estimate.risk
        _replace_model_evidence(player, metadata)
        modeled += 1
        matched += int(identity is not None)
        historical += int(profile is not None)
        confidence = str(metadata["confidence"])
        confidence_counts[confidence] += 1
        if identity:
            _upsert_identity_map(db, identity)

    db.flush()
    return {
        "model_version": MODEL_VERSION,
        "modeled": modeled,
        "matched": matched,
        "historical": historical,
        "coverage": round(matched / modeled, 4) if modeled else 0.0,
        "historical_coverage": round(historical / modeled, 4) if modeled else 0.0,
        "confidence": confidence_counts,
        "seasons": sorted(stats_contents),
    }


async def _cached_download(url: str, filename: str, max_age: timedelta) -> CachedAsset:
    cache_dir = settings.data_dir / "cache" / "nflverse" / "draft-model"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / filename
    now = datetime.now(UTC)
    if path.exists():
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if now - modified <= max_age:
            content = path.read_text(encoding="utf-8")
            return CachedAsset(
                content=content,
                sha256=hashlib.sha256(content.encode()).hexdigest(),
                cache_file=path.name,
                stale=False,
            )
    try:
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
            response = await client.get(
                url,
                headers={"User-Agent": "OpenGridiron/0.1 (+nflverse range model)"},
            )
        response.raise_for_status()
        content = response.text
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
        return CachedAsset(
            content=content,
            sha256=hashlib.sha256(response.content).hexdigest(),
            cache_file=path.name,
            stale=False,
        )
    except (httpx.HTTPError, OSError):
        if not path.exists():
            raise
        content = path.read_text(encoding="utf-8")
        return CachedAsset(
            content=content,
            sha256=hashlib.sha256(content.encode()).hexdigest(),
            cache_file=path.name,
            stale=True,
        )


async def sync_projection_ranges(db: Session, league: League) -> dict[str, object]:
    if not settings.draft_nflverse_ranges_enabled:
        return {"status": "disabled", "model_version": MODEL_VERSION}
    lock = _sync_locks.setdefault(league.season, asyncio.Lock())
    async with lock:
        seasons = list(range(league.season - LOOKBACK_SEASONS, league.season))
        requests = [
            _cached_download(
                ROSTER_URL.format(season=league.season),
                f"roster-{league.season}.csv",
                timedelta(hours=24),
            ),
            *[
                _cached_download(
                    STATS_URL.format(season=season),
                    f"stats-player-week-{season}.csv",
                    timedelta(days=7),
                )
                for season in seasons
            ],
        ]
        results = await asyncio.gather(*requests, return_exceptions=True)

    roster_asset = results[0] if isinstance(results[0], CachedAsset) else None
    stats_assets: dict[int, CachedAsset] = {}
    errors: list[str] = []
    if roster_asset is None:
        errors.append(f"roster: {results[0]}")
    for season, result in zip(seasons, results[1:], strict=True):
        if isinstance(result, CachedAsset):
            stats_assets[season] = result
        else:
            errors.append(f"stats {season}: {result}")

    metrics = apply_projection_ranges(
        db,
        league,
        roster_content=roster_asset.content if roster_asset else "",
        stats_contents={season: asset.content for season, asset in stats_assets.items()},
    )
    assets = {
        "roster": asdict(roster_asset) if roster_asset else None,
        "stats": {
            str(season): {
                "sha256": asset.sha256,
                "cache_file": asset.cache_file,
                "stale": asset.stale,
            }
            for season, asset in stats_assets.items()
        },
    }
    if isinstance(assets["roster"], dict):
        assets["roster"].pop("content", None)
    stale_assets = bool(roster_asset and roster_asset.stale) or any(
        asset.stale for asset in stats_assets.values()
    )
    status = "fresh" if not errors and not stale_assets else "partial"
    payload = {**metrics, "assets": assets, "errors": errors}
    db.add(
        DataSnapshot(
            source="nflverse.draft_model",
            source_id=str(league.id),
            effective_at=datetime.now(UTC),
            status=status,
            payload_json=json.dumps(payload),
        )
    )
    db.commit()
    logger.info(
        "Applied %s to league %s: modeled=%s matched=%s historical=%s",
        MODEL_VERSION,
        league.id,
        metrics["modeled"],
        metrics["matched"],
        metrics["historical"],
    )
    return {"status": status, **payload}
