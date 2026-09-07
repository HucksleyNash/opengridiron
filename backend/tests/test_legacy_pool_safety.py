from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from app.db import SessionLocal
from app.models import Game, Pool, PoolEntry, PoolPick
from app.services.decision import confidence_recommendations, survivor_recommendations

NOW = datetime(2026, 9, 4, tzinfo=UTC)


def pool(kind="survivor", **rules):
    return Pool(
        id=71,
        season=2026,
        name="Eligibility",
        pool_type=kind,
        rules_json=json.dumps({"future_value_weight": 0, **rules}),
    )


def game(identifier=1, **changes):
    values = dict(
        id=identifier,
        season=2026,
        week=2,
        home_team="CHI",
        away_team="GB",
        kickoff=NOW + timedelta(days=3),
        source_timestamp=NOW,
        source="fixture",
        win_probability_kind="manual",
        home_win_probability=0.7,
        cover_probability_kind="manual",
        home_cover_probability=0.6,
        spread_home=-3,
        completed=False,
    )
    values.update(changes)
    return Game(**values)


def entry():
    return PoolEntry(id=73, name="Mine", active=True, picks=[])


@pytest.mark.parametrize(
    "change",
    [
        {"win_probability_kind": "missing"},
        {"win_probability_kind": "legacy_unknown"},
        {"source_timestamp": None},
        {"source_timestamp": NOW - timedelta(hours=97)},
        {"locked_at": NOW},
        {"kickoff": NOW - timedelta(minutes=1)},
        {"completed": True},
        {"home_win_probability": float("nan")},
        {"home_win_probability": None},
    ],
)
def test_legacy_pool_endpoints_withhold_unsupported_stale_and_locked_games(change):
    unavailable = game(**change)
    assert survivor_recommendations(pool(), entry(), [unavailable], 2, now=NOW) == []
    assert confidence_recommendations(pool("confidence"), [unavailable], now=NOW) == []


def test_legacy_ats_requires_cover_source_and_known_line():
    for change in ({"cover_probability_kind": "missing"}, {"spread_home": None}):
        row = game(**change)
        assert (
            survivor_recommendations(pool(basis="against_spread"), entry(), [row], 2, now=NOW) == []
        )
        assert (
            confidence_recommendations(pool("confidence", basis="against_spread"), [row], now=NOW)
            == []
        )


def test_survivor_recomputes_final_loss_and_excludes_inactive_entry():
    current_entry = entry()
    past = game(9, week=1, completed=True, home_score=10, away_score=20)
    current_entry.picks = [PoolPick(week=1, slot=1, team="CHI", game_id=9, game=past, result="win")]
    assert survivor_recommendations(pool(), current_entry, [game()], 2, now=NOW) == []
    current_entry.picks = []
    current_entry.active = False
    assert survivor_recommendations(pool(), current_entry, [game()], 2, now=NOW) == []


def test_survivor_reuse_current_slots_and_week_lock_are_consistent():
    current_entry = entry()
    selected = game(8, away_team="DET", home_team="MIN")
    current_entry.picks = [PoolPick(week=2, slot=1, team="DET", game_id=8, game=selected)]
    choices = survivor_recommendations(
        pool(picks_per_week=2), current_entry, [selected, game()], 2, now=NOW
    )
    assert choices and all("DET" not in row.subject and "MIN" not in row.subject for row in choices)
    assert survivor_recommendations(pool(), current_entry, [selected, game()], 2, now=NOW) == []
    current_entry.picks = []
    selected.kickoff = NOW - timedelta(minutes=1)
    assert (
        survivor_recommendations(
            pool(lock_mode="week_start"), current_entry, [selected, game()], 2, now=NOW
        )
        == []
    )


def test_survivor_missing_future_probabilities_do_not_create_penalties():
    choices = survivor_recommendations(
        pool(future_value_weight=0.5),
        entry(),
        [game()],
        2,
        [game(9, week=3, win_probability_kind="missing")],
        now=NOW,
    )
    assert choices[0].expected_value == choices[0].confidence == 0.7


def test_confidence_respects_loser_direction_filters_and_full_card_weights():
    first, second = game(), game(2, away_team="MIN", home_team="DET", home_win_probability=0.8)
    choices = confidence_recommendations(
        pool("confidence", direction="loser", confidence_weights=[1, 5]), [first, second], now=NOW
    )
    assert [(r["pick"], r["confidence_weight"]) for r in choices] == [("MIN", 5), ("GB", 1)]
    filtered = confidence_recommendations(
        pool("confidence", allowed_teams=["chi", "min"]), [first, second], now=NOW
    )
    assert {r["pick"] for r in filtered} == {"CHI", "MIN"}
    second.win_probability_kind = "missing"
    assert confidence_recommendations(pool("confidence"), [first, second], now=NOW) == []
    assert (
        confidence_recommendations(pool("confidence", confidence_weights=[1, 1]), [first], now=NOW)
        == []
    )


def test_public_legacy_survivor_route_does_not_advise_eliminated_entries(client):
    with SessionLocal() as db:
        league_pool = Pool(
            name="Legacy elimination API", season=2089, pool_type="survivor", rules_json="{}"
        )
        db.add(league_pool)
        db.flush()
        player_entry = PoolEntry(pool_id=league_pool.id, name="Eliminated", active=True)
        db.add(player_entry)
        old = game(None, season=2089, week=1, completed=True, home_score=0, away_score=10)
        upcoming = game(
            None,
            season=2089,
            week=2,
            source_timestamp=datetime.now(UTC),
            kickoff=datetime.now(UTC) + timedelta(days=3),
        )
        db.add_all([old, upcoming])
        db.flush()
        db.add(PoolPick(entry_id=player_entry.id, game_id=old.id, week=1, slot=1, team="CHI"))
        db.commit()
        identifier = player_entry.id
    response = client.get(f"/api/v1/entries/{identifier}/survivor-recommendations?week=2")
    assert response.status_code == 200
    assert response.json() == []
