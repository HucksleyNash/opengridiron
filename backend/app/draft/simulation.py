from __future__ import annotations

import hashlib
import json
import random
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from .errors import DraftDomainError, not_found
from .models import DraftComputationRun, DraftRecommendationSnapshot, DraftSession
from .schemas import DraftSimulationRequest

_process_executor = ProcessPoolExecutor(max_workers=1)
_orchestrator = ThreadPoolExecutor(max_workers=1)


def _simulate_candidate(payload: dict[str, object]) -> dict[str, object]:
    seed = int(str(payload["seed"])[:16], 16)
    rng = random.Random(seed)
    playouts = int(payload["playouts"])
    candidates = list(payload["candidates"])  # type: ignore[arg-type]
    results = []
    for candidate in candidates:
        candidate = dict(candidate)
        utilities = []
        for _ in range(playouts):
            next_turn_noise = rng.triangular(-8.0, 11.0, 1.0)
            roster_value = float(candidate["vor"]) + 0.25 * float(candidate["score"])
            utilities.append(roster_value + next_turn_noise)
        utilities.sort()
        results.append(
            {
                "player_id": candidate["player_id"],
                "name": candidate["name"],
                "mean_utility": round(sum(utilities) / len(utilities), 3),
                "lower_utility": round(utilities[int(len(utilities) * 0.1)], 3),
                "upper_utility": round(utilities[int(len(utilities) * 0.9)], 3),
                "completed_playouts": len(utilities),
            }
        )
    results.sort(key=lambda item: (-float(item["mean_utility"]), int(item["player_id"])))
    return {"status": "ready", "results": results, "playouts": playouts}


def computation_payload(run: DraftComputationRun) -> dict[str, object]:
    result = json.loads(run.result_json) if run.result_json else {}
    if run.status == "ready":
        return {"run_id": run.id, **result}
    if run.status == "stale":
        return {
            "run_id": run.id,
            "status": "stale",
            "reason": run.error_code or "sequence_changed",
            "results": [],
        }
    if run.status in {"queued", "running"}:
        return {"run_id": run.id, "status": run.status, "results": []}
    return {
        "run_id": run.id,
        "status": "unavailable",
        "reason": run.error_code or run.status,
        "results": [],
    }


def _complete_simulation(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(DraftComputationRun, run_id)
        if run is None or run.status != "queued":
            return
        run.status = "running"
        run.started_at = datetime.now(UTC)
        request_payload = json.loads(run.request_json)
        db.commit()

        future = _process_executor.submit(_simulate_candidate, request_payload)
        try:
            result = future.result(timeout=2.0)
        except TimeoutError:
            future.cancel()
            run = db.get(DraftComputationRun, run_id)
            if run is not None:
                run.status = "timed_out"
                run.error_code = "simulation_timeout"
                run.completed_at = datetime.now(UTC)
                db.commit()
            return
        except Exception:
            run = db.get(DraftComputationRun, run_id)
            if run is not None:
                run.status = "failed"
                run.error_code = "simulation_worker_failed"
                run.completed_at = datetime.now(UTC)
                db.commit()
            return

        run = db.get(DraftComputationRun, run_id)
        if run is None:
            return
        session = db.get(DraftSession, run.session_id)
        run.result_json = json.dumps(result)
        run.completed_at = datetime.now(UTC)
        if session is None or session.current_sequence != run.session_sequence:
            run.status = "stale"
            run.error_code = "sequence_changed"
        else:
            run.status = "ready"
        db.commit()
    finally:
        db.close()


def enqueue_simulation(
    db: Session, session: DraftSession, payload: DraftSimulationRequest
) -> dict[str, object]:
    if session.current_sequence != payload.expected_sequence:
        raise DraftDomainError(
            409,
            "draft_conflict",
            "The draft changed before simulation started.",
            {"current_sequence": session.current_sequence},
        )
    snapshot = db.scalar(
        select(DraftRecommendationSnapshot)
        .where(
            DraftRecommendationSnapshot.session_id == session.id,
            DraftRecommendationSnapshot.session_sequence == session.current_sequence,
            DraftRecommendationSnapshot.status == "ready",
        )
        .order_by(DraftRecommendationSnapshot.id.desc())
    )
    if snapshot is None:
        raise DraftDomainError(
            425,
            "advice_pending",
            "Wait for deterministic advice before comparing scenarios.",
            {"retryable": True},
        )
    candidates = json.loads(snapshot.candidates_json) + json.loads(snapshot.alternatives_json)
    by_id = {int(candidate["player_id"]): candidate for candidate in candidates}
    if any(player_id not in by_id for player_id in payload.player_ids):
        raise DraftDomainError(
            422,
            "simulation_candidate_invalid",
            "Compare only candidates in the current recommendation snapshot.",
        )
    selected = [by_id[player_id] for player_id in payload.player_ids]
    request_payload = {
        "seed": hashlib.sha256(
            f"{session.id}:{session.current_sequence}:{payload.player_ids}".encode()
        ).hexdigest(),
        "playouts": payload.playouts,
        "candidates": selected,
    }
    request_json = json.dumps(request_payload, sort_keys=True)
    existing = db.scalar(
        select(DraftComputationRun)
        .where(
            DraftComputationRun.session_id == session.id,
            DraftComputationRun.kind == "simulation",
            DraftComputationRun.session_sequence == session.current_sequence,
            DraftComputationRun.input_hash == snapshot.input_hash,
            DraftComputationRun.request_json == request_json,
            DraftComputationRun.status.in_(["queued", "running", "ready"]),
        )
        .order_by(DraftComputationRun.id.desc())
    )
    if existing is not None:
        return computation_payload(existing)
    run = DraftComputationRun(
        session_id=session.id,
        kind="simulation",
        status="queued",
        session_sequence=session.current_sequence,
        input_hash=snapshot.input_hash,
        request_json=request_json,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    _orchestrator.submit(_complete_simulation, run.id)
    return computation_payload(run)


def read_computation(db: Session, session_id: int, run_id: int) -> dict[str, object]:
    run = db.get(DraftComputationRun, run_id)
    if run is None or run.session_id != session_id:
        raise not_found("draft_computation", run_id)
    return computation_payload(run)


def recover_interrupted_computations(db: Session) -> int:
    rows = list(
        db.scalars(
            select(DraftComputationRun).where(DraftComputationRun.status.in_(["queued", "running"]))
        )
    )
    now = datetime.now(UTC)
    for run in rows:
        run.status = "interrupted"
        run.error_code = "application_restarted"
        run.completed_at = now
    if rows:
        db.commit()
    return len(rows)
