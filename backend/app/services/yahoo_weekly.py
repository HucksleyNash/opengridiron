"""Bounded, explicit-week source comparisons kept separate from draft projections."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from ..models import DataSnapshot, League, Player
from .job_locks import job_lock
from .yahoo_scraper import (
    PLAYER_PAGE_SIZE,
    SCRAPER_COOKIE_KEY,
    YAHOO_BROWSER_HEADERS,
    YahooScraperRateLimited,
    _configured_url_for_league,
    _cookie_jar,
    _fetch,
    _page_url,
    _rate_limit_remaining,
    _record_rate_limit,
    _secret,
    parse_player_page,
    scraper_status,
)


def parse_verified_week(html: str, week: int) -> list:
    soup = BeautifulSoup(html, "html.parser")
    selected = soup.select_one('select[name="stat1"] option[selected]')
    if selected is None or selected.get("value") != f"S_PW_{week}":
        raise ValueError("Yahoo did not confirm the requested projected week")
    return parse_player_page(html)


def saved_weekly_projections(db, league: League, week: int) -> tuple[dict[int, dict], dict]:
    snapshot = (
        db.query(DataSnapshot)
        .filter_by(
            source="yahoo_weekly_projection", source_id=f"{league.id}:{league.season}:{week}"
        )
        .order_by(DataSnapshot.id.desc())
        .first()
    )
    source = {"name": "Yahoo weekly comparison", "status": "unavailable", "week": week}
    if snapshot is None:
        return {}, source
    received = (
        snapshot.retrieved_at.replace(tzinfo=UTC)
        if snapshot.retrieved_at.tzinfo is None
        else snapshot.retrieved_at
    )
    source.update(
        {"snapshot_id": snapshot.id, "received_at": received.isoformat(), "status": "available"}
    )
    if datetime.now(UTC) - received > timedelta(hours=6):
        source["status"] = "stale"
        return {}, source
    payload = json.loads(snapshot.payload_json)
    by_source = payload["players"]
    projections = {}
    for player in db.query(Player).filter_by(league_id=league.id).all():
        identity = (player.source_id or "").split(".p.")[-1]
        item = by_source.get(identity)
        if item is not None:
            projections[player.id] = {"points": item["points"], **payload["context"]}
    source["coverage"] = len(projections)
    source["detail"] = "Top 50 per supported position; unmatched players have no source comparison."
    return projections, source


async def refresh_weekly_projections(db, league: League, week: int) -> tuple[dict[int, dict], dict]:
    existing, source = saved_weekly_projections(db, league, week)
    if source["status"] == "available":
        return existing, source
    config = scraper_status(db)
    root = _configured_url_for_league(league, config["league_urls"])
    cookie = _secret(db, SCRAPER_COOKIE_KEY)
    if not root or not cookie:
        return existing, source
    # Pin historical leagues instead of silently using Yahoo's current-season redirect.
    parsed = urlparse(root)
    path = re.sub(r"^/\d{4}/", "/", parsed.path)
    root = urlunparse(parsed._replace(path=f"/{league.season}{path}"))
    with job_lock("yahoo-import") as acquired:
        if not acquired or _rate_limit_remaining(db):
            return existing, {**source, "detail": "Yahoo refresh is running or rate limited."}
        players = {}
        try:
            async with httpx.AsyncClient(
                headers=YAHOO_BROWSER_HEADERS,
                cookies=_cookie_jar(cookie),
                follow_redirects=True,
                timeout=45,
            ) as client:
                for position in ("QB", "RB", "WR", "TE", "K", "DEF"):
                    for page in range(2):
                        response = await _fetch(
                            client,
                            _page_url(
                                root,
                                "players",
                                {
                                    "status": "ALL",
                                    "pos": position,
                                    "count": str(page * PLAYER_PAGE_SIZE),
                                    "stat1": f"S_PW_{week}",
                                    "sort": "PTS",
                                    "sdir": "1",
                                },
                            ),
                        )
                        if f"/{league.season}/" not in urlparse(str(response.url)).path:
                            raise ValueError("Yahoo redirected away from the requested season")
                        rows = parse_verified_week(response.text, week)
                        for row in rows:
                            if row.projected_points is not None:
                                players[row.source_id.split(".p.")[-1]] = {
                                    "points": row.projected_points
                                }
                        if len(rows) < PLAYER_PAGE_SIZE:
                            break
            if not players:
                raise ValueError("No verified weekly projections returned")
            now = datetime.now(UTC)
            context = {
                "source": "Yahoo Fantasy weekly table",
                "period": "week",
                "season": league.season,
                "week": week,
                "scoring_basis": "league_rules",
                "scoring": json.loads(league.scoring_json),
                "received_at": now.isoformat(),
                "source_updated_at": None,
                "ros_value_state": "missing",
            }
            db.add(
                DataSnapshot(
                    source="yahoo_weekly_projection",
                    source_id=f"{league.id}:{league.season}:{week}",
                    retrieved_at=now,
                    status="fresh",
                    payload_json=json.dumps({"context": context, "players": players}),
                )
            )
            db.commit()
            return saved_weekly_projections(db, league, week)
        except YahooScraperRateLimited as exc:
            _record_rate_limit(db, exc)
            return {}, {
                **source,
                "status": "unavailable",
                "detail": "Yahoo rate limited the weekly comparison refresh.",
            }
        except (httpx.HTTPError, ValueError):
            return {}, {
                **source,
                "status": "unavailable",
                "detail": (
                    "Yahoo weekly table or selected period could not be verified. "
                    "Independent forecasts remain available."
                ),
            }
