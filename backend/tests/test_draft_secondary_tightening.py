from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

import pytest
from app.api import dashboard
from app.db import Base
from app.draft import recommendations
from app.draft.models import (
    Athlete,
    DraftEvent,
    DraftSession,
    ProjectionSnapshot,
    ProjectionSnapshotRow,
)
from app.draft.reducer import ReducedPick
from app.models import League, Player
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _draft(db, league_id, *, status="LIVE", kind="live", archived=False):
    draft = DraftSession(
        league_id=league_id,
        status=status,
        kind=kind,
        team_count=12,
        round_count=15,
        owner_team_slot=1,
        archived_at=datetime.now(UTC) if archived else None,
    )
    db.add(draft)
    db.flush()
    return draft


def test_dashboard_targets_active_live_draft_not_first_league_or_newest_mock(db):
    leagues = [League(name="My Pals", season=2026), League(name="Newbanantasy", season=2026)]
    db.add_all(leagues)
    db.flush()
    live = _draft(db, leagues[1].id)
    _draft(db, leagues[1].id, status="COMPLETE")
    _draft(db, leagues[0].id, kind="mock")
    _draft(db, leagues[0].id, archived=True)
    db.commit()

    result = dashboard(db)
    assert result["leagues"][0]["id"] == leagues[0].id
    assert result["active_draft"] == {
        "id": live.id,
        "league_id": leagues[1].id,
        "kind": "live",
        "status": "LIVE",
    }


def test_dashboard_active_draft_priority_and_empty_fallback(db):
    league = League(name="Priority league", season=2026)
    db.add(league)
    db.flush()
    live = _draft(db, league.id)
    paused = _draft(db, league.id, status="PAUSED")
    ready = _draft(db, league.id, status="READY")
    _draft(db, league.id, status="COMPLETE")
    _draft(db, league.id, status="ABANDONED")
    _draft(db, league.id, status="SETUP")
    db.commit()

    for selected in [live, paused, ready]:
        assert dashboard(db)["active_draft"]["id"] == selected.id
        selected.archived_at = datetime.now(UTC)
        db.commit()
    assert dashboard(db)["active_draft"] is None


@pytest.mark.parametrize(
    ("vor", "expected"),
    [
        (-54.6, "54.6 league-scored points below replacement"),
        (54.6, "54.6 league-scored points above replacement"),
        (0.0, "At league-scored replacement value"),
    ],
)
def test_vor_explanation_preserves_sign_and_tier_cliff(vor, expected):
    assert recommendations._vor_explanation(vor, False, 0) == expected
    assert (
        recommendations._vor_explanation(vor, True, 5) == expected + " with a 5.0-point tier cliff"
    )


@pytest.mark.parametrize(
    ("bye", "position", "same_bye", "same_position", "slots", "penalty"),
    [
        (None, "RB", 5, 3, ["RB"], 0.0),
        (11, "RB", 0, 0, ["RB"], 0.0),
        (11, "RB", 1, 0, ["RB"], 0.0),
        (11, "RB", 2, 0, ["RB"], 1.5),
        (11, "RB", 8, 0, ["RB"], 6.0),
        (11, "QB", 1, 1, ["QB", "BN"], 3.0),
        (11, "WR", 2, 2, ["WR", "WR", "W/R/T", "BN"], 4.5),
        (11, "QB", 1, 1, ["QB", "QB", "BN"], 0.0),
    ],
)
def test_bye_penalty_is_bounded_position_aware_and_unknown_safe(
    bye, position, same_bye, same_position, slots, penalty
):
    actual, warning = recommendations._bye_congestion(
        bye, position, slots, Counter({11: same_bye}), Counter({(position, 11): same_position})
    )
    assert actual == penalty
    assert bool(warning) == (penalty > 0)
    if warning:
        assert "Week 11" in warning
        assert f"-{penalty:.1f} decision points" in warning


def test_bye_penalty_does_not_override_an_open_starter_need():
    common = dict(tier_urgency=0, risk_adjustment=0, market_value=0, depth_need=0)
    starter = recommendations._weighted_score(normalized_vor=0, roster_need=1, **common)
    bench = recommendations._weighted_score(
        normalized_vor=1,
        roster_need=0,
        depth_need=0.35,
        tier_urgency=1,
        risk_adjustment=1,
        market_value=1,
    )
    assert starter - recommendations.MAX_BYE_CONGESTION_PENALTY > bench


def test_bye_congestion_changes_candidate_order_and_is_explained(db):
    league = League(name="Bye scoring", season=2097)
    db.add(league)
    db.flush()
    draft = _draft(db, league.id)
    draft.roster_slots_snapshot_json = '["QB","WR","RB","RB","BN","BN"]'
    snapshot = ProjectionSnapshot(
        league_id=league.id, source="test", content_hash="test", dataset_hash="test"
    )
    db.add(snapshot)
    db.flush()
    draft.projection_snapshot_id = snapshot.id
    specs = [
        ("Owner QB", "NE", "QB", 200),
        ("Owner WR", "NE", "WR", 200),
        ("Congested RB", "NE", "RB", 100),
        ("Spread RB", "LAR", "RB", 100),
        ("Unknown RB", "UNK", "RB", 100),
        ("Last RB", "CHI", "RB", 100),
    ]
    players = []
    for index, (name, team, position, points) in enumerate(specs):
        athlete = Athlete(display_name=name)
        db.add(athlete)
        db.flush()
        player = Player(
            league_id=league.id, athlete_id=athlete.id, name=name, pro_team=team, position=position
        )
        db.add(player)
        db.flush()
        db.add(
            ProjectionSnapshotRow(
                snapshot_id=snapshot.id,
                athlete_id=athlete.id,
                league_player_id=player.id,
                position=position,
                projected_points=points,
                floor=points,
                ceiling=points,
                risk=0.2,
                row_hash=str(index),
            )
        )
        players.append(player)
    db.commit()
    picks = [
        ReducedPick(
            event=DraftEvent(id=index + 1),
            overall_pick=index + 1,
            round=1,
            team_slot=1,
            player_id=player.id,
        )
        for index, player in enumerate(players[:2])
    ]

    top, rest, _ = recommendations.score_candidates(
        db, draft, picks, bye_weeks={"NE": 11, "LA": 8, "CHI": 9}
    )
    candidates = top + rest
    by_name = {candidate["name"]: candidate for candidate in candidates}
    congested, spread = by_name["Congested RB"], by_name["Spread RB"]
    assert spread["score"] - congested["score"] == pytest.approx(1.5)
    assert candidates.index(spread) < candidates.index(congested)
    assert congested["components"]["bye_congestion_penalty"] == 1.5
    assert "Week 11: 2 rostered players already share this bye" in congested["roster_impact"]
    assert spread["bye_week"] == 8
    assert by_name["Unknown RB"]["components"]["bye_congestion_penalty"] == 0
    assert "Owner QB" not in by_name

    # A scoring-input change at the same pick must not reuse an old snapshot hash.
    first = recommendations.recommendation_input_hash(draft, picks, {"NE": 11})
    second = recommendations.recommendation_input_hash(draft, picks, {"NE": 12})
    assert first != second
    assert recommendations.ALGORITHM_VERSION != "draft-score-v7"
