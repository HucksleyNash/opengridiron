from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

from app.db import SessionLocal
from app.models import Game, Pool, PoolEntry, PoolEntryWeek, PoolPick
from app.pool_errors import PoolDomainError
from app.schemas import WeeklyCardUpdate
from app.services.pool_week import save_weekly_card
from fastapi.testclient import TestClient


def test_two_sqlite_sessions_cannot_overwrite_the_same_card(client: TestClient) -> None:
    with SessionLocal() as db:
        game = Game(
            season=2094,
            week=1,
            away_team="GB",
            home_team="CHI",
            kickoff=datetime(2094, 9, 8, 17, tzinfo=UTC),
            home_win_probability=0.6,
            home_cover_probability=0.52,
            source="test",
            win_probability_kind="model",
            cover_probability_kind="model",
        )
        pool = Pool(
            name="Concurrency survivor",
            pool_type="survivor",
            season=2094,
            rules_json=(
                '{"direction":"winner","basis":"straight_up","picks_per_week":1,'
                '"max_team_uses":1,"allowed_teams":[],"blocked_teams":[],'
                '"tie_result":"push","lock_mode":"game_start",'
                '"confidence_weights":[],"future_value_weight":0.1}'
            ),
        )
        db.add_all([game, pool])
        db.flush()
        entry = PoolEntry(pool_id=pool.id, name="Main")
        db.add(entry)
        db.commit()
        game_id = game.id
        entry_id = entry.id

    barrier = Barrier(2)

    def write(team: str) -> str:
        with SessionLocal() as db:
            barrier.wait(timeout=5)
            try:
                save_weekly_card(
                    db,
                    entry_id=entry_id,
                    week=1,
                    payload=WeeklyCardUpdate.model_validate(
                        {
                            "version": 0,
                            "picks": [
                                {
                                    "slot": 1,
                                    "game_id": game_id,
                                    "team": team,
                                    "confidence": None,
                                }
                            ],
                        }
                    ),
                    now=datetime(2094, 9, 1, tzinfo=UTC),
                )
                return "saved"
            except PoolDomainError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, ["GB", "CHI"]))

    assert sorted(results) == ["card_conflict", "saved"]
    with SessionLocal() as db:
        card = (
            db.query(PoolEntryWeek)
            .filter(PoolEntryWeek.entry_id == entry_id, PoolEntryWeek.week == 1)
            .one()
        )
        picks = db.query(PoolPick).filter(PoolPick.entry_id == entry_id, PoolPick.week == 1).all()
        assert card.version == 1
        assert len(picks) == 1
        assert picks[0].team in {"GB", "CHI"}
