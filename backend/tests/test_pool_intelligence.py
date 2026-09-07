from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from app.models import Base, Game, Pool, PoolEntry, PoolPick
from app.schemas import PoolRules, WeeklyCardUpdate
from app.services.nflverse import parse_schedule
from app.services.pool_outcomes import entry_outcome, pick_result, settle_season, standings
from app.services.pool_strategy import _allocate
from app.services.pool_week import get_pool_week, save_weekly_card
from app.services.team_strength import schedule_forecasts
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def game(game_id, week, home, p, now, away="ZZ"):
    return Game(
        id=game_id,
        season=2026,
        week=week,
        home_team=home,
        away_team=away,
        kickoff=now + timedelta(days=week * 7),
        home_win_probability=p,
        home_cover_probability=0.5,
        win_probability_kind="manual",
        cover_probability_kind="manual",
        source="manual",
        source_timestamp=now,
    )


def test_import_corrects_spread_and_settles_using_saved_handicap(db):
    pool = Pool(
        name="ATS",
        pool_type="survivor",
        season=2025,
        rules_json=PoolRules(basis="against_spread").model_dump_json(),
    )
    entry = PoolEntry(name="Main", pool=pool)
    db.add(pool)
    csv = (
        "season,week,game_type,game_id,gameday,gametime,away_team,home_team,spread_line,"
        "home_score,away_score\n2025,1,REG,2025_01_GB_CHI,2025-09-01,13:00,GB,CHI,3.5,17,14\n"
    )
    parse_schedule(db, csv, 2025)
    g = db.query(Game).one()
    assert g.spread_home == -3.5
    assert g.completed
    pick = PoolPick(entry=entry, game=g, week=1, slot=1, team="CHI", spread_home=-3.5)
    db.add(pick)
    db.flush()
    assert settle_season(db, 2025) == 1
    assert pick.result == "loss"
    parse_schedule(db, csv.replace(",3.5,17,14", ",2.5,17,14"), 2025)
    assert g.spread_home == -2.5
    assert pick.result == "loss"
    assert standings(db, pool)["entries"][0]["status"] == "eliminated"
    parse_schedule(db, csv.replace(",17,14", ",20,14"), 2025)
    assert pick.result == "win"  # A corrected final score regrades deterministically.


@pytest.mark.parametrize(
    ("direction", "tie", "expected"),
    [
        ("winner", "push", "push"),
        ("loser", "push", "push"),
        ("winner", "survive", "win"),
        ("loser", "eliminate", "loss"),
    ],
)
def test_tie_policy_and_legacy_missing_handicap(direction, tie, expected):
    g = Game(home_team="CHI", away_team="GB", home_score=17, away_score=14, completed=True)
    p = PoolPick(team="CHI", spread_home=-3)
    rules = PoolRules(direction=direction, basis="against_spread", tie_result=tie)
    assert pick_result(p, g, rules) == expected
    p.spread_home = None
    assert pick_result(p, g, rules) == "ungraded"


def test_season_allocator_preserves_strong_team_for_later_week():
    now = datetime(2026, 9, 1, tzinfo=UTC)
    games = [
        game(1, 17, "AA", 0.8, now),
        game(2, 17, "BB", 0.7, now, "YY"),
        game(3, 18, "AA", 0.95, now),
        game(4, 18, "CC", 0.5, now, "YY"),
    ]
    pool = Pool(
        id=1,
        pool_type="survivor",
        rules_json=PoolRules(allowed_teams=["AA", "BB", "CC"]).model_dump_json(),
    )
    entry = PoolEntry(id=1, name="Main", active=True, picks=[])
    result = _allocate(pool, entry, games, 17, Counter(), 0, now)
    assert result["status"] == "complete"
    assert [(p["week"], p["team"]) for p in result["picks"]] == [(17, "BB"), (18, "AA")]
    assert result["season_survival_probability"] == pytest.approx(0.665)
    entry.picks = [PoolPick(game_id=1, week=17, slot=1, team="AA", probability=0.8)]
    fixed = _allocate(pool, entry, games, 17, Counter(), 0, now)
    assert fixed["picks"][0]["saved"]
    assert [p["team"] for p in fixed["picks"]] == ["AA", "CC"]


def test_multiple_entries_diversify_and_stale_inputs_leave_gaps():
    now = datetime(2026, 9, 1, tzinfo=UTC)
    games = [game(1, 1, "AA", 0.7, now), game(2, 1, "BB", 0.69, now, "YY")]
    pool = Pool(
        id=1,
        pool_type="survivor",
        rules_json=PoolRules(allowed_teams=["AA", "BB"]).model_dump_json(),
    )
    entry = PoolEntry(id=1, name="Main", active=True, picks=[])
    exposure = Counter()
    first = _allocate(pool, entry, games, 1, exposure, 0.15, now)
    second = _allocate(pool, entry, games, 1, exposure, 0.15, now)
    assert first["picks"][0]["team"] != second["picks"][0]["team"]
    assert (
        _allocate(pool, entry, games, 1, Counter(), 0, now + timedelta(days=5))["status"]
        == "partial"
    )


