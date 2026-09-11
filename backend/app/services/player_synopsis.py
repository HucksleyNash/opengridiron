from __future__ import annotations

import asyncio
import re
import unicodedata
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import feedparser
import httpx
from bs4 import BeautifulSoup
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ..models import NewsItem, Player
from ..schemas import PlayerArticle, PlayerInjuryReport, PlayerReportSource
from .news import INJURY_WORDS, _published

NFL_INJURIES_URL = "https://www.nfl.com/injuries/"
TEAM_NAMES = dict(
    zip(
        (
            "Cardinals",
            "Falcons",
            "Ravens",
            "Bills",
            "Panthers",
            "Bears",
            "Bengals",
            "Browns",
            "Cowboys",
            "Broncos",
            "Lions",
            "Packers",
            "Texans",
            "Colts",
            "Jaguars",
            "Chiefs",
            "Raiders",
            "Chargers",
            "Rams",
            "Dolphins",
            "Vikings",
            "Patriots",
            "Saints",
            "Giants",
            "Jets",
            "Eagles",
            "Steelers",
            "49ers",
            "Seahawks",
            "Buccaneers",
            "Titans",
            "Commanders",
        ),
        (
            "ARI",
            "ATL",
            "BAL",
            "BUF",
            "CAR",
            "CHI",
            "CIN",
            "CLE",
            "DAL",
            "DEN",
            "DET",
            "GB",
            "HOU",
            "IND",
            "JAX",
            "KC",
            "LV",
            "LAC",
            "LAR",
            "MIA",
            "MIN",
            "NE",
            "NO",
            "NYG",
            "NYJ",
            "PHI",
            "PIT",
            "SF",
            "SEA",
            "TB",
            "TEN",
            "WAS",
        ),
        strict=True,
    )
)
NEWS_DAYS = 30
CACHE_SECONDS = 600
_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    value = re.sub(r"[.'’]", "", value)
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _base_name(value: str) -> str:
    return re.sub(r"\s+(jr|sr|ii|iii|iv)$", "", _name(value))


def report_team(value: str) -> str:
    value = value.strip()
    for name, abbreviation in TEAM_NAMES.items():
        if value.lower() == name.lower() or value.lower().endswith(" " + name.lower()):
            return abbreviation
    return {
        "AZ": "ARI",
        "JAC": "JAX",
        "WSH": "WAS",
        "WFT": "WAS",
        "LA": "LAR",
        "OAK": "LV",
        "SD": "LAC",
    }.get(value.upper(), value.upper())


def matches_report(player: Player, row: dict) -> bool:
    return (
        _base_name(row["player_name"]) == _base_name(player.name)
        and report_team(row["team"]) == report_team(player.pro_team)
        and (not row.get("position") or row["position"] == player.position)
    )


def matches_player(name: str, text: str) -> bool:
    # Match a complete name, never a surname or a substring of a different name.
    needle = _base_name(name)
    return bool(needle and f" {needle} " in f" {_name(text)} ")


def _safe_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"https", "http"} and bool(parsed.hostname)
    except ValueError:
        return False


def parse_injuries(content: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(content, "html.parser")
    heading = next(
        (h for h in soup.select("h1") if re.match(r"\d{4} NFL Injury Report", h.get_text())), None
    )
    week = next((h for h in soup.select("h2") if "Injuries -" in h.get_text()), None)
    if heading is None or week is None:
        raise ValueError("NFL injury report format unavailable")
    period = f"{heading.get_text(' ', strip=True)} · {week.get_text(' ', strip=True)}"
    rows = []
    tables = soup.select("table")
    recognized = 0
    for table in tables:
        headers = [cell.get_text(" ", strip=True) for cell in table.select("thead th")]
        if not {"Player", "Injuries", "Practice Status", "Game Status"}.issubset(headers):
            continue
        recognized += 1
        team = table.find_previous(class_="d3-o-section-sub-title")
        for row in table.select("tbody tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
            if len(cells) != len(headers):
                raise ValueError("NFL injury row format unavailable")
            values = dict(zip(headers, cells, strict=True))
            rows.append(
                {
                    "player_name": values["Player"],
                    "position": values.get("Position", ""),
                    "team": team.get_text(" ", strip=True) if team else "Not supplied",
                    "injury": values["Injuries"] or "Not specified",
                    "practice_status": values["Practice Status"] or "Not specified",
                    "game_status": values["Game Status"] or "Not specified",
                    "report_period": period,
                    "url": NFL_INJURIES_URL,
                }
            )
    if not recognized and "No Injuries Reported" not in soup.get_text():
        raise ValueError("NFL injury table unavailable")
    return rows


def parse_news(content: str) -> list[dict[str, Any]]:
    feed = feedparser.parse(content)
    if not feed.version or (feed.bozo and not feed.entries):
        raise ValueError("News feed unavailable")
    items = []
    for entry in feed.entries[:100]:
        title = BeautifulSoup(str(entry.get("title", "")), "html.parser").get_text(" ", strip=True)
        source = str(entry.get("source", {}).get("title", "Google News"))
        if title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)]
        url = str(entry.get("link", ""))
        if title and _safe_url(url):
            items.append(
                {
                    "title": title[:1000],
                    "url": url,
                    "source": source,
                    # Google RSS descriptions repeat the headline and may contain unrelated links.
                    "excerpt": "",
                    "published_at": _published(entry.get("published")),
                }
            )
    return items


