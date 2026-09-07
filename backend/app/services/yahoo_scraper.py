from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from math import ceil
from time import monotonic
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup, Tag
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import DataSnapshot, DraftPick, League, Player, SecretSetting
from ..security import decrypt_secret, encrypt_secret
from .job_locks import job_lock
from .nflverse_draft import sync_projection_ranges

YAHOO_FANTASY_HOST = "football.fantasysports.yahoo.com"
SCRAPER_URLS_KEY = "yahoo.scraper.league_urls"
SCRAPER_COOKIE_KEY = "yahoo.scraper.cookie"
PLAYER_PAGE_SIZE = 25
MAX_PLAYER_PAGES = 80
YAHOO_RATE_LIMIT_COOLDOWN_SECONDS = 5 * 60

YAHOO_BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
    ),
}

_LIVE_DRAFT_BLOCKED_UNTIL: dict[str, float] = {}
_request_lock = asyncio.Lock()
_last_request_at = 0.0

_TEAM_POSITION_RE = re.compile(r"^\s*(?P<team>[A-Za-z0-9]+)\s+-\s+(?P<position>[A-Za-z/]+)")
_PLAYER_ID_RE = re.compile(r"/nfl/players/(?P<id>\d+)")
_TEAM_PATH_RE = re.compile(r"/(?:\d{4}/)?(?:f1|league)/[^/]+/(?P<team_id>\d+)/?$")
_LEAGUE_PATH_RE = re.compile(
    r"^/(?:(?P<season>\d{4})/)?(?P<kind>f1|league)/(?P<reference>[^/?#]+)/?$"
)

SCORING_NAMES = {
    "passing yards": "passing_yards",
    "passing touchdowns": "passing_tds",
    "interceptions": "interceptions",
    "rushing yards": "rushing_yards",
    "rushing touchdowns": "rushing_tds",
    "receptions": "receptions",
    "receiving yards": "receiving_yards",
    "receiving touchdowns": "receiving_tds",
    "2-point conversions": "two_point_conversions",
    "fumbles lost": "fumbles_lost",
}


@dataclass(frozen=True)
class ScrapedPlayer:
    source_id: str
    name: str
    pro_team: str
    position: str
    status: str = "Active"
    ownership: str = "FA"
    rostered_by: str | None = None
    current_slot: str | None = None
    overall_rank: float | None = None
    projected_points: float | None = None
    projection_period: str = "unknown"
    projection_season: int | None = None
    projection_received_at: str | None = None


@dataclass(frozen=True)
class ScrapedDraftPick:
    overall: int
    round: int
    team_name: str
    player_source_id: str | None
    player_name: str
    pro_team: str
    position: str


class YahooScraperRateLimited(ValueError):
    def __init__(self, retry_after_seconds: int = YAHOO_RATE_LIMIT_COOLDOWN_SECONDS) -> None:
        self.retry_after_seconds = max(1, retry_after_seconds)
        minutes = max(1, ceil(self.retry_after_seconds / 60))
        super().__init__(
            "Yahoo temporarily blocked automated requests. "
            f"Live Yahoo sync is paused for about {minutes} minute"
            f"{'s' if minutes != 1 else ''}; keep recording picks manually."
        )


def _text(node: Tag | None) -> str:
    if not node:
        return ""
    return " ".join(node.get_text(" ", strip=True).replace("\xa0", " ").split())


def _secret(db: Session, key: str) -> str | None:
    row = db.get(SecretSetting, key)
    return decrypt_secret(row.encrypted_value) if row else None


def _save_secret(db: Session, key: str, value: str) -> None:
    encrypted = encrypt_secret(value)
    row = db.get(SecretSetting, key)
    if row:
        row.encrypted_value = encrypted
    else:
        db.add(SecretSetting(key=key, encrypted_value=encrypted))


def normalize_league_url(value: str) -> str:
    raw = value.strip()
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.hostname != YAHOO_FANTASY_HOST:
        raise ValueError(f"Yahoo league URLs must use https://{YAHOO_FANTASY_HOST}")
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("Yahoo league URLs cannot contain credentials or a custom port")
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    if not _LEAGUE_PATH_RE.fullmatch(path):
        raise ValueError("Use a Yahoo football league home URL, not a team or player URL")
    return urlunparse(("https", YAHOO_FANTASY_HOST, path, "", "", ""))


def normalize_cookie_header(value: str) -> str:
    cookie = value.strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()
    if "\r" in cookie or "\n" in cookie:
        raise ValueError("The Yahoo cookie must be a single Cookie header value")
    parts = [part.strip() for part in cookie.split(";") if part.strip()]
    if not parts or any("=" not in part for part in parts):
        raise ValueError("The Yahoo cookie must contain name=value pairs")
    return "; ".join(parts)


