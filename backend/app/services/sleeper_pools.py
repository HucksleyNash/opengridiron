"""Read public Sleeper survivor settings without credentials or pick submission."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Pool, PoolEntry, PoolPick
from ..pool_errors import PoolDomainError
from ..schemas import PoolRules, SleeperEntryInfo, SleeperPoolInfo, SleeperPoolRequest

API_ROOT = "https://api.sleeper.app/v1"
HISTORY_WARNING = (
    "Sleeper picks and pick history are not imported. Team availability and season plans "
    "only include picks saved here; compare them with your Sleeper history. "
    "Submit your actual picks on Sleeper."
)


def league_id_from_url(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[0-9]{1,32}", value):
        return value
    try:
        parsed = urlparse(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in {"sleeper.com", "www.sleeper.com", "sleeper.app"}
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
        )
        match = re.fullmatch(r"/leagues/([0-9]{1,32})/?", parsed.path)
        if valid and match:
            return match[1]
    except ValueError:
        pass
    raise PoolDomainError(
        status_code=422,
        code="invalid_sleeper_url",
        message="Enter a Sleeper league URL such as https://sleeper.com/leagues/123, or its ID.",
    )


async def _get(client: httpx.AsyncClient, path: str) -> Any:
    try:
        response = await client.get(f"{API_ROOT}/{path}")
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise ValueError("Response too large")
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PoolDomainError(
            status_code=502,
            code="sleeper_unavailable",
            message=(
                "Sleeper could not return this pool's data. "
                "Try again shortly; saved data is unchanged."
            ),
        ) from exc


def _integer(value: Any) -> int | None:
    # Unknown or missing settings must not silently become supported defaults.
    return value if type(value) is int else None


def _rules(league: dict) -> tuple[PoolRules | None, list[str]]:
    settings = league.get("settings") or {}
    unsupported = []
    checks = [
        (
            league.get("sport") == "pickem:nfl" and _integer(settings.get("pickem_type")) == 1,
            "Only NFL survivor pools are supported.",
        ),
        (league.get("season_type") == "regular", "Only regular-season pools are supported."),
        (
            _integer(settings.get("num_revives_allowed")) == 0,
            "Revives are enabled or unspecified; revival rules are not supported.",
        ),
        (
            _integer(settings.get("use_spread")) == 0,
            "Spread scoring is enabled or unspecified. "
            "Sleeper's locked spreads are unavailable here.",
        ),
        (
            _integer(settings.get("use_confidence")) == 0,
            "Confidence scoring is enabled or unspecified.",
        ),
        (_integer(settings.get("scoring_type")) == 0, "Unrecognized survivor scoring type."),
        (_integer(settings.get("daily_advance")) == 0, "Unrecognized weekly advance setting."),
        (
            all(
                _integer(settings.get(key)) == 0 for key in ("perfect_bonus", "perfect_week_bonus")
            ),
            "Bonus scoring is enabled or unspecified.",
        ),
    ]
    unsupported.extend(message for valid, message in checks if not valid)
    weekly = _integer(settings.get("weekly_pick_limit"))
    uses = _integer(settings.get("num_picks_allowed_per_team"))
    if weekly is None or not 1 <= weekly <= 10:
        unsupported.append("The weekly pick limit is missing or unsupported.")
    if uses is None or not 1 <= uses <= 18:
        unsupported.append("The team reuse limit is missing or unsupported.")
    if unsupported:
        return None, unsupported
    return PoolRules(
        direction="winner",
        basis="straight_up",
        picks_per_week=weekly,
        max_team_uses=uses,
        tie_result="eliminate",
        lock_mode="game_start",
    ), []


async def preview_pool(payload: SleeperPoolRequest) -> SleeperPoolInfo:
    league_id = league_id_from_url(payload.url)
    username = payload.username.strip()
    if username and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", username):
        raise PoolDomainError(
            status_code=422,
            code="invalid_sleeper_username",
            message="Enter your Sleeper username without spaces or a profile URL.",
        )
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        league = await _get(client, f"league/{league_id}")
        if league is None:
            raise PoolDomainError(
                status_code=404,
                code="sleeper_pool_not_found",
                message="Sleeper did not find that pool. Check the league URL.",
            )
        if (
            not isinstance(league, dict)
            or str(league.get("league_id")) != league_id
            or not isinstance(league.get("settings") or {}, dict)
            or not isinstance(league.get("scoring_settings") or {}, dict)
        ):
            raise PoolDomainError(
                status_code=502,
                code="invalid_sleeper_data",
                message="Sleeper returned an invalid pool.",
            )
        results = await asyncio.gather(
            _get(client, f"league/{league_id}/users"),
            _get(client, f"league/{league_id}/rosters"),
            *([_get(client, f"user/{username}")] if username else []),
        )
    users, rosters = results[:2]
    if not all(
        isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
        for rows in (users, rosters)
    ):
        raise PoolDomainError(
            status_code=502,
            code="invalid_sleeper_data",
            message="Sleeper's participant data is unavailable. Try again shortly.",
        )
    owner = results[2] if username else None
    if username and (not isinstance(owner, dict) or not owner.get("user_id")):
        raise PoolDomainError(
            status_code=404,
            code="sleeper_user_not_found",
            message="That Sleeper username was not found.",
        )
    user_id = str(owner["user_id"]) if owner else None
    entries = []
    for roster in rosters:
        if not isinstance(roster.get("co_owners") or [], list) or not isinstance(
            roster.get("metadata") or {}, dict
        ):
            raise PoolDomainError(
                status_code=502,
                code="invalid_sleeper_data",
                message="Sleeper returned invalid entry metadata.",
            )
        if not user_id or user_id not in [roster.get("owner_id"), *(roster.get("co_owners") or [])]:
            continue
        roster_id = _integer(roster.get("roster_id"))
        if roster_id is None:
            raise PoolDomainError(
                status_code=502,
                code="invalid_sleeper_data",
                message="Sleeper returned an invalid entry ID.",
            )
        eliminated = (roster.get("metadata") or {}).get("is_eliminated")
        entries.append(
            SleeperEntryInfo(
                roster_id=roster_id,
                name=f"{owner.get('display_name') or username} · Entry {roster_id}"[:160],
                eliminated={"true": True, "false": False}.get(str(eliminated).lower()),
            )
        )
    if user_id and not entries:
        raise PoolDomainError(
            status_code=422,
            code="sleeper_entry_not_found",
            message=(
                "That username has no entry in this pool. Check the username, "
                "or leave it blank to import only pool details."
            ),
        )
    rules, unsupported = _rules(league)
    settings = league.get("settings") or {}
    try:
        season = int(league["season"])
        if (
            not 2000 <= season <= 2100
            or not isinstance(league.get("name"), str)
            or not league["name"].strip()
        ):
            raise ValueError("Invalid name or season")
    except (KeyError, ValueError, TypeError) as exc:
        raise PoolDomainError(
            status_code=502,
            code="invalid_sleeper_data",
            message="Sleeper returned an invalid season or pool name.",
        ) from exc
    return SleeperPoolInfo(
        league_id=league_id,
        url=f"https://sleeper.com/leagues/{league_id}",
        name=league["name"][:160],
        season=season,
        status=str(league.get("status") or "unknown"),
        current_week=_integer(settings.get("leg")),
        capacity=_integer(settings.get("num_teams")),
        participant_count=len(users),
        entry_count=len(rosters),
        commissioners=[
            str(user.get("display_name") or user.get("user_id"))
            for user in users
            if user.get("is_owner") is True
        ],
        username=username,
        user_id=user_id,
        entries=entries,
        rules=rules,
        settings=settings,
        scoring_settings=league.get("scoring_settings") or {},
        fetched_at=datetime.now(UTC),
        warnings=[
            HISTORY_WARNING,
            "Ties eliminate; picks lock at kickoff, per Sleeper's published survivor rules.",
        ],
        unsupported=unsupported,
    )


def pool_source(pool: Pool) -> SleeperPoolInfo | None:
    return (
        SleeperPoolInfo.model_validate_json(pool.sleeper_snapshot_json)
        if pool.sleeper_league_id
        else None
    )


def save_import(db: Session, info: SleeperPoolInfo, *, expected_pool_id: int | None = None) -> Pool:
    if info.unsupported or info.rules is None:
        raise PoolDomainError(
            status_code=422,
            code="unsupported_sleeper_rules",
            message="This pool cannot be imported: " + " ".join(info.unsupported),
        )
    # Serialize import with card saves, which also acquire SQLite's write lock.
    db.rollback()
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))
    try:
        pool = db.query(Pool).filter(Pool.sleeper_league_id == info.league_id).first()
        # A refresh must not recreate a pool deleted while its HTTP request was pending.
        if expected_pool_id is not None and (pool is None or pool.id != expected_pool_id):
            raise PoolDomainError(
                status_code=404,
                code="sleeper_pool_not_found",
                message="This Sleeper pool was deleted. Refresh your pools to continue.",
            )
        if pool:
            has_picks = (
                db.query(PoolPick.id).join(PoolEntry).filter(PoolEntry.pool_id == pool.id).first()
            )
            if has_picks and (
                pool.season != info.season
                or pool.pool_type != "survivor"
                or PoolRules.model_validate_json(pool.rules_json) != info.rules
            ):
                raise PoolDomainError(
                    status_code=409,
                    code="pool_rules_locked",
                    message=(
                        "Sleeper rules changed after local picks were saved. "
                        "Saved rules and picks are unchanged; review the changes on Sleeper."
                    ),
                )
            old_source = pool_source(pool)
            if old_source and old_source.user_id and old_source.user_id != info.user_id:
                raise PoolDomainError(
                    status_code=409,
                    code="sleeper_owner_changed",
                    message=(
                        "This pool is already linked to a different username. "
                        "Refresh it from its saved Sleeper details."
                    ),
                )
        else:
            pool = Pool(sleeper_league_id=info.league_id)
            db.add(pool)
        pool.name, pool.season, pool.pool_type = info.name, info.season, "survivor"
        pool.rules_json = info.rules.model_dump_json()
        pool.sleeper_snapshot_json = info.model_dump_json()
        db.flush()
        for entry in info.entries:
            existing = (
                db.query(PoolEntry)
                .filter(
                    PoolEntry.pool_id == pool.id,
                    PoolEntry.sleeper_roster_id == entry.roster_id,
                )
                .first()
            )
            if existing is None:
                db.add(
                    PoolEntry(
                        pool_id=pool.id,
                        sleeper_roster_id=entry.roster_id,
                        name=entry.name,
                        active=True,
                    )
                )
        db.commit()
        db.refresh(pool)
        return pool
    except IntegrityError as exc:
        db.rollback()
        raise PoolDomainError(
            status_code=409,
            code="sleeper_import_conflict",
            message="This pool was imported by another request. Refresh your pools and try again.",
        ) from exc
    except Exception:
        db.rollback()
        raise
