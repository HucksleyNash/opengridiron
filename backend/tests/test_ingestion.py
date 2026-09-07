from __future__ import annotations

import json
from pathlib import Path

from app.db import SessionLocal
from app.models import Game, IdentityMap, League, Player
from app.services.news import _classify
from app.services.nflverse import parse_rosters, parse_schedule
from app.services.yahoo import _apply_settings, _upsert_players
from fastapi.testclient import TestClient


def test_news_classification() -> None:
    assert _classify("Starter ruled out", "Knee injury") == ("injury", "urgent")
    assert _classify("Team signs veteran", "Transaction announced")[0] == "transaction"


def test_nflverse_schedule_and_roster_parsing(client: TestClient) -> None:
    db = SessionLocal()
    try:
        schedule = "\n".join(
            [
                "season,game_type,week,gameday,gametime,away_team,home_team,away_moneyline,home_moneyline,away_spread_odds,home_spread_odds,spread_line,total_line",
                "2026,REG,1,2026-09-10,19:20,GB,CHI,120,-140,-110,-110,-2.5,44.5",
                "2025,REG,1,2025-09-10,19:20,GB,CHI,120,-140,-110,-110,-2.5,44.5",
            ]
        )
        result = parse_schedule(db, schedule, 2026)
        db.commit()
        assert result == {"created": 1, "updated": 0}
        stored = db.query(Game).filter(Game.season == 2026).one()
        assert stored.home_win_probability > 0.5
        assert stored.total == 44.5
        assert stored.win_probability_kind == "market"

        roster = "gsis_id,full_name,team,position\n00-001,Test Runner,CHI,RB\n"
        assert parse_rosters(db, roster) == {"created": 1, "updated": 0}
        db.commit()
        identity = db.query(IdentityMap).filter(IdentityMap.gsis_id == "00-001").one()
        assert identity.canonical_name == "Test Runner"
    finally:
        db.close()


def test_nflverse_real_header_postseason_and_stable_week_move(client: TestClient) -> None:
    fixture_dir = Path(__file__).parent / "fixtures"
    first = (fixture_dir / "nflverse_schedule_first.csv").read_text(encoding="utf-8")
    second = (fixture_dir / "nflverse_schedule_second.csv").read_text(encoding="utf-8")
    db = SessionLocal()
    try:
        assert parse_schedule(db, first, 2095) == {"created": 5, "updated": 0}
        db.commit()
        games = db.query(Game).filter(Game.season == 2095).all()
        assert {game.week for game in games} == {1, 18, 19, 20, 21}
        moved = next(game for game in games if game.source_game_key == "gsis-2095-reg")
        moved_id = moved.id
        assert moved.source_game_key_kind == "gsis"
        assert moved.win_probability_kind == "unavailable"

        assert parse_schedule(db, second, 2095) == {"created": 0, "updated": 5}
        db.commit()
        refreshed = db.get(Game, moved_id)
        assert refreshed is not None
        assert refreshed.week == 2
        assert refreshed.kickoff.isoformat().startswith("2095-09-15")
    finally:
        db.close()


def test_yahoo_fixture_parsing(client: TestClient) -> None:
    db = SessionLocal()
    try:
        league = League(name="Yahoo Fixture", season=2026, source="yahoo", yahoo_key="449.l.1")
        db.add(league)
        db.commit()
        settings = {
            "settings": {
                "stat_modifiers": [{"stat_id": "4", "display_name": "Passing TD", "value": "6"}],
                "roster_positions": [
                    {"roster_position": {"position": "QB", "count": "1"}},
                    {"roster_position": {"position": "WR", "count": "2"}},
                ],
            }
        }
        _apply_settings(league, settings)
        assert json.loads(league.scoring_json)["Passing TD"] == 6
        assert json.loads(league.roster_slots_json) == ["QB", "WR", "WR"]

        payload = {
            "players": [
                {
                    "player_key": "449.p.1",
                    "name": {"full": "Test Quarterback"},
                    "editorial_team_abbr": "chi",
                    "display_position": "QB",
                }
            ]
        }
        assert _upsert_players(db, league, payload, "My Team") == 1
        db.commit()
        stored = db.query(Player).filter(Player.source_id == "449.p.1").one()
        assert stored.rostered_by == "My Team"
        assert stored.pro_team == "CHI"
    finally:
        db.close()
