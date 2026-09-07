from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Query

from ..config import settings

if settings.app_env != "test":
    raise RuntimeError("Draft fixture routes are available only when APP_ENV=test")


router = APIRouter(prefix="/api/v1/testing/draft-fixtures", tags=["testing"])


PLAYER_NAMES = [
    ("Amon-Ra St. Brown", "DET", "WR", 276.0, 0.12),
    ("Bijan Robinson", "ATL", "RB", 268.0, 0.10),
    ("Ja'Marr Chase", "CIN", "WR", 264.0, 0.14),
    ("Justin Jefferson", "MIN", "WR", 258.0, 0.11),
    ("Jahmyr Gibbs", "DET", "RB", 251.0, 0.16),
    ("CeeDee Lamb", "DAL", "WR", 246.0, 0.15),
    ("Saquon Barkley", "PHI", "RB", 241.0, 0.18),
    ("Puka Nacua", "LAR", "WR", 236.0, 0.17),
    ("Malik Nabers", "NYG", "WR", 231.0, 0.19),
    ("Breece Hall", "NYJ", "RB", 226.0, 0.21),
    ("A.J. Brown", "PHI", "WR", 221.0, 0.16),
    ("Jonathan Taylor", "IND", "RB", 217.0, 0.22),
    ("Josh Allen", "BUF", "QB", 403.0, 0.09),
    ("Lamar Jackson", "BAL", "QB", 394.0, 0.12),
    ("Trey McBride", "ARI", "TE", 213.0, 0.13),
    ("Brock Bowers", "LV", "TE", 207.0, 0.14),
    ("Nico Collins", "HOU", "WR", 205.0, 0.18),
    ("De'Von Achane", "MIA", "RB", 202.0, 0.25),
    ("Drake London", "ATL", "WR", 199.0, 0.17),
    ("Josh Jacobs", "GB", "RB", 196.0, 0.20),
    ("Jalen Hurts", "PHI", "QB", 382.0, 0.14),
    ("George Kittle", "SF", "TE", 188.0, 0.24),
    ("Mike Evans", "TB", "WR", 186.0, 0.23),
    ("James Cook", "BUF", "RB", 184.0, 0.19),
    ("Davante Adams", "LAR", "WR", 181.0, 0.27),
    ("Kenneth Walker III", "SEA", "RB", 178.0, 0.24),
    ("Tee Higgins", "CIN", "WR", 176.0, 0.25),
    ("Kyren Williams", "LAR", "RB", 173.0, 0.24),
    ("Patrick Mahomes", "KC", "QB", 367.0, 0.10),
    ("Sam LaPorta", "DET", "TE", 169.0, 0.18),
    ("DK Metcalf", "PIT", "WR", 166.0, 0.22),
    ("Alvin Kamara", "NO", "RB", 163.0, 0.28),
]


def _snake_team_slot(overall_pick: int, team_count: int) -> int:
    round_number = (overall_pick - 1) // team_count + 1
    within_round = (overall_pick - 1) % team_count
    return within_round + 1 if round_number % 2 else team_count - within_round


def build_draft_fixture(
    *,
    now: datetime,
    team_count: int = 8,
    round_count: int = 4,
    owner_team_slot: int = 3,
    source_state: Literal["healthy", "stale", "outage"] = "healthy",
) -> dict[str, object]:
    players = [
        {
            "id": index,
            "athlete_id": index,
            "name": name,
            "pro_team": pro_team,
            "position": position,
            "projected_points": projected_points,
            "floor": round(projected_points * 0.82, 1),
            "ceiling": round(projected_points * 1.18, 1),
            "ros_value": round(projected_points / 10, 1),
            "risk": risk,
        }
        for index, (name, pro_team, position, projected_points, risk) in enumerate(
            PLAYER_NAMES, start=1
        )
    ]
    teams = [
        {
            "slot": slot,
            "name": "Open Gridiron" if slot == owner_team_slot else f"Fixture Team {slot}",
            "is_owner": slot == owner_team_slot,
        }
        for slot in range(1, team_count + 1)
    ]
    source_age = 12 if source_state == "healthy" else 480
    first_picks = [
        {
            "overall_pick": overall,
            "round": 1,
            "team_slot": _snake_team_slot(overall, team_count),
            "player_id": overall,
            "source": "manual",
        }
        for overall in range(1, owner_team_slot)
    ]
    yahoo_observations = [
        {
            "provider_key": f"fixture-{pick['overall_pick']}",
            "overall_pick": pick["overall_pick"],
            "player_id": pick["player_id"],
            "team_slot": pick["team_slot"],
            "result": "confirmed",
        }
        for pick in first_picks
    ]
    yahoo_observations.append(
        {
            "provider_key": "fixture-proposal-3",
            "overall_pick": owner_team_slot,
            "player_id": owner_team_slot + 1,
            "team_slot": owner_team_slot,
            "result": "proposal",
        }
    )
    return {
        "fixture_version": 1,
        "clock": {"now": now.isoformat(), "pick_seconds_remaining": 72},
        "source": {
            "state": source_state,
            "mode": "yahoo_scrape_shadow",
            "last_success_at": (now - timedelta(seconds=source_age)).isoformat(),
            "error": "Yahoo fixture outage" if source_state == "outage" else None,
        },
        "league": {
            "id": 9001,
            "name": "Deterministic Draft Lab",
            "season": now.year,
            "source": "fixture",
            "scoring": {"receptions": 1.0},
            "roster_slots": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BENCH"],
        },
        "session": {
            "id": 7001,
            "kind": "mock",
            "format": "snake",
            "status": "LIVE",
            "team_count": team_count,
            "round_count": round_count,
            "owner_team_slot": owner_team_slot,
            "current_sequence": len(first_picks) + 1,
            "source_mode": "yahoo_scrape_shadow",
        },
        "teams": teams,
        "players": players,
        "events": first_picks,
        "yahoo_observations": yahoo_observations,
        "error_controls": [
            "stale_sequence",
            "advice_pending",
            "source_outage",
            "unknown_player",
        ],
    }


@router.get("/{name}")
def draft_fixture(
    name: Literal["small", "standard"] = "small",
    source_state: Literal["healthy", "stale", "outage"] = Query(default="healthy"),
    now: datetime = Query(default=datetime(2026, 8, 30, 20, 0, tzinfo=UTC)),
) -> dict[str, object]:
    if name == "standard":
        return build_draft_fixture(
            now=now,
            team_count=12,
            round_count=16,
            owner_team_slot=7,
            source_state=source_state,
        )
    return build_draft_fixture(now=now, source_state=source_state)