def _cookie_jar(cookie_header: str) -> httpx.Cookies:
    cookies = httpx.Cookies()
    for part in cookie_header.split(";"):
        name, value = part.strip().split("=", 1)
        cookies.set(name, value, domain=YAHOO_FANTASY_HOST, path="/")
    return cookies


def save_scraper_settings(db: Session, league_urls: list[str], cookie: str | None = None) -> None:
    normalized_urls = list(dict.fromkeys(normalize_league_url(url) for url in league_urls))
    if not normalized_urls:
        raise ValueError("Add at least one Yahoo league URL")
    _save_secret(db, SCRAPER_URLS_KEY, json.dumps(normalized_urls))
    if cookie is not None and cookie.strip():
        _save_secret(db, SCRAPER_COOKIE_KEY, normalize_cookie_header(cookie))
    if not _secret(db, SCRAPER_COOKIE_KEY):
        raise ValueError("Add an authenticated Yahoo Cookie header before saving")
    db.commit()


def scraper_status(db: Session) -> dict[str, Any]:
    urls_raw = _secret(db, SCRAPER_URLS_KEY)
    try:
        urls = json.loads(urls_raw) if urls_raw else []
    except json.JSONDecodeError:
        urls = []
    if not isinstance(urls, list):
        urls = []
    safe_urls = [url for url in urls if isinstance(url, str)]
    has_cookie = bool(_secret(db, SCRAPER_COOKIE_KEY))
    return {
        "configured": bool(safe_urls and has_cookie),
        "has_cookie": has_cookie,
        "league_urls": safe_urls,
    }


def _parse_scoring_value(value: str) -> float | None:
    normalized = value.lower().replace(",", "").strip()
    if normalized in {"", "-", "none", "no points"}:
        return 0.0 if normalized == "no points" else None
    per_point = re.search(r"(-?\d+(?:\.\d+)?)\s+yards?\s+per\s+point", normalized)
    if per_point:
        yards = float(per_point.group(1))
        return 1 / yards if yards else None
    points_per = re.search(
        r"(-?\d+(?:\.\d+)?)\s+points?\s+per\s+(-?\d+(?:\.\d+)?)\s+yards?",
        normalized,
    )
    if points_per:
        yards = float(points_per.group(2))
        return float(points_per.group(1)) / yards if yards else None
    number = re.match(r"^-?\d+(?:\.\d+)?", normalized)
    return float(number.group()) if number else None


