from __future__ import annotations

import base64
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import DataSnapshot, DraftPick, League, Player, SecretSetting
from ..security import decrypt_secret, encrypt_secret

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
FANTASY_URL = "https://fantasysports.yahooapis.com/fantasy/v2"


def _secret(db: Session, key: str, env_value: str | None = None) -> str | None:
    row = db.get(SecretSetting, key)
    return decrypt_secret(row.encrypted_value) if row else env_value


def save_yahoo_settings(db: Session, client_id: str, client_secret: str, redirect_uri: str) -> None:
    for key, value in {
        "yahoo.client_id": client_id,
        "yahoo.client_secret": client_secret,
        "yahoo.redirect_uri": redirect_uri,
    }.items():
        row = db.get(SecretSetting, key)
        encrypted = encrypt_secret(value)
        if row:
            row.encrypted_value = encrypted
        else:
            db.add(SecretSetting(key=key, encrypted_value=encrypted))
    db.commit()


def authorization_url(db: Session) -> str:
    client_id = _secret(db, "yahoo.client_id", settings.yahoo_client_id)
    redirect_uri = _secret(db, "yahoo.redirect_uri", settings.yahoo_redirect_uri)
    if not client_id or not redirect_uri:
        raise ValueError("Yahoo client ID and redirect URI are not configured")
    state = secrets.token_urlsafe(32)
    row = db.get(SecretSetting, "yahoo.oauth_state")
    encrypted = encrypt_secret(state)
    if row:
        row.encrypted_value = encrypted
    else:
        db.add(SecretSetting(key="yahoo.oauth_state", encrypted_value=encrypted))
    db.commit()
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }
    )
    return f"{AUTH_URL}?{query}"


async def exchange_code(db: Session, code: str, state: str) -> None:
    expected = _secret(db, "yahoo.oauth_state")
    if not expected or not secrets.compare_digest(expected, state):
        raise ValueError("Yahoo OAuth state mismatch")
    client_id = _secret(db, "yahoo.client_id", settings.yahoo_client_id)
    client_secret = _secret(db, "yahoo.client_secret", settings.yahoo_client_secret)
    redirect_uri = _secret(db, "yahoo.redirect_uri", settings.yahoo_redirect_uri)
    if not client_id or not client_secret or not redirect_uri:
        raise ValueError("Yahoo OAuth settings are incomplete")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            TOKEN_URL,
            headers={"Authorization": f"Basic {basic}"},
            data={"grant_type": "authorization_code", "redirect_uri": redirect_uri, "code": code},
        )
    response.raise_for_status()
    payload = response.json()
    expires_at = datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in", 3600)) - 60)
    for key, value in {
        "yahoo.access_token": payload["access_token"],
        "yahoo.refresh_token": payload.get("refresh_token", ""),
        "yahoo.expires_at": expires_at.isoformat(),
    }.items():
        row = db.get(SecretSetting, key)
        encrypted = encrypt_secret(value)
        if row:
            row.encrypted_value = encrypted
        else:
            db.add(SecretSetting(key=key, encrypted_value=encrypted))
    db.commit()


async def access_token(db: Session) -> str:
    token = _secret(db, "yahoo.access_token")
    expires_raw = _secret(db, "yahoo.expires_at")
    if token and expires_raw and datetime.fromisoformat(expires_raw) > datetime.now(UTC):
        return token
    refresh_token = _secret(db, "yahoo.refresh_token")
    client_id = _secret(db, "yahoo.client_id", settings.yahoo_client_id)
    client_secret = _secret(db, "yahoo.client_secret", settings.yahoo_client_secret)
    if not refresh_token or not client_id or not client_secret:
        raise ValueError("Yahoo is not connected")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            TOKEN_URL,
            headers={"Authorization": f"Basic {basic}"},
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        )
    response.raise_for_status()
    payload = response.json()
    new_refresh = payload.get("refresh_token", refresh_token)
    expires_at = datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in", 3600)) - 60)
    for key, value in {
        "yahoo.access_token": payload["access_token"],
        "yahoo.refresh_token": new_refresh,
        "yahoo.expires_at": expires_at.isoformat(),
    }.items():
        row = db.get(SecretSetting, key)
        encrypted = encrypt_secret(value)
        if row:
            row.encrypted_value = encrypted
        else:
            db.add(SecretSetting(key=key, encrypted_value=encrypted))
    db.commit()
    return payload["access_token"]


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _player_name(node: dict[str, Any]) -> str | None:
    value = node.get("name")
    if isinstance(value, dict):
        return value.get("full") or " ".join(
            part for part in [value.get("first"), value.get("last")] if isinstance(part, str)
        )
    return value if isinstance(value, str) else None


