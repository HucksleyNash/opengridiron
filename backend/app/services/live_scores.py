"""Apply in-progress NFL scores without replacing the canonical schedule feed."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session

from ..models import DataSnapshot, Game

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
TEAM_ALIASES = {"JAC": "JAX", "LA": "LAR", "WSH": "WAS", "WFT": "WAS"}


def team_code(value: object) -> str:
    code = str(value or "").strip().upper()
    return TEAM_ALIASES.get(code, code)


def score(value: object) -> int | None:
    try:
        parsed = int(str(value))
        return parsed if parsed >= 0 else None
    except (TypeError, ValueError):
        return None


def apply_scoreboard(db: Session, payload: object, season: int, week: int) -> dict[str, int]:
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValueError("Live scoreboard returned an invalid response")

    games = db.query(Game).filter(Game.season == season, Game.week == week).all()
    by_matchup = {
        (team_code(game.away_team), team_code(game.home_team)): game for game in games
    }
    matched = updated = 0
    for event in payload["events"]:
        if not isinstance(event, dict):
            continue
        competitions = event.get("competitions")
        competition = competitions[0] if isinstance(competitions, list) and competitions else None
        competitors = competition.get("competitors") if isinstance(competition, dict) else None
        if not isinstance(competitors, list):
            continue
        sides = {
            competitor.get("homeAway"): competitor
            for competitor in competitors
            if isinstance(competitor, dict) and competitor.get("homeAway") in {"home", "away"}
        }
        home, away = sides.get("home"), sides.get("away")
        if not home or not away:
            continue
        status = event.get("status")
        status_type = status.get("type") if isinstance(status, dict) else None
        state = status_type.get("state") if isinstance(status_type, dict) else None
        if state not in {"in", "post"}:
            continue
        home_score, away_score = score(home.get("score")), score(away.get("score"))
        if home_score is None or away_score is None:
            continue
        away_team = away.get("team")
        home_team = home.get("team")
        if not isinstance(away_team, dict) or not isinstance(home_team, dict):
            continue
        matchup = (
            team_code(away_team.get("abbreviation")),
            team_code(home_team.get("abbreviation")),
        )
        game = by_matchup.get(matchup)
        if game is None:
            continue
        matched += 1
        completed = state == "post"
        if (
            game.away_score != away_score
            or game.home_score != home_score
            or game.completed != completed
        ):
            game.away_score = away_score
            game.home_score = home_score
            game.completed = completed
            updated += 1

    db.add(
        DataSnapshot(
            source="espn.scoreboard",
            source_id=f"{season}:{week}",
            retrieved_at=datetime.now(UTC),
            status="fresh",
            payload_json=json.dumps({"matched": matched, "updated": updated}),
        )
    )
    db.commit()
    return {"matched": matched, "updated": updated}


async def sync_live_scores(db: Session, season: int, week: int) -> dict[str, int | str]:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        response = await client.get(
            SCOREBOARD_URL,
            params={"dates": season, "seasontype": 2, "week": week},
            headers={"User-Agent": "OpenGridiron/0.1"},
        )
    response.raise_for_status()
    counts = apply_scoreboard(db, response.json(), season, week)
    return {**counts, "status": "completed"}