def _scoring_name(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in SCORING_NAMES:
        return SCORING_NAMES[normalized]
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")


def _season_from_settings(settings: dict[str, str], canonical_url: str) -> int:
    path_match = _LEAGUE_PATH_RE.fullmatch(urlparse(canonical_url).path.rstrip("/"))
    if path_match and path_match.group("season"):
        return int(path_match.group("season"))
    for label in ("Draft Time", "Trade End Date", "Start Scoring on"):
        years = re.findall(r"\b(20\d{2})\b", settings.get(label, ""))
        if years:
            return int(years[-1])
    return datetime.now(UTC).year


def parse_settings_page(html: str, canonical_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#settings-table")
    if not table:
        raise ValueError("Yahoo settings table was not found; the session may have expired")
    settings: dict[str, str] = {}
    for row in table.select("tbody tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        label = _text(cells[0]).rstrip(":")
        settings[label] = _text(cells[1])

    scoring: dict[str, float] = {}
    scoring_table = soup.select_one("#settings-stat-mod-table")
    if scoring_table:
        for row in scoring_table.select("tbody tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            for offset in range(0, len(cells) - 1, 3):
                name = _text(cells[offset])
                value = _parse_scoring_value(_text(cells[offset + 1]))
                if name and value is not None:
                    scoring[_scoring_name(name)] = value

    roster_value = next(
        (
            value
            for key, value in settings.items()
            if key.lower().replace(" ", "") == "rosterpositions"
        ),
        "",
    )
    roster_slots = [slot.strip().upper() for slot in roster_value.split(",") if slot.strip()]
    faab_budget: int | None = None
    for label, value in settings.items():
        if "budget" not in label.lower():
            continue
        match = re.search(r"\d+", value.replace(",", ""))
        if match:
            faab_budget = int(match.group())
            break

    canonical = normalize_league_url(canonical_url)
    path_match = _LEAGUE_PATH_RE.fullmatch(urlparse(canonical).path)
    if not path_match:
        raise ValueError("Yahoo returned an unexpected canonical league URL")
    return {
        "name": settings.get("League Name") or path_match.group("reference"),
        "season": _season_from_settings(settings, canonical),
        "reference": path_match.group("reference"),
        "canonical_url": canonical,
        "scoring": scoring,
        "roster_slots": roster_slots,
        "faab_budget": faab_budget,
        "settings": settings,
    }


def parse_team_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: dict[int, str] = {}
    for anchor in soup.select("a[href]"):
        absolute = urljoin(base_url, str(anchor.get("href")))
        parsed = urlparse(absolute)
        if parsed.hostname != YAHOO_FANTASY_HOST:
            continue
        match = _TEAM_PATH_RE.fullmatch(parsed.path.rstrip("/"))
        if match:
            links[int(match.group("team_id"))] = urlunparse(
                ("https", YAHOO_FANTASY_HOST, parsed.path.rstrip("/"), "", "", "")
            )
    return [links[key] for key in sorted(links)]


def _player_metadata(player_cell: Tag) -> tuple[str, str]:
    for candidate in player_cell.select(".ysf-player-name .D-b, .ysf-player-name .Fz-xxs"):
        match = _TEAM_POSITION_RE.match(_text(candidate))
        if match:
            return match.group("team").upper(), match.group("position").upper()
    text_match = re.search(r"\b([A-Za-z]{2,3})\s+-\s+([A-Za-z/]+)\b", _text(player_cell))
    if text_match:
        return text_match.group(1).upper(), text_match.group(2).upper()
    return "FA", "UNK"


def _player_status(player_cell: Tag) -> str:
    status = player_cell.select_one(".ysf-player-status [title], .ysf-player-status[title]")
    if status and status.get("title"):
        return str(status.get("title"))
    return "Active"


def _row_headers(row: Tag) -> tuple[list[str], list[Tag]]:
    table = row.find_parent("table")
    if table is None:
        return [], []
    cells = row.find_all("td", recursive=False)
    header_rows = table.select("thead tr")
    matching = next(
        (
            headers
            for header_row in reversed(header_rows)
            if len(headers := header_row.find_all("th", recursive=False)) == len(cells)
        ),
        [],
    )
    return [_text(header).lower() for header in matching], cells


def _player_overall_rank(row: Tag) -> float | None:
    headers, cells = _row_headers(row)
    for index, header in enumerate(headers):
        normalized = header.replace("-", " ")
        if not any(
            label in normalized for label in ("pre season rank", "overall rank", "xrank", "adp")
        ):
            continue
        if index >= len(cells):
            continue
        match = re.search(r"\d+(?:\.\d+)?", _text(cells[index]).replace(",", ""))
        if match:
            return float(match.group())
    return None


def _player_projected_points(row: Tag) -> float | None:
    headers, cells = _row_headers(row)
    for index, header in enumerate(headers):
        if "fan pts" not in header or index >= len(cells):
            continue
        match = re.search(r"-?\d+(?:\.\d+)?", _text(cells[index]).replace(",", ""))
        return float(match.group()) if match else None
    return None


def _row_cell_for_header(row: Tag, labels: tuple[str, ...]) -> Tag | None:
    headers, cells = _row_headers(row)
    for index, header in enumerate(headers):
        if any(label in header for label in labels) and index < len(cells):
            return cells[index]
    return None


def parse_player_page(html: str, *, rostered_by: str | None = None) -> list[ScrapedPlayer]:
    soup = BeautifulSoup(html, "html.parser")
    players: list[ScrapedPlayer] = []
    seen: set[str] = set()
    for anchor in soup.select("a.name[data-ys-playerid], td.player a.name[href*='/nfl/players/']"):
        player_id = str(anchor.get("data-ys-playerid") or "")
        if not player_id:
            match = _PLAYER_ID_RE.search(str(anchor.get("href") or ""))
            player_id = match.group("id") if match else ""
        source_id = f"yahoo.p.{player_id}" if player_id else ""
        if not source_id or source_id in seen:
            continue
        player_cell = anchor.find_parent("td")
        row = anchor.find_parent("tr")
        if not player_cell or not row:
            continue
        seen.add(source_id)
        pro_team, position = _player_metadata(player_cell)
        owner = rostered_by
        ownership = "TEAM" if owner else "FA"
        current_slot: str | None = None
        cells = row.find_all("td", recursive=False)
        if rostered_by:
            if cells and cells[0] is not player_cell:
                current_slot = _text(cells[0]).upper() or None
        else:
            owner_cell = _row_cell_for_header(row, ("owner", "availability"))
            if owner_cell is None:
                owner_cell = player_cell.find_next_sibling("td")
            owner_text = _text(owner_cell)
            owner_upper = owner_text.upper()
            if owner_upper == "W" or owner_upper.startswith("W (") or "WAIVER" in owner_upper:
                ownership = "W"
            elif owner_upper not in {"", "FA", "FREE AGENT", "-"}:
                ownership = "TEAM"
                owner = owner_text
        players.append(
            ScrapedPlayer(
                source_id=source_id,
                name=_text(anchor),
                pro_team=pro_team,
                position=position,
                status=_player_status(player_cell),
                ownership=ownership,
                rostered_by=owner,
                current_slot=current_slot,
                overall_rank=_player_overall_rank(row),
                projected_points=_player_projected_points(row),
            )
        )
    return players


def parse_player_position_filters(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    values = {
        str(node.get("value")).upper()
        for node in soup.select("input[name='pos'][value]")
        if node.get("value")
    }
    return [position for position in ("O", "K", "DEF", "D") if position in values]


def parse_team_page(html: str) -> tuple[str, list[ScrapedPlayer]]:
    soup = BeautifulSoup(html, "html.parser")
    title = _text(soup.title)
    team_name = ""
    if " - " in title and " | Fantasy" in title:
        team_name = title.split(" - ", 1)[1].split(" | Fantasy", 1)[0].strip()
    if not team_name:
        candidate = soup.select_one("span.F-reset.Nowrap")
        team_name = _text(candidate)
    if not team_name:
        raise ValueError("Yahoo team name was not found")
    return team_name, parse_player_page(html, rostered_by=team_name)


def _draft_round_tables(soup: BeautifulSoup) -> list[tuple[int, Tag]]:
    tables: list[tuple[int, Tag]] = []
    for table in soup.select("table"):
        header = _text(table.select_one("thead"))
        match = re.search(r"\bRound\s+(\d+)\b", header, re.IGNORECASE)
        if match:
            tables.append((int(match.group(1)), table))
    return tables


def _draft_row_number(row: Tag) -> int | None:
    number_cell = row.select_one("td.first")
    if number_cell is None:
        number_cell = row.find("td", recursive=False)
    match = re.search(r"\d+", _text(number_cell))
    return int(match.group()) if match else None


def parse_draft_order(html: str) -> list[str]:
    """Return Yahoo's complete first-round slot order, or no order when unpublished."""
    soup = BeautifulSoup(html, "html.parser")
    first_round = next(
        (table for round_number, table in _draft_round_tables(soup) if round_number == 1), None
    )
    if first_round is None:
        return []

    slots: dict[int, str] = {}
    for row in first_round.select("tbody tr"):
        slot = _draft_row_number(row)
        team_cell = row.select_one("td.last")
        if team_cell is None:
            cells = row.find_all("td", recursive=False)
            team_cell = cells[-1] if len(cells) >= 2 else None
        team_name = _text(team_cell)
        if slot is not None and team_name:
            slots[slot] = team_name

    if not 8 <= len(slots) <= 16:
        return []
    expected_slots = list(range(1, len(slots) + 1))
    if sorted(slots) != expected_slots:
        return []
    return [slots[slot] for slot in expected_slots]


def parse_draft_page(html: str) -> list[ScrapedDraftPick]:
    soup = BeautifulSoup(html, "html.parser")
    round_tables = _draft_round_tables(soup)
    team_count = len(parse_draft_order(html))
    if not team_count and any(actual_round > 1 for actual_round, _table in round_tables):
        raise ValueError("Yahoo draft results did not include a complete first-round order")

    picks: list[ScrapedDraftPick] = []
    for actual_round, table in round_tables:
        for row in table.select("tbody tr"):
            player_cell = row.select_one("td.player")
            team_cell = row.select_one("td.last")
            anchor = player_cell.select_one("a.name") if player_cell else None
            if not player_cell or not team_cell or not anchor:
                continue
            href_match = _PLAYER_ID_RE.search(str(anchor.get("href") or ""))
            source_id = f"yahoo.p.{href_match.group('id')}" if href_match else None
            meta_match = re.search(r"\(([A-Za-z0-9]+)\s+-\s+([A-Za-z/]+)\)", _text(player_cell))
            round_pick = _draft_row_number(row)
            if round_pick is None:
                continue
            overall = (actual_round - 1) * team_count + round_pick if team_count else round_pick
            picks.append(
                ScrapedDraftPick(
                    overall=overall,
                    round=actual_round,
                    team_name=_text(team_cell),
                    player_source_id=source_id,
                    player_name=_text(anchor),
                    pro_team=meta_match.group(1).upper() if meta_match else "FA",
                    position=meta_match.group(2).upper() if meta_match else "UNK",
                )
            )
    return picks


def _canonical_url(html: str, fallback: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    link = soup.select_one("link[rel='canonical'][href]")
    return str(link.get("href")) if link else fallback


def _page_url(root_url: str, resource: str, params: dict[str, str] | None = None) -> str:
    parsed = urlparse(root_url)
    path = f"{parsed.path.rstrip('/')}/{resource.lstrip('/')}"
    query = urlencode(params or {})
    return urlunparse(("https", YAHOO_FANTASY_HOST, path, "", query, ""))


def _is_login_page(response: httpx.Response) -> bool:
    host = response.url.host.lower()
    if host.endswith("login.yahoo.com"):
        return True
    body = response.text[:200_000].lower()
    return 'name="signin"' in body or 'id="login-signin"' in body


async def _fetch(client: httpx.AsyncClient, url: str) -> httpx.Response:
    global _last_request_at
    async with _request_lock:
        delay = settings.yahoo_request_interval_seconds - (monotonic() - _last_request_at)
        if delay > 0:
            await asyncio.sleep(delay)
        _last_request_at = monotonic()
    response = await client.get(url)
    if response.status_code in {429, 999}:
        retry_after = response.headers.get("Retry-After", "")
        try:
            retry_after_seconds = int(retry_after)
        except ValueError:
            retry_after_seconds = YAHOO_RATE_LIMIT_COOLDOWN_SECONDS
        raise YahooScraperRateLimited(retry_after_seconds)
    response.raise_for_status()
    if _is_login_page(response):
        raise ValueError("Yahoo rejected the saved browser session; replace the Cookie header")
    if response.url.host.lower() != YAHOO_FANTASY_HOST:
        raise ValueError("Yahoo redirected the scraper outside Fantasy Football")
    return response


def _find_player(db: Session, league: League, source_id: str | None) -> Player | None:
    if not source_id:
        return None
    player = (
        db.query(Player)
        .filter(Player.league_id == league.id, Player.source_id == source_id)
        .one_or_none()
    )
    if player:
        return player
    numeric_id = source_id.rsplit(".", 1)[-1]
    return (
        db.query(Player)
        .filter(
            Player.league_id == league.id,
            Player.source_id.like(f"%.p.{numeric_id}"),
        )
        .one_or_none()
    )


def _upsert_player(db: Session, league: League, scraped: ScrapedPlayer) -> Player:
    from ..schemas import ProjectionContext
    from .projection_context import context_for

    player = _find_player(db, league, scraped.source_id)
    is_new = player is None
    if not player:
        player = Player(
            league_id=league.id,
            source_id=scraped.source_id,
            name=scraped.name,
            pro_team=scraped.pro_team,
            position=scraped.position,
        )
        db.add(player)
    elif player.source_id and player.source_id.startswith("yahoo.p."):
        player.source_id = scraped.source_id
    player.name = scraped.name
    player.pro_team = scraped.pro_team
    player.position = scraped.position
    player.status = scraped.status
    player.ownership = scraped.ownership
    player.rostered_by = scraped.rostered_by
    player.current_slot = scraped.current_slot
    if scraped.projected_points is not None:
        player.projected_points = scraped.projected_points
        previous = context_for(player)
        player.projection_context_json = ProjectionContext(
            source="Yahoo Fantasy",
            period=scraped.projection_period,
            season=scraped.projection_season,
            received_at=scraped.projection_received_at,
            scoring_basis="source_points",
            scoring=json.loads(league.scoring_json or "{}"),
            ros_value_state="missing" if is_new else previous.ros_value_state,
        ).model_dump_json()
    return player


def _find_draft_player(
    db: Session, league: League, scraped_pick: ScrapedDraftPick
) -> Player | None:
    player = _find_player(db, league, scraped_pick.player_source_id)
    if player is not None or scraped_pick.player_source_id is not None:
        return player
    identity_matches = list(
        db.scalars(
            select(Player).where(
                Player.league_id == league.id,
                Player.name == scraped_pick.player_name,
                Player.pro_team == scraped_pick.pro_team,
                Player.position == scraped_pick.position,
            )
        )
    )
    yahoo_matches = [
        candidate
        for candidate in identity_matches
        if candidate.source_id and candidate.source_id.startswith("yahoo.p.")
    ]
    if len(yahoo_matches) == 1:
        return yahoo_matches[0]
    return identity_matches[0] if len(identity_matches) == 1 else None


def _upsert_draft_picks(db: Session, league: League, draft_picks: list[ScrapedDraftPick]) -> None:
    for scraped_pick in draft_picks:
        player = _find_draft_player(db, league, scraped_pick)
        if not player:
            player = _upsert_player(
                db,
                league,
                ScrapedPlayer(
                    source_id=scraped_pick.player_source_id or f"draft.{scraped_pick.overall}",
                    name=scraped_pick.player_name,
                    pro_team=scraped_pick.pro_team,
                    position=scraped_pick.position,
                ),
            )
            db.flush()
        pick = (
            db.query(DraftPick)
            .filter(DraftPick.league_id == league.id, DraftPick.overall == scraped_pick.overall)
            .one_or_none()
        )
        if not pick:
            pick = DraftPick(
                league_id=league.id,
                overall=scraped_pick.overall,
                round=scraped_pick.round,
                team_name=scraped_pick.team_name,
                source="yahoo_scrape",
            )
            db.add(pick)
        pick.round = scraped_pick.round
        pick.team_name = scraped_pick.team_name
        pick.player_id = player.id
        pick.source = "yahoo_scrape"


def _find_league(db: Session, reference: str, season: int) -> League | None:
    scrape_key = f"scrape:{season}:{reference}"
    league = db.query(League).filter(League.yahoo_key == scrape_key).one_or_none()
    if league:
        return league
    if reference.isdigit():
        return (
            db.query(League)
            .filter(League.season == season, League.yahoo_key.like(f"%.l.{reference}"))
            .one_or_none()
        )
    return None


async def _scrape_one(
    db: Session, client: httpx.AsyncClient, configured_url: str
) -> dict[str, int]:
    resources = 0
    home_response = await _fetch(client, configured_url)
    resources += 1
    root_url = normalize_league_url(_canonical_url(home_response.text, str(home_response.url)))

    settings_response = await _fetch(client, _page_url(root_url, "settings"))
    resources += 1
    metadata = parse_settings_page(settings_response.text, root_url)

    team_links = parse_team_links(home_response.text, root_url)
    if not team_links:
        team_links = parse_team_links(settings_response.text, root_url)
    roster_players: dict[str, ScrapedPlayer] = {}
    teams: list[dict[str, Any]] = []
    for team_url in team_links:
        response = await _fetch(client, team_url)
        resources += 1
        team_name, players = parse_team_page(response.text)
        teams.append({"name": team_name, "url": team_url, "players": [asdict(p) for p in players]})
        roster_players.update({player.source_id: player for player in players})

    all_players: dict[str, ScrapedPlayer] = {}
    player_pool_complete = True
    player_positions = ["O"]
    position_index = 0
    while position_index < len(player_positions):
        position = player_positions[position_index]
        position_complete = False
        for page in range(MAX_PLAYER_PAGES):
            response = await _fetch(
                client,
                _page_url(
                    root_url,
                    "players",
                    {
                        "status": "ALL",
                        "pos": position,
                        "count": str(page * PLAYER_PAGE_SIZE),
                        "stat1": f"S_PS_{metadata['season']}",
                    },
                ),
            )
            resources += 1
            if position_index == 0 and page == 0:
                discovered = parse_player_position_filters(response.text)
                player_positions = discovered or ["O"]
            page_players = parse_player_page(response.text)
            for player in page_players:
                player = replace(
                    player,
                    projection_period="season",
                    projection_season=int(metadata["season"]),
                    projection_received_at=datetime.now(UTC).isoformat(),
                )
                if player.overall_rank is None:
                    player = replace(player, overall_rank=float(len(all_players) + 1))
                all_players[player.source_id] = player
            if len(page_players) < PLAYER_PAGE_SIZE:
                position_complete = True
                break
        player_pool_complete = player_pool_complete and position_complete
        position_index += 1

    try:
        draft_response = await _fetch(client, _page_url(root_url, "draftresults"))
        resources += 1
        draft_order = parse_draft_order(draft_response.text)
        draft_picks = parse_draft_page(draft_response.text)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in {404, 409}:
            raise
        draft_order = []
        draft_picks = []

    combined_players = dict(all_players)
    for source_id, roster_player in roster_players.items():
        ranked_player = all_players.get(source_id)
        combined_players[source_id] = replace(
            roster_player,
            overall_rank=ranked_player.overall_rank if ranked_player else None,
            projected_points=ranked_player.projected_points if ranked_player else None,
            projection_period=ranked_player.projection_period if ranked_player else "unknown",
            projection_season=ranked_player.projection_season if ranked_player else None,
            projection_received_at=ranked_player.projection_received_at if ranked_player else None,
        )
    reference = str(metadata["reference"])
    season = int(metadata["season"])
    league = _find_league(db, reference, season)
    created = int(league is None)
    if not league:
        league = League(
            name=str(metadata["name"]),
            season=season,
            source="yahoo_scrape",
            yahoo_key=f"scrape:{season}:{reference}",
        )
        db.add(league)
        db.flush()
    else:
        league.name = str(metadata["name"])
        league.season = season
    if metadata["scoring"]:
        league.scoring_json = json.dumps(metadata["scoring"])
    if metadata["roster_slots"]:
        league.roster_slots_json = json.dumps(metadata["roster_slots"])
    if metadata["faab_budget"] is not None:
        league.faab_budget = int(metadata["faab_budget"])

    for scraped in combined_players.values():
        _upsert_player(db, league, scraped)
    db.flush()

    if player_pool_complete and team_links:
        rostered_ids = set(roster_players)
        for player in db.query(Player).filter(Player.league_id == league.id).all():
            if (
                player.source_id
                and player.source_id not in rostered_ids
                and not any(
                    player.source_id.endswith(f".p.{source_id.rsplit('.', 1)[-1]}")
                    for source_id in rostered_ids
                )
            ):
                scraped = next(
                    (
                        item
                        for source_id, item in all_players.items()
                        if player.source_id.endswith(f".p.{source_id.rsplit('.', 1)[-1]}")
                    ),
                    None,
                )
                if scraped and scraped.ownership != "TEAM":
                    player.ownership = scraped.ownership
                    player.rostered_by = None
                    player.current_slot = None

    _upsert_draft_picks(db, league, draft_picks)

    snapshot_payload = {
        "league": {
            "name": league.name,
            "season": league.season,
            "url": root_url,
            "settings": metadata["settings"],
            "scoring": metadata["scoring"],
            "roster_slots": metadata["roster_slots"],
        },
        "teams": teams,
        "draft_order": [
            {"slot": slot, "team_name": team_name}
            for slot, team_name in enumerate(draft_order, start=1)
        ],
        "players": [asdict(player) for player in combined_players.values()],
        "draft_picks": [asdict(pick) for pick in draft_picks],
        "player_pool_complete": player_pool_complete,
    }
    db.add(
        DataSnapshot(
            source="yahoo_scrape",
            source_id=league.yahoo_key,
            status="fresh" if player_pool_complete else "partial",
            payload_json=json.dumps(snapshot_payload),
        )
    )
    db.commit()
    return {
        "created": created,
        "updated": 1 - created,
        "resources": resources,
        "players": len(combined_players),
        "draft_order_teams": len(draft_order),
        "draft_picks": len(draft_picks),
        "partial": int(not player_pool_complete),
    }


def _configured_url_for_league(league: League, league_urls: list[str]) -> str | None:
    for league_url in league_urls:
        match = _LEAGUE_PATH_RE.fullmatch(urlparse(league_url).path)
        if not match or not league.yahoo_key:
            continue
        reference = match.group("reference")
        if league.yahoo_key == f"scrape:{league.season}:{reference}":
            return league_url
        if reference.isdigit() and league.yahoo_key.endswith(f".l.{reference}"):
            return league_url
    return None


def _rate_limit_remaining(db: Session) -> int:
    latest = (
        db.query(DataSnapshot)
        .filter_by(source="yahoo_scrape.cooldown")
        .order_by(DataSnapshot.id.desc())
        .first()
    )
    if not latest:
        return 0
    try:
        until = datetime.fromisoformat(json.loads(latest.payload_json or "{}")["retry_at"])
        return max(0, ceil((until - datetime.now(UTC)).total_seconds()))
    except (KeyError, ValueError, TypeError):
        return 0


def _record_rate_limit(db: Session, error: YahooScraperRateLimited) -> None:
    db.rollback()
    seconds = max(error.retry_after_seconds, _rate_limit_remaining(db))
    db.add(
        DataSnapshot(
            source="yahoo_scrape.cooldown",
            status="rate_limited",
            payload_json=json.dumps(
                {"retry_at": (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()}
            ),
        )
    )
    db.commit()


async def _sync_scraped_urls(db: Session, league_urls: list[str], cookie: str) -> dict[str, Any]:
    with job_lock("yahoo-import") as acquired:
        if not acquired:
            raise ValueError("Yahoo synchronization is already running; wait for it to finish.")
        remaining = _rate_limit_remaining(db)
        if remaining:
            raise YahooScraperRateLimited(remaining)
        try:
            return await _sync_scraped_urls_unlocked(db, league_urls, cookie)
        except YahooScraperRateLimited as exc:
            _record_rate_limit(db, exc)
            raise


async def _sync_scraped_urls_unlocked(
    db: Session, league_urls: list[str], cookie: str
) -> dict[str, Any]:
    totals = {
        "created": 0,
        "updated": 0,
        "resources": 0,
        "players": 0,
        "draft_order_teams": 0,
        "draft_picks": 0,
        "partial": 0,
        "ranges_modeled": 0,
        "nflverse_matched": 0,
    }
    errors: list[str] = []
    async with httpx.AsyncClient(
        headers=YAHOO_BROWSER_HEADERS,
        cookies=_cookie_jar(cookie),
        follow_redirects=True,
        timeout=45,
    ) as client:
        for league_url in league_urls:
            try:
                result = await _scrape_one(db, client, league_url)
            except YahooScraperRateLimited:
                # Stop the entire import; retrying other leagues worsens Yahoo's
                # host-wide throttle and used to discard the retry interval.
                raise
            except Exception as exc:
                db.rollback()
                error = str(exc)[:300] or exc.__class__.__name__
                db.add(
                    DataSnapshot(
                        source="yahoo_scrape",
                        source_id=league_url,
                        status="failed",
                        payload_json=json.dumps({"error": error}),
                    )
                )
                db.commit()
                errors.append(f"{league_url}: {error}")
                continue
            latest_snapshot = (
                db.query(DataSnapshot)
                .filter(DataSnapshot.source == "yahoo_scrape")
                .order_by(DataSnapshot.id.desc())
                .first()
            )
            league = (
                db.query(League).filter(League.yahoo_key == latest_snapshot.source_id).one_or_none()
                if latest_snapshot and latest_snapshot.source_id
                else None
            )
            if league is not None:
                try:
                    range_result = await sync_projection_ranges(db, league)
                    totals["ranges_modeled"] += int(range_result.get("modeled", 0))
                    totals["nflverse_matched"] += int(range_result.get("matched", 0))
                except Exception as exc:
                    db.rollback()
                    error = str(exc)[:300] or exc.__class__.__name__
                    errors.append(f"{league_url} nflverse ranges: {error}")
            for key in (
                "created",
                "updated",
                "resources",
                "players",
                "draft_order_teams",
                "draft_picks",
                "partial",
            ):
                totals[key] += int(result[key])
        refreshed_cookie = client.build_request(
            "GET", f"https://{YAHOO_FANTASY_HOST}/"
        ).headers.get("Cookie")
    if refreshed_cookie and refreshed_cookie != cookie:
        _save_secret(db, SCRAPER_COOKIE_KEY, refreshed_cookie)
        db.commit()
    if errors and not totals["created"] and not totals["updated"]:
        raise ValueError(errors[0])
    return {**totals, "errors": errors}


async def sync_scraped_draft_results(db: Session, league_id: int) -> dict[str, int]:
    """Refresh only live draft picks for one league, without re-importing its player pool."""
    league = db.get(League, league_id)
    if league is None:
        raise ValueError("League not found")
    status = scraper_status(db)
    if not status["configured"]:
        raise ValueError("Yahoo authenticated scraping is not configured")
    cookie = _secret(db, SCRAPER_COOKIE_KEY)
    if not cookie:
        raise ValueError("Yahoo browser session is missing")
    league_url = _configured_url_for_league(league, list(status["league_urls"]))
    if league_url is None:
        raise ValueError("This league is not linked to a configured Yahoo scraper URL")

    remaining = max(
        _rate_limit_remaining(db), ceil(_LIVE_DRAFT_BLOCKED_UNTIL.get(league_url, 0) - monotonic())
    )
    if remaining > 0:
        raise YahooScraperRateLimited(remaining)

    resources = 0
    try:
        async with httpx.AsyncClient(
            headers=YAHOO_BROWSER_HEADERS,
            cookies=_cookie_jar(cookie),
            follow_redirects=True,
            timeout=15,
        ) as client:
            try:
                response = await _fetch(client, _page_url(league_url, "draftresults"))
                resources = 1
                draft_picks = parse_draft_page(response.text)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {404, 409}:
                    raise
                resources = 1
                draft_picks = []
    except YahooScraperRateLimited as exc:
        _LIVE_DRAFT_BLOCKED_UNTIL[league_url] = monotonic() + exc.retry_after_seconds
        _record_rate_limit(db, exc)
        raise

    _LIVE_DRAFT_BLOCKED_UNTIL.pop(league_url, None)
    _upsert_draft_picks(db, league, draft_picks)
    db.commit()
    return {"resources": resources, "draft_picks": len(draft_picks)}


async def sync_scraped_league(db: Session, league_id: int) -> dict[str, Any]:
    league = db.get(League, league_id)
    if league is None:
        raise ValueError("League not found")
    status = scraper_status(db)
    if not status["configured"]:
        raise ValueError("Yahoo authenticated scraping is not configured")
    cookie = _secret(db, SCRAPER_COOKIE_KEY)
    if not cookie:
        raise ValueError("Yahoo browser session is missing")
    league_url = _configured_url_for_league(league, list(status["league_urls"]))
    if league_url is None:
        raise ValueError("This league is not linked to a configured Yahoo scraper URL")
    return await _sync_scraped_urls(db, [league_url], cookie)


async def sync_scraped_leagues(db: Session) -> dict[str, Any]:
    status = scraper_status(db)
    if not status["configured"]:
        raise ValueError("Yahoo authenticated scraping is not configured")
    cookie = _secret(db, SCRAPER_COOKIE_KEY)
    if not cookie:
        raise ValueError("Yahoo browser session is missing")
    return await _sync_scraped_urls(db, list(status["league_urls"]), cookie)
