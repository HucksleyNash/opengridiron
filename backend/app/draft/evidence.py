from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..models import NewsItem


def attributed_evidence(db: Session, athlete_ids: set[int]) -> dict[int, list[dict[str, object]]]:
    """Return explicitly identity-linked evidence; strings/names never auto-match."""
    if not athlete_ids:
        return {}
    rows = list(
        db.scalars(
            select(NewsItem)
            .options(joinedload(NewsItem.source))
            .order_by(NewsItem.published_at.desc(), NewsItem.retrieved_at.desc())
            .limit(500)
        )
    )
    result: dict[int, list[dict[str, object]]] = defaultdict(list)
    now = datetime.now(UTC)
    for item in rows:
        try:
            identities = json.loads(item.players_json or "[]")
        except json.JSONDecodeError:
            continue
        if not isinstance(identities, list):
            continue
        mapped_ids = {
            int(value["athlete_id"])
            for value in identities
            if isinstance(value, dict) and isinstance(value.get("athlete_id"), int)
        }
        for athlete_id in mapped_ids & athlete_ids:
            if len(result[athlete_id]) >= 3:
                continue
            published = item.published_at or item.retrieved_at
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            result[athlete_id].append(
                {
                    "id": item.id,
                    "title": item.title,
                    "excerpt": item.excerpt,
                    "category": item.category,
                    "severity": item.severity,
                    "url": item.canonical_url,
                    "source": item.source.name if item.source else "Attributed source",
                    "official": item.source.official if item.source else False,
                    "published_at": published.isoformat(),
                    "stale": now - published > timedelta(hours=36),
                    "score_effect": 0,
                }
            )
    return dict(result)