def test_saved_pick_receipt_and_elimination_prevent_later_card_changes(db):
    now = datetime.now(UTC)
    pool = Pool(
        name="Outcomes", season=2026, pool_type="survivor", rules_json=PoolRules().model_dump_json()
    )
    entry = PoolEntry(name="Main", pool=pool)
    first, second = game(1, 1, "CHI", 0.7, now, "GB"), game(2, 2, "KC", 0.8, now, "DEN")
    db.add_all([pool, first, second])
    db.commit()
    saved = save_weekly_card(
        db,
        entry_id=entry.id,
        week=1,
        now=now,
        payload=WeeklyCardUpdate(version=0, picks=[{"slot": 1, "game_id": 1, "team": "CHI"}]),
    )
    assert saved["card"]["picks"][0]["probability"] == 0.7
    first.home_score, first.away_score, first.completed = 14, 21, True
    settle_season(db, 2026)
    db.commit()
    later = get_pool_week(db, pool_id=pool.id, entry_id=entry.id, week=2, now=now)
    assert later["entry"]["read_only"]
    from app.pool_errors import PoolDomainError

    with pytest.raises(PoolDomainError, match="eliminated"):
        save_weekly_card(
            db,
            entry_id=entry.id,
            week=2,
            now=now,
            payload=WeeklyCardUpdate(version=0, picks=[{"slot": 1, "game_id": 2, "team": "KC"}]),
        )


def test_team_model_selection_and_week_predictions_do_not_use_future_results():
    header = "season,week,game_type,away_team,home_team,home_score,away_score\n"
    history = "".join(
        f"{year},{week},REG,A{i},H{i},28,7\n"
        for year in range(2019, 2026)
        for week in range(1, 19)
        for i in range(8)
    )
    before, benchmark = schedule_forecasts(header + history + "2026,1,REG,A0,H0,,\n", 2026)
    changed, changed_benchmark = schedule_forecasts(
        header + history + "2026,1,REG,A0,H0,0,40\n", 2026
    )
    assert benchmark["status"] == "validated"
    assert benchmark["holdout_season"] == 2025
    assert benchmark == changed_benchmark
    assert before[(2026, 1, "A0", "H0")] == changed[(2026, 1, "A0", "H0")]
    assert schedule_forecasts(header + "2026,1,REG,A0,H0,,\n", 2026)[0] == {}


def test_incomplete_schedule_cannot_claim_complete_season_and_current_loss_stops_plan():
    now = datetime.now(UTC)
    g = game(1, 1, "CHI", 0.7, now, "GB")
    pool = Pool(id=1, season=2026, pool_type="survivor", rules_json=PoolRules().model_dump_json())
    entry = PoolEntry(id=1, name="Main", active=True, picks=[])
    result = _allocate(pool, entry, [g], 1, Counter(), 0, now)
    assert result["status"] == "partial"
    assert result["missing_weeks"] == list(range(2, 19))
    assert result["season_survival_probability"] is None
    entry.picks = [PoolPick(game_id=1, week=1, slot=1, team="CHI", probability=0.7)]
    g.home_score, g.away_score, g.completed = 7, 14, True
    assert _allocate(pool, entry, [g], 1, Counter(), 0, now)["status"] == "eliminated"


def test_moved_games_duplicate_picks_and_missing_weights_are_ungraded():
    now = datetime.now(UTC)
    g = game(1, 1, "CHI", 0.7, now, "GB")
    g.home_score, g.away_score, g.completed = 21, 14, True
    pool = Pool(id=1, season=2026, pool_type="confidence", rules_json=PoolRules().model_dump_json())
    entry = PoolEntry(
        id=1,
        name="Main",
        active=True,
        picks=[
            PoolPick(game_id=1, week=1, slot=1, team="CHI", confidence=1),
            PoolPick(game_id=1, week=2, slot=1, team="CHI", confidence=2),
        ],
    )
    outcome = entry_outcome(pool, entry, [g])
    assert (outcome["points"], outcome["wins"], outcome["ungraded"]) == (1, 1, 1)
    entry.picks[1].week = 1
    assert entry_outcome(pool, entry, [g])["ungraded"] == 2
    entry.picks = [PoolPick(game_id=1, week=1, slot=1, team="CHI", confidence=None)]
    assert entry_outcome(pool, entry, [g])["points"] == 0
    assert entry_outcome(pool, entry, [g])["ungraded"] == 1


def test_automated_settlement_preserves_unlinked_legacy_result(db):
    pool = Pool(
        name="Legacy", season=2026, pool_type="survivor", rules_json=PoolRules().model_dump_json()
    )
    entry = PoolEntry(name="Main", pool=pool)
    pick = PoolPick(entry=entry, week=1, slot=1, team="CHI", result="win")
    db.add(pool)
    db.commit()
    assert settle_season(db, 2026) == 0
    assert pick.result == "win"
    assert standings(db, pool)["entries"][0]["ungraded"] == 1