def _upsert_players(
    db: Session, league: League, payload: dict[str, Any], rostered_by: str | None = None
) -> int:
    changed = 0
    seen: set[str] = set()
    for node in _walk(payload):
        player_key = node.get("player_key")
        if not isinstance(player_key, str) or player_key in seen:
            continue
        seen.add(player_key)
        name = _player_name(node)
        if not name:
            continue
        team = str(node.get("editorial_team_abbr") or "FA").upper()
        position = str(
            node.get("display_position") or node.get("primary_position") or "UNK"
        ).upper()
        status = str(node.get("status_full") or node.get("status") or "Active")
        ownership = node.get("ownership")
        owner_name = rostered_by
        if isinstance(ownership, dict):
            owner_name = str(ownership.get("owner_team_name") or owner_name or "") or None
        player = (
            db.query(Player)
            .filter(Player.league_id == league.id, Player.source_id == player_key)
            .one_or_none()
        )
        if player:
            player.name = name
            player.pro_team = team
            player.position = position
            player.status = status
            if owner_name:
                player.rostered_by = owner_name
                player.ownership = "TEAM"
            changed += 1
        else:
            db.add(
                Player(
                    league_id=league.id,
                    source_id=player_key,
                    name=name,
                    pro_team=team,
                    position=position,
                    status=status,
                    ownership="TEAM" if owner_name else "FA",
                    rostered_by=owner_name,
                )
            )
            changed += 1
    return changed


def _apply_settings(league: League, payload: dict[str, Any]) -> None:
    scoring: dict[str, float] = {}
    roster_slots: list[str] = []
    for node in _walk(payload):
        if "stat_id" in node and "value" in node:
            try:
                name = str(
                    node.get("display_name") or node.get("name") or f"stat_{node['stat_id']}"
                )
                scoring[name] = float(node["value"])
            except (TypeError, ValueError):
                pass
        roster = node.get("roster_position")
        if isinstance(roster, dict):
            position = roster.get("position")
            try:
                count = int(roster.get("count", 1))
            except (TypeError, ValueError):
                count = 1
            if position:
                roster_slots.extend([str(position).upper()] * max(count, 0))
        if node.get("uses_faab") in {"1", 1}:
            try:
                league.faab_budget = int(
                    node.get("waiver_rule_value") or node.get("faab_budget") or 100
                )
            except (TypeError, ValueError):
                league.faab_budget = 100
    if scoring:
        league.scoring_json = json.dumps(scoring)
    if roster_slots:
        league.roster_slots_json = json.dumps(roster_slots)


async def _get_json(client: httpx.AsyncClient, token: str, url: str) -> dict[str, Any]:
    response = await client.get(
        url, params={"format": "json"}, headers={"Authorization": f"Bearer {token}"}
    )
    response.raise_for_status()
    return response.json()


