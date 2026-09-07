from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.models import Game, Pool, PoolEntry, PoolPick
from app.services.pool_week import evaluate_weekly_card, infer_suggested_week


def _pool(*, lock_mode: str = "game_start", picks_per_week: int = 1) -> Pool:
    return Pool(
        id=1,
        name="Evaluator",
        pool_type="survivor",
        season=2093,
        rules_json=json.dumps(
            {
                "direction": "winner",
                "basis": "straight_up",
                "picks_per_week": picks_per_week,
                "max_team_uses": 1,
                "allowed_teams": [],
                "blocked_teams": [],
                "tie_result": "push",
                "lock_mode": lock_mode,
                "confidence_weights": [],
                "future_value_weight": 0.1,
            }
        ),
    )


def _game(game_id: int, week: int, kickoff: datetime, away: str, home: str) -> Game:
    return Game(
        id=game_id,
        season=2093,
        week=week,
        away_team=away,
        home_team=home,
        kickoff=kickoff,
        home_win_probability=0.65,
        home_cover_probability=0.52,
        source="test",
        win_probability_kind="model",
        cover_probability_kind="model",
    )


def _evaluate(
    pool: Pool,
    games: list[Game],
    picks: list[PoolPick],
    now: datetime,
) -> dict[str, object]:
    entry = PoolEntry(id=1, pool_id=1, name="Main", active=True)
    return evaluate_weekly_card(
        pool=pool,
        entry=entry,
        week=1,
        games=games,
        season_games=games,
        picks=picks,
        version=0,
        schedule={"state": "ready", "source": "test", "last_success_at": now},
        now=now,
    )


def test_game_lock_boundary_is_server_authoritative() -> None:
    start = datetime(2093, 9, 8, 17, tzinfo=UTC)
    game = _game(1, 1, start, "GB", "CHI")
    pick = PoolPick(id=1, entry_id=1, game_id=1, week=1, slot=1, team="CHI")
    pick.game = game

    before = _evaluate(_pool(), [game], [pick], start - timedelta(microseconds=1))
    at_start = _evaluate(_pool(), [game], [pick], start)

    assert before["card"]["state"] == "complete"  # type: ignore[index]
    assert at_start["card"]["state"] == "locked_complete"  # type: ignore[index]
    assert at_start["card"]["picks"][0]["locked"] is True  # type: ignore[index]


def test_week_start_lock_marks_an_incomplete_multi_pick_card_locked() -> None:
    now = datetime(2093, 9, 8, 18, tzinfo=UTC)
    first = _game(1, 1, now - timedelta(hours=1), "GB", "CHI")
    second = _game(2, 1, now + timedelta(hours=4), "KC", "DEN")
    pick = PoolPick(id=1, entry_id=1, game_id=2, week=1, slot=1, team="KC")
    pick.game = second

    result = _evaluate(
        _pool(lock_mode="week_start", picks_per_week=2), [first, second], [pick], now
    )
    assert result["card"]["state"] == "locked_incomplete"  # type: ignore[index]
    assert result["card"]["missing_count"] == 1  # type: ignore[index]


def test_suggested_week_uses_next_kickoff_and_last_week_after_season() -> None:
    now = datetime(2093, 9, 8, 12, tzinfo=UTC)
    games = [
        _game(1, 1, now + timedelta(days=2), "GB", "CHI"),
        _game(2, 2, now + timedelta(days=1), "KC", "DEN"),
    ]
    assert infer_suggested_week(games, now) == 2
    assert infer_suggested_week(games, now + timedelta(days=3)) == 2
