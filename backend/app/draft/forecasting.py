from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from ..config import settings
from .models import DraftRankingSnapshot, DraftSession
from .reducer import ReducedPick, next_owner_pick


def next_turn_owner_pick(
    completed_picks: int, team_count: int, owner_slot: int, total: int
) -> int | None:
    upcoming_owner_pick = next_owner_pick(completed_picks, team_count, owner_slot, total)
    if upcoming_owner_pick is None:
        return None
    return next_owner_pick(upcoming_owner_pick, team_count, owner_slot, total)


def availability_guidance(
    db: Session,
    session: DraftSession,
    picks: list[ReducedPick],
    *,
    player_ids: list[int] | None = None,
) -> dict[int, dict[str, object]]:
    def unavailable(reason: str, label: str, status: str = "unavailable"):
        return {
            player_id: {"status": status, "reason": reason, "label": label}
            for player_id in player_ids or []
        }

    total = session.team_count * session.round_count
    next_pick = next_turn_owner_pick(len(picks), session.team_count, session.owner_team_slot, total)
    if next_pick is None and player_ids is not None:
        return unavailable("no_later_owner_pick", "No later owner pick in this draft")
    if not settings.draft_forecast_enabled:
        return unavailable("forecast_disabled", "Next-turn forecasts are disabled")
    if session.ranking_snapshot_id is None:
        return unavailable("ranking_missing", "Sync rankings to estimate next-turn availability")
    ranking = db.get(DraftRankingSnapshot, session.ranking_snapshot_id)
    if ranking is None:
        return unavailable("ranking_missing", "Sync rankings to estimate next-turn availability")
    if ranking.status != "ready":
        return unavailable("ranking_incomplete", "Rankings are incomplete; sync rankings again")
    if ranking.canonical_coverage < 0.9:
        return unavailable("ranking_coverage", "Rankings cover fewer than 90% of source players")
    retrieved = ranking.retrieved_at
    if retrieved.tzinfo is None:
        retrieved = retrieved.replace(tzinfo=UTC)
    if datetime.now(UTC) - retrieved > timedelta(hours=48):
        return unavailable(
            "ranking_stale", "Rankings are over 48 hours old; refresh rankings", "stale"
        )
    try:
        rows = json.loads(ranking.rows_json)
        metadata = json.loads(ranking.metadata_json)
    except json.JSONDecodeError:
        return unavailable("ranking_invalid", "Ranking data could not be read; sync rankings again")
    if not isinstance(rows, list) or not isinstance(metadata, dict):
        return unavailable("ranking_invalid", "Ranking data could not be read; sync rankings again")
    probability_ready = (
        metadata.get("brier_beats_baseline") is True and float(metadata.get("ece", 1.0)) <= 0.08
    )
    result = unavailable("player_unranked", "No ranking is available for this player")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("player_id"), int):
            continue
        player_id = int(row["player_id"])
        if next_pick is None:
            result[player_id] = {
                "status": "unavailable",
                "reason": "no_later_owner_pick",
                "label": "No later owner pick in this draft",
            }
            continue
        if probability_ready and isinstance(row.get("survival_probability"), (int, float)):
            probability = max(0.0, min(1.0, float(row["survival_probability"])))
            result[player_id] = {
                "status": "calibrated",
                "probability": round(probability, 4),
                "label": f"{probability:.0%} chance to survive to pick {next_pick}",
            }
            continue
        rank_value = row.get("adp", row.get("overall_rank"))
        if not isinstance(rank_value, (int, float)):
            result[player_id] = {
                "status": "unavailable",
                "reason": "player_unranked",
                "label": "No ranking is available for this player",
            }
            continue
        margin = float(rank_value) - next_pick
        if margin >= 8:
            label = f"Likely to survive to pick {next_pick}"
            band = "likely"
        elif margin <= -8:
            label = f"Unlikely to survive to pick {next_pick}"
            band = "unlikely"
        else:
            label = f"Volatile around pick {next_pick}"
            band = "volatile"
        result[player_id] = {
            "status": "ordinal",
            "band": band,
            "label": label,
            "rank_value": round(float(rank_value), 2),
        }
    return result
