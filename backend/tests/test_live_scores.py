from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.db import SessionLocal
from app.models import DataSnapshot, Game
from app.services.live_scores import apply_scoreboard


def event(away: str, home: str, away_score: str, home_score: str, state: str) -> dict:
    return {
        "status": {"type": {"state": state}},
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "score": home_score, "team": {"abbreviation": home}},
                    {"homeAway": "away", "score": away_score, "team": {"abbreviation": away}},
                ]
            }
        ],
    }


@pytest.mark.parametrize(
    ("state", "completed", "season"), [("in", False, 2084), ("post", True, 2085)]
)
def test_live_scoreboard_updates_matching_games_and_completion(
    client, state, completed, season
):
    with SessionLocal() as db:
        game = Game(
            season=season,
            week=2,
            away_team="WAS",
            home_team="LA",
            kickoff=datetime(2084, 9, 17, tzinfo=UTC),
            source="nflverse",
        )
        db.add(game)
        db.commit()

        result = apply_scoreboard(
            db, {"events": [event("WSH", "LAR", "17", "20", state)]}, season, 2
        )

        db.refresh(game)
        assert result == {"matched": 1, "updated": 1}
        assert (game.away_score, game.home_score, game.completed) == (17, 20, completed)
        snapshot = (
            db.query(DataSnapshot)
            .filter_by(source="espn.scoreboard", source_id=f"{season}:2")
            .one()
        )
        assert snapshot.status == "fresh"


def test_live_scoreboard_ignores_pregame_invalid_and_unmatched_events(client):
    with SessionLocal() as db:
        game = Game(
            season=2083,
            week=3,
            away_team="GB",
            home_team="CHI",
            kickoff=datetime(2083, 9, 24, tzinfo=UTC),
            source="nflverse",
        )
        db.add(game)
        db.commit()

        payload = {
            "events": [
                event("GB", "CHI", "0", "0", "pre"),
                event("MIN", "DET", "7", "3", "in"),
                event("GB", "CHI", "bad", "3", "in"),
                {"status": "malformed", "competitions": [{"competitors": []}]},
            ]
        }
        assert apply_scoreboard(db, payload, 2083, 3) == {"matched": 0, "updated": 0}
        db.refresh(game)
        assert game.away_score is None and game.home_score is None and not game.completed


def test_live_scoreboard_rejects_invalid_payload(client):
    with SessionLocal() as db, pytest.raises(ValueError, match="invalid response"):
        apply_scoreboard(db, {"events": {}}, 2082, 1)
