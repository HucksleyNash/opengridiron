from __future__ import annotations

import csv
import hashlib
import io
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import League, Player
from ..services.projections import score_projection
from .errors import DraftDomainError, not_found
from .identity import ensure_player_athlete
from .models import (
    DraftInputPreview,
    DraftRankingSnapshot,
    ProjectionSnapshot,
    ProjectionSnapshotRow,
)
from .schemas import DraftInputCommit

MAX_INPUT_BYTES = 1_000_000
MAX_INPUT_ROWS = 1_000
MAX_PREVIEWS = 20
PREVIEW_TTL = timedelta(hours=1)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _preview_json(value: str) -> list[dict[str, object]]:
    try:
        rows = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _expire_old_previews(db: Session, league_id: int) -> None:
    now = datetime.now(UTC)
    expired = list(
        db.scalars(
            select(DraftInputPreview).where(
                DraftInputPreview.league_id == league_id,
                DraftInputPreview.status == "staged",
                DraftInputPreview.expires_at <= now,
            )
        )
    )
    for preview in expired:
        preview.status = "expired"


def _float(row: dict[str, object], key: str, default: float | None = None) -> float | None:
    value = row.get(key)
    if value in {None, ""}:
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _raw_stats(row: dict[str, object]) -> tuple[dict[str, float], str | None]:
    value = row.get("raw_stats")
    if value is None or value == "":
        return {}, None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}, "raw_stats must be a JSON object with numeric values."
    if not isinstance(value, dict):
        return {}, "raw_stats must be an object with numeric values."
    stats: dict[str, float] = {}
    for key, raw_value in value.items():
        try:
            stats[str(key)] = float(raw_value)
        except (TypeError, ValueError):
            return {}, f"raw_stats.{key} must be numeric."
    return stats, None


