from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import League, Player
from .identity import ensure_player_athlete
from .models import ProjectionSnapshot, ProjectionSnapshotRow


def _hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _range_model(player: Player) -> dict[str, object] | None:
    try:
        evidence = json.loads(player.evidence_json or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(evidence, list):
        return None
    return next(
        (
            item
            for item in evidence
            if isinstance(item, dict) and item.get("kind") == "projection_range_model"
        ),
        None,
    )


def ensure_legacy_projection_snapshot(db: Session, league: League) -> ProjectionSnapshot | None:
    """Capture mutable legacy player values once so a draft never rereads them."""
    players = list(
        db.scalars(select(Player).where(Player.league_id == league.id).order_by(Player.id))
    )
    if not players:
        return None

    for player in players:
        ensure_player_athlete(db, player, league)
    db.flush()

    canonical = [
        {
            "player_id": player.id,
            "athlete_id": player.athlete_id,
            "position": player.position,
            "projected_points": player.projected_points,
            "floor": player.floor,
            "ceiling": player.ceiling,
            "source_value": player.ros_value,
            "risk": player.risk,
            "range_model": _range_model(player),
        }
        for player in players
    ]
    dataset_hash = _hash(canonical)
    existing = db.scalar(
        select(ProjectionSnapshot).where(
            ProjectionSnapshot.league_id == league.id,
            ProjectionSnapshot.dataset_hash == dataset_hash,
        )
    )
    if existing is not None:
        return existing

    model_rows = sum(values["range_model"] is not None for values in canonical)
    enriched = model_rows > 0
    snapshot = ProjectionSnapshot(
        league_id=league.id,
        source="yahoo+nflverse" if enriched else "legacy-player-state",
        import_type="enriched-player-state" if enriched else "legacy-player-state",
        content_hash=dataset_hash,
        dataset_hash=dataset_hash,
        parser_version="nflverse-range-v1" if enriched else "legacy-v1",
        row_count=len(players),
        canonical_coverage=1.0,
        status="ready",
        metadata_json=json.dumps(
            {
                "provenance": "mutable Player columns captured at session creation",
                "raw_stats_available": enriched,
                "range_model_rows": model_rows,
                "range_model_coverage": round(model_rows / len(players), 4),
            }
        ),
    )
    db.add(snapshot)
    db.flush()
    for player, values in zip(players, canonical, strict=True):
        db.add(
            ProjectionSnapshotRow(
                snapshot_id=snapshot.id,
                athlete_id=player.athlete_id,
                league_player_id=player.id,
                source_row_id=player.source_id or f"player:{player.id}",
                position=player.position,
                eligibility_json=json.dumps([player.position]),
                raw_stats_json=json.dumps(values["range_model"] or {}),
                projected_points=player.projected_points,
                floor=player.floor,
                ceiling=player.ceiling,
                source_value=player.ros_value,
                risk=player.risk,
                row_hash=_hash(values),
            )
        )
    return snapshot