async def _public_source(
    client: httpx.AsyncClient,
    name: str,
    url: str,
    parser: Callable[[str], list[dict[str, Any]]],
    refresh: bool,
) -> tuple[list[dict[str, Any]], PlayerReportSource]:
    now = datetime.now(UTC)
    previous = _cache.get(url)
    # Refresh is throttled to one attempt per minute, including failures.
    ttl = 60 if refresh or (previous and previous["status"] != "ok") else CACHE_SECONDS
    if previous and (now - previous["checked_at"]).total_seconds() < ttl:
        _cache.move_to_end(url)
        return previous["items"], PlayerReportSource(name=name, url=url, **previous["source"])
    try:
        response = await client.get(url)
        response.raise_for_status()
        items = parser(response.text)
        source = PlayerReportSource(
            name=name,
            url=url,
            status="ok",
            checked_at=now,
            fetched_at=now,
        )
    except (httpx.HTTPError, ValueError):
        items = previous["items"] if previous else []
        fetched = previous["source"]["fetched_at"] if previous else None
        source = PlayerReportSource(
            name=name,
            url=url,
            status="stale" if fetched else "unavailable",
            checked_at=now,
            fetched_at=fetched,
            message="Refresh failed; showing previously retrieved reports."
            if fetched
            else "This source could not be reached or read. Try refreshing shortly.",
        )
    _cache[url] = {
        "items": items,
        "source": source.model_dump(exclude={"name", "url"}),
        "checked_at": now,
        "status": source.status,
    }
    _cache.move_to_end(url)
    while len(_cache) > 128:
        _cache.popitem(last=False)
    return items, source


async def official_injury_reports(refresh: bool = False):
    async with httpx.AsyncClient(
        timeout=10,
        follow_redirects=True,
        headers={"User-Agent": "OpenGridiron/0.1 (personal football news reader)"},
    ) as client:
        return await _public_source(
            client, "NFL injury report", NFL_INJURIES_URL, parse_injuries, refresh
        )


def _category(title: str, excerpt: str) -> str:
    # Treat these as injury-related articles, never as a diagnosis for the player.
    words = INJURY_WORDS - {"out", "limited"}
    words |= {"injuries", "rehab", "recovery", "injures", "injuring", "pup", "groin", "achilles"}
    return "injury" if set(re.findall(r"[a-z]+", f"{title} {excerpt}".lower())) & words else "news"


async def player_reports(db: Session, player: Player, refresh: bool = False) -> dict[str, Any]:
    # Strip search operators supplied in manually imported names.
    search_name = " ".join(re.findall(r"[\w.'’-]+", player.name))
    query = f'"{search_name}" NFL when:{NEWS_DAYS}d'
    search_url = "https://news.google.com/rss/search?" + urlencode(
        {
            "q": query,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        }
    )
    async with httpx.AsyncClient(
        timeout=10,
        follow_redirects=True,
        headers={"User-Agent": "OpenGridiron/0.1 (personal football news reader)"},
    ) as client:
        (injuries, injury_source), (news, news_source) = await asyncio.gather(
            _public_source(client, "NFL injury report", NFL_INJURIES_URL, parse_injuries, refresh),
            _public_source(client, "Google News", search_url, parse_news, refresh),
        )
    reports = [
        PlayerInjuryReport(**row, retrieved_at=injury_source.fetched_at)
        for row in injuries
        if matches_report(player, row)
    ]
    cutoff = datetime.now(UTC) - timedelta(days=NEWS_DAYS)
    articles = []
    for item in news:
        published = item["published_at"]
        if (published and _utc(published) < cutoff) or not matches_player(
            player.name, item["title"]
        ):
            continue
        articles.append(
            PlayerArticle(
                **item,
                category=_category(item["title"], item["excerpt"]),
                retrieved_at=news_source.fetched_at,
            )
        )
    # Match before limiting: a busy league-wide wire must not hide a player's coverage.
    stored = (
        db.query(NewsItem)
        .options(joinedload(NewsItem.source))
        .filter(
            func.coalesce(NewsItem.published_at, NewsItem.retrieved_at) >= cutoff,
        )
        .all()
    )
    for item in stored:
        if not matches_player(player.name, f"{item.title} {item.excerpt}") or not _safe_url(
            item.canonical_url
        ):
            continue
        articles.append(
            PlayerArticle(
                title=item.title,
                url=item.canonical_url,
                excerpt=item.excerpt,
                source=item.source.name if item.source else "Collected news",
                category=_category(item.title, item.excerpt),
                published_at=_utc(item.published_at) if item.published_at else None,
                retrieved_at=_utc(item.retrieved_at),
            )
        )
    # Prefer direct, locally collected links over an aggregator copy of the same headline.
    unique: dict[str, PlayerArticle] = {}
    urls: set[str] = set()
    for article in reversed(articles):
        key = _name(article.title)
        url = article.url.split("#", 1)[0]
        if key not in unique and url not in urls:
            unique[key] = article
            urls.add(url)
    ordered = sorted(
        unique.values(), key=lambda item: _utc(item.published_at or item.retrieved_at), reverse=True
    )
    synopsis = (
        f"{player.name} is listed as {player.position} "
        f"for {player.pro_team or 'an unspecified NFL team'}. "
        f"Imported fantasy status: {player.status or 'not supplied'}. "
        + (
            f"Rostered by {player.rostered_by}. "
            if player.rostered_by
            else "Not on an imported fantasy roster. "
        )
    )
    if reports:
        synopsis += "An official injury-report entry is available below. "
    elif injury_source.status == "ok":
        synopsis += (
            "No matching entry was found on the current NFL injury report; "
            "this does not confirm availability. "
        )
    else:
        synopsis += "The current official injury report could not be verified. "
    return {
        "synopsis": synopsis.strip(),
        "injury_reports": reports,
        "articles": ordered[:30],
        "sources": [injury_source, news_source],
    }