def _parse(content: bytes, filename: str) -> list[dict[str, object]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DraftDomainError(422, "input_encoding", "Draft inputs must use UTF-8.") from exc
    if filename.lower().endswith(".json"):
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DraftDomainError(422, "input_json", "The JSON input is malformed.") from exc
        if isinstance(value, dict):
            value = value.get("rows")
        if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
            raise DraftDomainError(422, "input_shape", "JSON must contain an array of row objects.")
        return value
    if filename.lower().endswith(".csv"):
        return [dict(row) for row in csv.DictReader(io.StringIO(text))]
    raise DraftDomainError(422, "input_type", "Upload a .csv or .json draft input.")


def _player_for_row(db: Session, league_id: int, row: dict[str, object]) -> Player | None:
    player_id = row.get("player_id")
    if player_id not in {None, ""}:
        try:
            player = db.get(Player, int(str(player_id)))
        except ValueError:
            return None
        return player if player and player.league_id == league_id else None
    source_id = str(row.get("source_id") or "").strip()
    if source_id:
        return db.scalar(
            select(Player).where(
                Player.league_id == league_id,
                Player.source_id == source_id,
            )
        )
    return None


def preview_input(
    db: Session,
    league_id: int,
    *,
    input_type: Literal["projection", "ranking", "combined"],
    filename: str,
    content: bytes,
) -> dict[str, object]:
    league = db.get(League, league_id)
    if league is None:
        raise not_found("league", league_id)
    if len(content) > MAX_INPUT_BYTES:
        raise DraftDomainError(413, "input_too_large", "Draft input files are limited to 1 MB.")
    raw_rows = _parse(content, filename)
    if not raw_rows:
        raise DraftDomainError(422, "input_empty", "The draft input contains no rows.")
    if len(raw_rows) > MAX_INPUT_ROWS:
        raise DraftDomainError(
            413,
            "input_too_many_rows",
            f"Draft input files are limited to {MAX_INPUT_ROWS} rows.",
        )
    content_hash = hashlib.sha256(content).hexdigest()
    try:
        league_scoring = json.loads(league.scoring_json or "{}")
    except json.JSONDecodeError:
        league_scoring = {}
    rows: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    blocking: list[dict[str, object]] = []
    seen_athletes: set[int] = set()
    for index, raw in enumerate(raw_rows, start=2 if filename.lower().endswith(".csv") else 1):
        player = _player_for_row(db, league_id, raw)
        if player is None:
            blocking.append(
                {
                    "row": index,
                    "code": "identity_required",
                    "message": (
                        "Use player_id or a provider source_id already mapped in this league."
                    ),
                    "observed_name": raw.get("name"),
                }
            )
            continue
        identity_key = player.athlete_id or -player.id
        if identity_key in seen_athletes:
            blocking.append(
                {
                    "row": index,
                    "code": "duplicate_athlete",
                    "message": "An athlete may appear only once in a committed dataset.",
                    "player_id": player.id,
                }
            )
            continue
        seen_athletes.add(identity_key)
        source_projected = _float(raw, "projected_points")
        raw_stats, raw_stats_error = _raw_stats(raw)
        if input_type in {"projection", "combined"} and raw_stats_error:
            blocking.append(
                {
                    "row": index,
                    "code": "raw_stats_invalid",
                    "message": raw_stats_error,
                    "player_id": player.id,
                }
            )
            continue
        projected = (
            score_projection(raw_stats, league_scoring)
            if input_type in {"projection", "combined"} and raw_stats
            else source_projected
        )
        adp = _float(raw, "adp", _float(raw, "overall_rank"))
        if input_type in {"projection", "combined"} and projected is None:
            blocking.append(
                {
                    "row": index,
                    "code": "projected_points_required",
                    "message": (
                        "Projection rows require numeric projected_points or raw_stats that can "
                        "be scored with this league's rules."
                    ),
                    "player_id": player.id,
                }
            )
            continue
        if input_type in {"ranking", "combined"} and adp is None:
            blocking.append(
                {
                    "row": index,
                    "code": "ranking_required",
                    "message": "Ranking rows require numeric adp or overall_rank.",
                    "player_id": player.id,
                }
            )
            continue
        risk = _float(raw, "risk", player.risk)
        if risk is None or not 0 <= risk <= 1:
            blocking.append(
                {
                    "row": index,
                    "code": "risk_range",
                    "message": "Risk must be between 0 and 1.",
                    "player_id": player.id,
                }
            )
            continue
        if input_type in {"projection", "combined"} and not raw_stats:
            warnings.append(
                {
                    "row": index,
                    "code": "source_points_only",
                    "message": (
                        "No raw stats supplied; scoring cannot be recomputed from components."
                    ),
                }
            )
        rows.append(
            {
                "player_id": player.id,
                "athlete_id": player.athlete_id,
                "source_row_id": str(raw.get("source_id") or player.source_id or player.id),
                "position": str(raw.get("position") or player.position).upper(),
                "projected_points": projected,
                "raw_stats": raw_stats,
                "floor": _float(raw, "floor", projected or 0.0),
                "ceiling": _float(raw, "ceiling", projected or 0.0),
                "source_value": _float(raw, "value", projected or 0.0),
                "risk": risk,
                "adp": adp,
                "overall_rank": _float(raw, "overall_rank", adp),
                "position_rank": _float(raw, "position_rank"),
                "uncertainty": _float(raw, "uncertainty", 0.5),
            }
        )
    preview_id = secrets.token_urlsafe(18)
    _expire_old_previews(db, league_id)
    staged = list(
        db.scalars(
            select(DraftInputPreview)
            .where(
                DraftInputPreview.league_id == league_id,
                DraftInputPreview.status == "staged",
            )
            .order_by(DraftInputPreview.created_at)
        )
    )
    for old_preview in staged[: max(0, len(staged) - MAX_PREVIEWS + 1)]:
        old_preview.status = "expired"
    created_at = datetime.now(UTC)
    preview = DraftInputPreview(
        id=preview_id,
        league_id=league_id,
        input_type=input_type,
        filename=filename,
        content_hash=content_hash,
        rows_json=json.dumps(rows),
        warnings_json=json.dumps(warnings),
        blocking_errors_json=json.dumps(blocking),
        status="staged",
        created_at=created_at,
        expires_at=created_at + PREVIEW_TTL,
    )
    db.add(preview)
    db.commit()
    db.refresh(preview)
    return preview_payload(preview)


def preview_payload(preview: DraftInputPreview) -> dict[str, object]:
    rows = _preview_json(preview.rows_json)
    warnings = _preview_json(preview.warnings_json)
    blocking_errors = _preview_json(preview.blocking_errors_json)
    return {
        "preview_id": preview.id,
        "input_type": preview.input_type,
        "filename": preview.filename,
        "content_hash": preview.content_hash,
        "row_count": len(rows),
        "matched_count": len(rows),
        "warnings": warnings,
        "blocking_errors": blocking_errors,
        "can_commit": preview.status == "staged" and not blocking_errors and bool(rows),
        "sample": rows[:5],
        "expires_at": preview.expires_at,
    }


def commit_input(
    db: Session,
    league_id: int,
    preview_id: str,
    payload: DraftInputCommit,
) -> dict[str, object]:
    preview = db.get(DraftInputPreview, preview_id)
    if preview is None or preview.league_id != league_id:
        raise DraftDomainError(404, "input_preview_not_found", "The staged input preview expired.")
    if preview.content_hash != payload.content_hash:
        raise DraftDomainError(409, "input_hash_mismatch", "The preview content hash changed.")
    if preview.status == "committed" and preview.result_json:
        return json.loads(preview.result_json)
    if preview.status == "expired" or datetime.now(UTC) > _aware(preview.expires_at):
        preview.status = "expired"
        db.commit()
        raise DraftDomainError(410, "input_preview_expired", "The staged input preview expired.")
    rows = _preview_json(preview.rows_json)
    warnings = _preview_json(preview.warnings_json)
    blocking_errors = _preview_json(preview.blocking_errors_json)
    if blocking_errors:
        raise DraftDomainError(
            422,
            "input_blocked",
            "Resolve every blocking row before committing the dataset.",
            {"blocking_errors": blocking_errors},
        )
    if warnings and not payload.acknowledge_warnings:
        raise DraftDomainError(
            422,
            "input_warning_acknowledgement_required",
            "Acknowledge the preview warnings before committing.",
            {"warnings": warnings},
        )
    league = db.get(League, league_id)
    if league is None:
        raise not_found("league", league_id)
    for row in rows:
        player = db.get(Player, int(row["player_id"]))
        if player is None or player.league_id != league_id:
            raise DraftDomainError(
                409,
                "input_identity_changed",
                "A mapped player changed after preview; preview the file again.",
            )
        athlete = ensure_player_athlete(db, player, league)
        row["athlete_id"] = athlete.id
    db.flush()
    dataset_hash = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    projection_id: int | None = None
    ranking_id: int | None = None
    if preview.input_type in {"projection", "combined"}:
        snapshot = db.scalar(
            select(ProjectionSnapshot).where(
                ProjectionSnapshot.league_id == league_id,
                ProjectionSnapshot.dataset_hash == dataset_hash,
            )
        )
        if snapshot is None:
            snapshot = ProjectionSnapshot(
                league_id=league_id,
                source="owner-import",
                import_type=preview.input_type,
                content_hash=preview.content_hash,
                dataset_hash=dataset_hash,
                parser_version="draft-input-v1",
                row_count=len(rows),
                canonical_coverage=1.0,
                status="ready",
                metadata_json=json.dumps({"filename": preview.filename}),
            )
            db.add(snapshot)
            db.flush()
            for row in rows:
                row_hash = hashlib.sha256(
                    json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                db.add(
                    ProjectionSnapshotRow(
                        snapshot_id=snapshot.id,
                        athlete_id=int(row["athlete_id"]),
                        league_player_id=int(row["player_id"]),
                        source_row_id=str(row["source_row_id"]),
                        position=str(row["position"]),
                        eligibility_json=json.dumps([row["position"]]),
                        raw_stats_json=json.dumps(
                            {"projection_stats": row.get("raw_stats", {})}, sort_keys=True
                        ),
                        projected_points=float(row["projected_points"] or 0.0),
                        floor=float(row["floor"] or 0.0),
                        ceiling=float(row["ceiling"] or 0.0),
                        source_value=float(row["source_value"] or 0.0),
                        risk=float(row["risk"] or 0.5),
                        row_hash=row_hash,
                    )
                )
        projection_id = snapshot.id
    if preview.input_type in {"ranking", "combined"}:
        ranking = db.scalar(
            select(DraftRankingSnapshot).where(
                DraftRankingSnapshot.league_id == league_id,
                DraftRankingSnapshot.dataset_hash == dataset_hash,
            )
        )
        if ranking is None:
            ranking = DraftRankingSnapshot(
                league_id=league_id,
                source="owner-import",
                content_hash=preview.content_hash,
                dataset_hash=dataset_hash,
                row_count=len(rows),
                canonical_coverage=1.0,
                status="ready",
                rows_json=json.dumps(rows),
                metadata_json=json.dumps(
                    {
                        "filename": preview.filename,
                        "probability_gate": "ordinal_only",
                    }
                ),
            )
            db.add(ranking)
            db.flush()
        ranking_id = ranking.id
    result = {
        "projection_snapshot_id": projection_id,
        "ranking_snapshot_id": ranking_id,
        "dataset_hash": dataset_hash,
        "row_count": len(rows),
        "status": "committed",
    }
    preview.status = "committed"
    preview.committed_at = datetime.now(UTC)
    preview.result_json = json.dumps(result)
    db.commit()
    return result
