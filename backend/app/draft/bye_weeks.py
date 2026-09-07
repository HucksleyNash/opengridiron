from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Game

REGULAR_SEASON_WEEKS = frozenset(range(1, 19))
SCHEDULE_TEAM_ALIASES = {"LAR": "LA"}


def team_bye_weeks(db: Session, season: int) -> dict[str, int]:
    weeks_by_team: dict[str, set[int]] = {}
    schedule_rows = db.execute(
        select(Game.week, Game.away_team, Game.home_team).where(
            Game.season == season,
            Game.week >= 1,
            Game.week <= 18,
        )
    )
    for week, away_team, home_team in schedule_rows:
        for team in (away_team, home_team):
            weeks_by_team.setdefault(team.upper(), set()).add(week)

    bye_weeks: dict[str, int] = {}
    for team, scheduled_weeks in weeks_by_team.items():
        missing_weeks = REGULAR_SEASON_WEEKS - scheduled_weeks
        if len(scheduled_weeks) == 17 and len(missing_weeks) == 1:
            bye_weeks[team] = next(iter(missing_weeks))
    return bye_weeks


def player_bye_week(pro_team: str | None, bye_weeks: dict[str, int]) -> int | None:
    if not pro_team:
        return None
    team = pro_team.upper()
    return bye_weeks.get(SCHEDULE_TEAM_ALIASES.get(team, team))
