from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings
from ..models import PushSubscription


def push_enabled() -> bool:
    return bool(settings.vapid_private_key and settings.vapid_public_key)


def send_notification(
    db: Session, title: str, message: str, url: str | None = None
) -> dict[str, int]:
    if not push_enabled():
        return {"sent": 0, "failed": 0}
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return {"sent": 0, "failed": 0}

    payload = json.dumps({"title": title, "body": message, "url": url or "/news"})
    sent = failed = 0
    stale: list[PushSubscription] = []
    for row in db.query(PushSubscription).all():
        try:
            webpush(
                subscription_info=json.loads(row.subscription_json),
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_claims_email},
                ttl=900,
            )
            sent += 1
        except WebPushException as exc:
            failed += 1
            response: Any = getattr(exc, "response", None)
            if response is not None and response.status_code in {404, 410}:
                stale.append(row)
    for row in stale:
        db.delete(row)
    db.commit()
    return {"sent": sent, "failed": failed}
