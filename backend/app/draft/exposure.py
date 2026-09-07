from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import League, Player
from .models import DraftSession, ProjectionSnapshotRow


def exposure_payload(db: Session, session: DraftSession) -> dict[str, object]:
    total_leagues = db.scalar(select(func.count(League.id))) or 0
    snapshot_rows = (
        list(
            db.execute(
                select(
                    ProjectionSnapshotRow.league_player_id,
                    ProjectionSnapshotRow.athlete_id,
                    Player.name,
                )
                .join(Player, Player.id == ProjectionSnapshotRow.league_player_id)
                .where(ProjectionSnapshotRow.snapshot_id == session.projection_snapshot_id)
            )
        )
        if session.projection_snapshot_id
        else []
    )

    athlete_ids = {athlete_id for _, athlete_id, _ in snapshot_rows}
    rostered_by_athlete: dict[int, list[dict[str, object]]] = defaultdict(list)
    if athlete_ids:
        rostered_rows = db.execute(
            select(
                Player.athlete_id,
                League.id,
                League.name,
                Player.id,
                Player.source_id,
                Player.rostered_by,
            )
            .join(League, League.id == Player.league_id)
            .where(
                Player.athlete_id.in_(athlete_ids),
                Player.league_id != session.league_id,
                Player.rostered_by.is_not(None),
            )
            .order_by(Player.athlete_id, League.name, Player.id)
        )
        for athlete_id, league_id, league_name, player_id, source_id, rostered_by in rostered_rows:
            if athlete_id is None:
                continue
            rostered_by_athlete[athlete_id].append(
                {
                    "id": league_id,
                    "name": league_name,
                    "league_player_id": player_id,
                    "source_id": source_id,
                    "rostered_by": rostered_by,
                }
            )

    result = []
    for player_id, athlete_id, player_name in snapshot_rows:
        rostered = rostered_by_athlete[athlete_id]
        result.append(
            {
                "player_id": player_id,
                "athlete_id": athlete_id,
                "name": player_name,
                "league_count": len(rostered),
                "share": round(len(rostered) / max(1, total_leagues - 1), 4),
                "leagues": rostered,
                "informational_only": True,
            }
        )
    return {
        "session_id": session.id,
        "total_other_leagues": max(0, total_leagues - 1),
        "strategy_enabled": False,
        "players": result,
    }
