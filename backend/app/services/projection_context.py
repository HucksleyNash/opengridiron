"""Lossless source context alongside legacy numeric decision-engine inputs.

The legacy ROS column stays numeric for existing draft/trade consumers. Its
missing/provided/legacy-unknown state is persisted separately; the public API
returns null for missing input and preserves explicitly supplied zero.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from ..models import Player
from ..schemas import PlayerCreate, ProjectionContext


def context_for(player: Player) -> ProjectionContext:
    return ProjectionContext.model_validate_json(player.projection_context_json or "{}")


def record_import_context(player: Player, payload: PlayerCreate, source: str) -> None:
    values = payload.projection.model_dump() if payload.projection else {}
    values["source"] = values.get("source") or source
    values["received_at"] = datetime.now(UTC)
    values["ros_value_state"] = "missing" if payload.ros_value is None else "provided"
    player.projection_context_json = ProjectionContext(**values).model_dump_json()


def public_ros_value(player: Player) -> float | None:
    return None if context_for(player).ros_value_state == "missing" else player.ros_value


def import_payload(row: dict) -> PlayerCreate:
    """CSV flat metadata or JSON nested metadata; blank is not numeric zero."""
    values = dict(row)
    values["source_id"] = str(row.get("source_id") or row.get("id") or row.get("name"))
    values["pro_team"] = row.get("pro_team") or row.get("team") or "FA"
    values["position"] = row.get("position") or "UNK"
    for key in ("rostered_by", "current_slot"):
        values[key] = row.get(key) or None
    for key in ("projected_points", "floor", "ceiling", "risk", "ros_value"):
        raw = row.get(key, row.get("projection") if key == "projected_points" else None)
        # A nested projection object is provenance, never a numeric alias.
        if isinstance(raw, dict):
            raw = None
        values[key] = (
            raw
            if raw not in (None, "")
            else (None if key == "ros_value" else 0.5 if key == "risk" else 0)
        )
    if not isinstance(row.get("projection"), dict):
        values["projection"] = {
            key: row[f"projection_{key}"]
            for key in ("source", "period", "season", "week", "source_updated_at", "scoring_basis")
            if row.get(f"projection_{key}") not in (None, "")
        }
        if row.get("projection_scoring"):
            values["projection"]["scoring"] = json.loads(row["projection_scoring"])
    return PlayerCreate.model_validate(values)