async def _sync_league_detail(
    db: Session,
    client: httpx.AsyncClient,
    token: str,
    league: League,
) -> tuple[int, int]:
    resource_count = player_count = 0
    resources = [
        "settings",
        "standings",
        "scoreboard",
        "draftresults",
        "transactions",
        "teams/roster",
    ]
    for resource in resources:
        try:
            payload = await _get_json(
                client, token, f"{FANTASY_URL}/league/{league.yahoo_key}/{resource}"
            )
        except httpx.HTTPStatusError as exc:
            db.add(
                DataSnapshot(
                    source="yahoo",
                    source_id=f"{league.yahoo_key}:{resource}",
                    status="failed",
                    payload_json=json.dumps({"http_status": exc.response.status_code}),
                )
            )
            resource_count += 1
            continue
        db.add(
            DataSnapshot(
                source="yahoo",
                source_id=f"{league.yahoo_key}:{resource}",
                payload_json=json.dumps(payload),
            )
        )
        resource_count += 1
        if resource == "settings":
            _apply_settings(league, payload)
        elif resource == "teams/roster":
            for team_node in _walk(payload):
                if not isinstance(team_node.get("team_key"), str):
                    continue
                team_name = team_node.get("name")
                if isinstance(team_name, str):
                    player_count += _upsert_players(db, league, team_node, team_name)
        elif resource == "draftresults":
            for node in _walk(payload):
                if "pick" not in node or "player_key" not in node:
                    continue
                try:
                    overall = int(node["pick"])
                    round_number = int(node.get("round") or 1)
                except (TypeError, ValueError):
                    continue
                player = (
                    db.query(Player)
                    .filter(
                        Player.league_id == league.id, Player.source_id == str(node["player_key"])
                    )
                    .one_or_none()
                )
                pick = (
                    db.query(DraftPick)
                    .filter(DraftPick.league_id == league.id, DraftPick.overall == overall)
                    .one_or_none()
                )
                if not pick:
                    db.add(
                        DraftPick(
                            league_id=league.id,
                            overall=overall,
                            round=round_number,
                            team_name=str(node.get("team_key") or "Yahoo team"),
                            player_id=player.id if player else None,
                            source="yahoo",
                        )
                    )

    start = 0
    while start < 5000:
        try:
            payload = await _get_json(
                client,
                token,
                f"{FANTASY_URL}/league/{league.yahoo_key}/players;start={start};count=25",
            )
        except httpx.HTTPStatusError as exc:
            db.add(
                DataSnapshot(
                    source="yahoo",
                    source_id=f"{league.yahoo_key}:players:{start}",
                    status="failed",
                    payload_json=json.dumps({"http_status": exc.response.status_code}),
                )
            )
            resource_count += 1
            break
        changed = _upsert_players(db, league, payload)
        db.add(
            DataSnapshot(
                source="yahoo",
                source_id=f"{league.yahoo_key}:players:{start}",
                payload_json=json.dumps(payload),
            )
        )
        resource_count += 1
        player_count += changed
        keys = {
            node.get("player_key")
            for node in _walk(payload)
            if isinstance(node.get("player_key"), str)
        }
        if len(keys) < 25:
            break
        start += 25
    return resource_count, player_count


async def sync_leagues(db: Session) -> dict[str, int]:
    token = await access_token(db)
    url = f"{FANTASY_URL}/users;use_login=1/games;game_keys=nfl/leagues"
    async with httpx.AsyncClient(timeout=45) as client:
        payload = await _get_json(client, token, url)
    snapshot = DataSnapshot(
        source="yahoo", source_id="current-leagues", payload_json=json.dumps(payload)
    )
    db.add(snapshot)
    created = updated = 0
    for node in _walk(payload):
        league_key = node.get("league_key")
        if not league_key or not isinstance(league_key, str):
            continue
        league = db.query(League).filter(League.yahoo_key == league_key).one_or_none()
        name = str(node.get("name", league_key))
        season = int(node.get("season", datetime.now().year))
        if league:
            league.name = name
            league.season = season
            updated += 1
        else:
            db.add(League(name=name, season=season, source="yahoo", yahoo_key=league_key))
            created += 1
    db.commit()
    resources = players = 0
    leagues = db.query(League).filter(League.source == "yahoo", League.yahoo_key.is_not(None)).all()
    async with httpx.AsyncClient(timeout=45) as client:
        for league in leagues:
            synced_resources, synced_players = await _sync_league_detail(db, client, token, league)
            resources += synced_resources
            players += synced_players
            db.commit()
    return {"created": created, "updated": updated, "resources": resources, "players": players}
