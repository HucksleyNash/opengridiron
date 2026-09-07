"""Keep stale availability and schedule inputs from producing actionable swaps."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..models import DataSnapshot


def yahoo_roster_source(db, league, now: datetime) -> dict | None:
    if not (league.source or "").startswith("yahoo"):
        return None
    snapshots = (
        db.query(DataSnapshot)
        .filter(
            DataSnapshot.source.in_(["yahoo", "yahoo_scrape"]),
            DataSnapshot.source_id.startswith(league.yahoo_key or "__missing__"),
        )
        .order_by(DataSnapshot.id.desc())
        .limit(200)
        .all()
    )
    if not snapshots:
        return {
            "name": "Yahoo league",
            "status": "unavailable",
            "detail": "No successful roster snapshot is available.",
        }
    latest = snapshots[0]
    received = (
        latest.retrieved_at.replace(tzinfo=UTC)
        if latest.retrieved_at.tzinfo is None
        else latest.retrieved_at
    )
    # Include every resource from the latest import, so a failed roster page cannot
    # be hidden by a successful later page in the same import.
    recent = [
        s
        for s in snapshots
        if abs((s.retrieved_at.replace(tzinfo=UTC) - received).total_seconds()) < 1800
    ]
    if latest.source == "yahoo_scrape":
        recent = [latest]
    status = "stale" if now - received > timedelta(hours=6) else "available"
    if any(s.status not in {"fresh", "available", "refreshed"} for s in recent):
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
