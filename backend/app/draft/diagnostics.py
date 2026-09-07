from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from .models import (
    DraftComputationRun,
    DraftEvent,
    DraftRecommendationSnapshot,
    DraftReconciliationConflict,
    DraftSession,
    ProjectionSnapshot,
)


def diagnostics_payload(db: Session, session: DraftSession) -> dict[str, object]:
    events = list(
        db.scalars(
            select(DraftEvent)
            .where(DraftEvent.session_id == session.id)
            .order_by(DraftEvent.sequence)
        )
    )
    projection = (
        db.get(ProjectionSnapshot, session.projection_snapshot_id)
        if session.projection_snapshot_id
        else None
    )
    recommendation_count = (
        db.scalar(
            select(func.count(DraftRecommendationSnapshot.id)).where(
                DraftRecommendationSnapshot.session_id == session.id
            )
        )
        or 0
    )
    conflicts = list(
        db.scalars(
            select(DraftReconciliationConflict).where(
                DraftReconciliationConflict.session_id == session.id
            )
        )
    )
    computations = list(
        db.scalars(
            select(DraftComputationRun)
            .where(DraftComputationRun.session_id == session.id)
            .order_by(DraftComputationRun.id.desc())
            .limit(20)
        )
    )
    return {
        "schema_version": "draft-diagnostics-v1",
        "generated_at": datetime.now(UTC),
        "session": {
            "id": session.id,
            "league_id": session.league_id,
            "status": session.status,
            "current_sequence": session.current_sequence,
            "source_mode": session.source_mode,
            "team_count": session.team_count,
            "round_count": session.round_count,
            "owner_team_slot": session.owner_team_slot,
            "projection_snapshot_id": session.projection_snapshot_id,
            "ranking_snapshot_id": session.ranking_snapshot_id,
            "config_version": session.config_version,
            "replay_generation": session.replay_generation,
        },
        "capabilities": {
            "draft_suite": settings.draft_suite_enabled,
            "yahoo": settings.draft_yahoo_enabled,
            "forecast": settings.draft_forecast_enabled,
            "simulation": settings.draft_simulation_enabled,
            "ai_explanations": settings.draft_ai_explanations_enabled,
            "nflverse_ranges": settings.draft_nflverse_ranges_enabled,
        },
        "inputs": {
            "projection_dataset_hash": projection.dataset_hash if projection else None,
            "projection_source": projection.source if projection else None,
            "projection_row_count": projection.row_count if projection else 0,
        },
        "counts": {
            "events": len(events),
            "recommendation_snapshots": recommendation_count,
            "unresolved_conflicts": sum(row.status == "unresolved" for row in conflicts),
            "computation_runs": len(computations),
        },
        "events": [
            {
                "id": event.id,
                "sequence": event.sequence,
                "type": event.type,
                "overall_pick": event.overall_pick,
                "team_slot": event.team_slot,
                "player_id": event.player_id,
                "source": event.source,
                "provider_key": event.provider_key,
                "recorded_at": event.recorded_at,
                "metadata_keys": sorted(json.loads(event.metadata_json or "{}").keys()),
            }
            for event in events
        ],
        "computations": [
            {
                "id": run.id,
                "kind": run.kind,
                "status": run.status,
                "session_sequence": run.session_sequence,
                "input_hash": run.input_hash,
                "error_code": run.error_code,
                "created_at": run.created_at,
                "completed_at": run.completed_at,
            }
            for run in computations
        ],
        "redaction": {
            "secrets_included": False,
            "provider_payloads_included": False,
            "notes_included": False,
        },
    }
