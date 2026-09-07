from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag
from sqlalchemy.orm import Session

from ..models import Alert, NewsItem, NewsSource

INJURY_WORDS = {
    "acl",
    "ankle",
    "concussion",
    "doubtful",
    "hamstring",
    "injured",
    "injury",
    "ir",
    "knee",
    "limited",
    "out",
    "questionable",
    "shoulder",
    "surgery",
}
TRANSACTION_WORDS = {
    "activated",
    "claimed",
    "cut",
    "released",
    "signed",
    "signs",
    "traded",
    "waived",
}
ROLE_WORDS = {"benched", "coach", "depth chart", "named starter", "reps", "starter"}


def _classify(title: str, excerpt: str) -> tuple[str, str]:
    text = f"{title} {excerpt}".lower()
    tokens = set(re.findall(r"[a-z]+", text))
    if tokens & INJURY_WORDS:
        severity = "urgent" if tokens & {"out", "ir", "surgery", "acl", "concussion"} else "warning"
        return "injury", severity
    if tokens & TRANSACTION_WORDS:
        return "transaction", "warning"
    if any(word in text for word in ROLE_WORDS):
        return "role", "info"
    if "suspend" in text:
        return "suspension", "urgent"
    return "news", "info"


def _published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = parsedate_to_datetime(value)
        return result if result.tzinfo else result.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return result if result.tzinfo else result.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            return None


def _parse_html_items(
    db: Session, source: NewsSource, content: str, *, require_items: bool = False
) -> int:
    soup = BeautifulSoup(content, "html.parser")
    created = 0
    candidates: list[tuple[Tag, Tag]] = []
    for article in soup.select("article")[:50]:
        heading = article.select_one("h1,h2,h3,h4")
        link = article.select_one("a[href]")
        if heading and link:
            candidates.append((heading, link))
    if not candidates:
        for heading in soup.select("h2,h3,h4"):
            link = heading.find_parent("a", href=True)
            if link:
                candidates.append((heading, link))
            if len(candidates) >= 50:
                break
    if require_items and not candidates:
        raise ValueError("Source returned HTML without recognizable news items")
    for heading, link in candidates:
        url = urljoin(source.url, str(link.get("href")))
        container: Tag | None = heading.parent if isinstance(heading.parent, Tag) else None
        for _ in range(4):
            if not container or container.select_one("p,time[datetime]"):
                break
            container = container.parent if isinstance(container.parent, Tag) else None
        excerpt_node = container.select_one("p") if container else None
        time_node = container.select_one("time[datetime]") if container else None
        excerpt = excerpt_node.get_text(" ", strip=True) if excerpt_node else ""
        published = _published(str(time_node.get("datetime"))) if time_node else None
        if _store_item(
            db,
            source,
            url,
            heading.get_text(" ", strip=True),
            excerpt,
            published,
        ):
            created += 1
    return created


def _store_item(
    db: Session, source: NewsSource, url: str, title: str, excerpt: str, published: datetime | None
) -> bool:
    canonical = url.split("#", 1)[0]
    content_hash = hashlib.sha256(f"{canonical}|{title}|{excerpt}".encode()).hexdigest()
    existing = db.query(NewsItem).filter(NewsItem.canonical_url == canonical).one_or_none()
    if existing and existing.content_hash == content_hash:
        return False
    category, severity = _classify(title, excerpt)
    if existing:
        existing.title = title
        existing.excerpt = excerpt[:600]
        existing.content_hash = content_hash
        existing.category = category
        existing.severity = severity
        existing.retrieved_at = datetime.now(UTC)
        item = existing
    else:
        item = NewsItem(
            source_id=source.id,
            canonical_url=canonical,
            content_hash=content_hash,
            title=title[:1000],
            excerpt=excerpt[:600],
            category=category,
            severity=severity,
            published_at=published,
        )
        db.add(item)
    if severity in {"warning", "urgent"}:
        fingerprint = hashlib.sha256(f"{canonical}|{content_hash}".encode()).hexdigest()
        if not db.query(Alert).filter(Alert.fingerprint == fingerprint).first():
            db.add(
                Alert(
                    fingerprint=fingerprint,
                    title=title[:1000],
                    message=excerpt[:500] or f"New {category} report",
                    severity=severity,
                    url=canonical,
                )
            )
    return True


async def fetch_source(db: Session, source: NewsSource) -> dict[str, int | str]:
    headers = {"User-Agent": "OpenGridiron/0.1 (+personal self-hosted reader)"}
    if source.etag:
        headers["If-None-Match"] = source.etag
    if source.last_modified:
        headers["If-Modified-Since"] = source.last_modified
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
        response = await client.get(source.url)
    if response.status_code == 304:
        source.last_fetched_at = datetime.now(UTC)
        db.commit()
        return {"status": "not_modified", "created": 0}
    response.raise_for_status()
    created = 0
    if source.source_type == "rss":
        feed = feedparser.parse(response.content)
        for entry in feed.entries[:50]:
            title = str(entry.get("title", "Untitled")).strip()
            url = str(entry.get("link", source.url))
            excerpt = BeautifulSoup(str(entry.get("summary", "")), "html.parser").get_text(
                " ", strip=True
            )
            if _store_item(db, source, url, title, excerpt, _published(entry.get("published"))):
                created += 1
        if not feed.entries and "html" in response.headers.get("content-type", "").lower():
            created += _parse_html_items(db, source, response.text, require_items=True)
        elif not feed.entries and not feed.version:
            raise ValueError("Source returned an empty or invalid feed")
    else:
        created += _parse_html_items(db, source, response.text)
    source.last_fetched_at = datetime.now(UTC)
    source.etag = response.headers.get("etag")
    source.last_modified = response.headers.get("last-modified")
    db.commit()
    return {"status": "ok", "created": created}


def ensure_default_sources(db: Session) -> None:
    defaults = [
        ("NFL News", "https://www.nfl.com/news?service=rss", "rss", True),
        ("NFL Injuries", "https://www.nfl.com/injuries/", "html", True),
        ("ESPN NFL", "https://www.espn.com/espn/rss/nfl/news", "rss", False),
        ("CBS Sports NFL", "https://www.cbssports.com/rss/headlines/nfl/", "rss", False),
    ]
    for name, url, source_type, official in defaults:
        if not db.query(NewsSource).filter(NewsSource.url == url).first():
            db.add(NewsSource(name=name, url=url, source_type=source_type, official=official))
    db.commit()
