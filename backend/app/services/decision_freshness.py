"""Keep stale availability and schedule inputs from producing actionable swaps."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..models import DataSnapshot


def yahoo_roster_source(db, league, now: datetime) -> dict | None:
    if not (league.source or "").startswith("yahoo"):
        return None
    # Freshness needs metadata only. Sorting archived response bodies can exceed
    # SQLite's temporary storage limit even when the data volume has free space.
    snapshots = db.query(
        DataSnapshot.id,
        DataSnapshot.source,
        DataSnapshot.retrieved_at,
        DataSnapshot.status,
    ).filter(
        DataSnapshot.source.in_(["yahoo", "yahoo_scrape"]),
        DataSnapshot.source_id.startswith(league.yahoo_key or "__missing__"),
    )
    latest = snapshots.order_by(DataSnapshot.id.desc()).first()
    if latest is None:
        return {
            "name": "Yahoo league",
            "status": "unavailable",
            "detail": "No successful roster snapshot is available.",
        }
    received = (
        latest.retrieved_at.replace(tzinfo=UTC)
        if latest.retrieved_at.tzinfo is None
        else latest.retrieved_at
    )
    status = "stale" if now - received > timedelta(hours=6) else "available"
    successful = {"fresh", "available", "refreshed"}
    # A scrape stores the entire import in one record. OAuth uses many pages:
    # check all their statuses in SQL without loading history or cutting off a
    # failed page after an arbitrary number of successful pages.
    incomplete = latest.status not in successful
    if latest.source != "yahoo_scrape":
        incomplete = db.query(
            snapshots.filter(
                DataSnapshot.id <= latest.id,
                DataSnapshot.retrieved_at > received - timedelta(minutes=30),
                DataSnapshot.retrieved_at < received + timedelta(minutes=30),
                DataSnapshot.status.not_in(successful),
            ).exists()
        ).scalar()
    if incomplete:
        status = "partial"
    return {
        "name": "Yahoo league",
        "status": status,
        "received_at": received.isoformat(),
        "snapshot_id": latest.id,
    }


def recommendation_gate(sources: list[dict], season: int, week: int) -> list[str]:
    required = {"Yahoo league", "NFL schedule", "Player identities", f"NFL statistics {season - 1}"}
    if week > 1:
        required.add(f"NFL statistics {season}")
    return [
        f"{source['name']} is {source['status']}; refresh it before making roster changes."
        for source in sources
        if source["name"] in required
        and source.get("status") in {"stale", "partial", "unavailable", "failed", "running"}
    ]


def withhold_decisions(decisions: dict, reasons: list[str]) -> dict:
    if not reasons:
        return decisions
    return {
        **decisions,
        "assignments": [],
        "waivers": [],
        "error": " ".join(reasons),
        "recommendations_withheld": True,
        "partial_total": True,
        "gain": None,
        "recommended_points": None,
    }
