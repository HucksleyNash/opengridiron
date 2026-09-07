from __future__ import annotations

import json

from sqlalchemy.orm import Session

from .models import DraftRankingSnapshot, DraftSession


def market_ranks(db: Session, session: DraftSession) -> dict[int, float]:
    if session.ranking_snapshot_id is None:
        return {}
    snapshot = db.get(DraftRankingSnapshot, session.ranking_snapshot_id)
    if snapshot is None or snapshot.status != "ready":
        return {}
    try:
        rows = json.loads(snapshot.rows_json or "[]")
    except json.JSONDecodeError:
        return {}
    result: dict[int, float] = {}
    for fallback, row in enumerate(rows if isinstance(rows, list) else [], start=1):
        if not isinstance(row, dict) or not str(row.get("player_id") or "").isdigit():
            continue
        try:
            rank = float(row.get("overall_rank") or row.get("adp") or fallback)
        except (TypeError, ValueError):
            rank = float(fallback)
        if rank > 0:
            result[int(row["player_id"])] = rank
    return result


def normalized_market_values(player_ids: list[int], ranks: dict[int, float]) -> dict[int, float]:
    present = [ranks[player_id] for player_id in player_ids if player_id in ranks]
    if not present:
        return {player_id: 0.5 for player_id in player_ids}
    low = min(present)
    high = max(present)
    span = high - low
    return {
        player_id: (
            1.0 - (ranks[player_id] - low) / span
            if player_id in ranks and span > 0
            else 1.0
            if player_id in ranks
            else 0.5
        )
        for player_id in player_ids
    }
