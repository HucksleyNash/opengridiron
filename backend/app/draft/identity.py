from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import League, Player
from .models import Athlete, AthleteAlias


def ensure_player_athlete(db: Session, player: Player, league: League) -> Athlete:
    """Give a league player a durable identity without unsafe name-only merging."""
    if player.athlete_id:
        athlete = db.get(Athlete, player.athlete_id)
        if athlete is not None:
            return athlete

    namespace = f"league:{league.id}"
    external_id = player.source_id or f"player:{player.id}"
    existing_alias = db.scalar(
        select(AthleteAlias).where(
            AthleteAlias.provider == "league_player",
            AthleteAlias.namespace == namespace,
            AthleteAlias.season_scope == str(league.season),
            AthleteAlias.external_id == external_id,
        )
    )
    if existing_alias is not None:
        player.athlete_id = existing_alias.athlete_id
        return existing_alias.athlete

    athlete = Athlete(display_name=player.name)
    db.add(athlete)
    db.flush()
    db.add(
        AthleteAlias(
            athlete_id=athlete.id,
            provider="league_player",
            namespace=namespace,
            season_scope=str(league.season),
            external_id=external_id,
            observed_name=player.name,
            observed_team=player.pro_team,
            observed_position=player.position,
            confidence=1.0,
            status="mapped",
        )
    )
    player.athlete_id = athlete.id
    return athlete
