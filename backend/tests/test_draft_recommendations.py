from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

from app.draft import recommendations


def _projection_row(row_id: int, position: str, points: float) -> SimpleNamespace:
    return SimpleNamespace(
        id=row_id,
        athlete_id=row_id,
        position=position,
        projected_points=points,
    )


def _lineup_player(position: str, points: float) -> object:
    return recommendations.LineupPlayerProjection(
        positions=frozenset({position}),
        projected_points=points,
    )


def test_replacement_baselines_count_yahoo_wrt_flex_slots() -> None:
    rows = [
        _projection_row(1, "RB", 20),
        _projection_row(2, "RB", 16),
        _projection_row(3, "RB", 12),
        _projection_row(4, "RB", 7),
        _projection_row(5, "WR", 18),
        _projection_row(6, "WR", 15),
        _projection_row(7, "WR", 10),
        _projection_row(8, "WR", 6),
    ]

    baselines = recommendations._replacement_baselines(  # type: ignore[arg-type]
        rows,
        ["RB", "WR", "W/R/T"],
        team_count=2,
    )

    assert baselines["RB"] == 7
    assert baselines["WR"] == 6


def test_third_te_gets_no_roster_need_when_it_cannot_improve_the_lineup() -> None:
    context = recommendations._build_lineup_context(
        [
            _lineup_player("QB", 200),
            _lineup_player("RB", 180),
            _lineup_player("RB", 170),
            _lineup_player("WR", 190),
            _lineup_player("WR", 175),
            _lineup_player("WR", 160),
            _lineup_player("TE", 150),
            _lineup_player("TE", 120),
            _lineup_player("K", 100),
            _lineup_player("DEF", 100),
        ],
        ["QB", "RB", "RB", "WR", "WR", "TE", "W/R/T", "K", "DEF", "BN"],
    )

    utility = recommendations._marginal_lineup_utility(_lineup_player("TE", 110), context)

    assert utility.roster_need == 0.0
    assert utility.projected_delta == 0.0
    assert utility.impact == "Bench-only at current projections"


def test_lineup_upgrade_gets_partial_need_without_an_empty_starter() -> None:
    context = recommendations._build_lineup_context(
        [
            _lineup_player("RB", 180),
            _lineup_player("WR", 190),
            _lineup_player("WR", 160),
            _lineup_player("TE", 150),
        ],
        ["RB", "WR", "TE", "W/R/T", "BN"],
    )

    utility = recommendations._marginal_lineup_utility(_lineup_player("TE", 170), context)

    assert utility.roster_need == 0.75
    assert utility.projected_delta == 20.0
    assert utility.impact == "Improves the projected starting lineup by 20.0 points"


def test_tiny_lineup_upgrade_gets_proportional_need() -> None:
    context = recommendations._build_lineup_context(
        [_lineup_player("K", 100)],
        ["K", "BN"],
    )

    utility = recommendations._marginal_lineup_utility(_lineup_player("K", 100.02), context)

    assert utility.roster_need < 0.01
    assert utility.projected_delta == 0.02


def test_candidate_that_only_fills_yahoo_flex_gets_partial_roster_need() -> None:
    context = recommendations._build_lineup_context(
        [
            _lineup_player("RB", 180),
            _lineup_player("WR", 190),
            _lineup_player("TE", 150),
        ],
        ["RB", "WR", "TE", "W/R/T", "BN"],
    )

    utility = recommendations._marginal_lineup_utility(_lineup_player("WR", 160), context)

    assert utility.roster_need == 0.75
    assert utility.projected_delta == 160.0
    assert utility.impact == "Fills an open W/R/T starter"


def test_empty_required_starter_gets_full_roster_need() -> None:
    context = recommendations._build_lineup_context(
        [_lineup_player("RB", 180), _lineup_player("WR", 190)],
        ["RB", "WR", "TE", "W/R/T", "BN"],
    )

    utility = recommendations._marginal_lineup_utility(_lineup_player("TE", 150), context)

    assert utility.roster_need == 1.0
    assert utility.projected_delta == 150.0
    assert utility.impact == "Fills an open TE starter"


def test_position_depth_policy_allows_one_te_backup_but_not_a_third_te() -> None:
    roster_slots = ["QB", "RB", "RB", "WR", "WR", "TE", "W/R/T", "K", "DEF", "BN"]

    assert recommendations._bench_depth_need("TE", Counter({"TE": 1}), roster_slots) > 0
    assert not recommendations._position_is_saturated("TE", Counter({"TE": 1}), roster_slots)
    assert recommendations._position_is_saturated("TE", Counter({"TE": 2}), roster_slots)


def test_position_depth_policy_rejects_backup_kickers() -> None:
    roster_slots = ["QB", "RB", "RB", "WR", "WR", "TE", "W/R/T", "K", "DEF", "BN"]

    assert recommendations._bench_depth_need("K", Counter({"K": 1}), roster_slots) == 0
    assert recommendations._position_is_saturated("K", Counter({"K": 1}), roster_slots)


def test_position_depth_policy_values_rb_and_wr_reserves() -> None:
    roster_slots = ["QB", "RB", "RB", "WR", "WR", "TE", "W/R/T", "K", "DEF", "BN"]

    assert recommendations._bench_depth_need("RB", Counter({"RB": 3}), roster_slots) > 0
    assert recommendations._bench_depth_need("WR", Counter({"WR": 3}), roster_slots) > 0
    assert not recommendations._position_is_saturated("RB", Counter({"RB": 3}), roster_slots)
    assert recommendations._position_is_saturated("RB", Counter({"RB": 5}), roster_slots)


def test_open_starter_need_outweighs_maximum_zero_utility_score() -> None:
    open_starter_score = recommendations._weighted_score(
        normalized_vor=0.0,
        roster_need=1.0,
        depth_need=0.0,
        tier_urgency=0.0,
        risk_adjustment=0.0,
        market_value=0.0,
    )
    max_zero_utility_score = recommendations._weighted_score(
        normalized_vor=1.0,
        roster_need=0.0,
        depth_need=0.0,
        tier_urgency=1.0,
        risk_adjustment=1.0,
        market_value=1.0,
    )

    assert open_starter_score > max_zero_utility_score


def test_vor_normalization_uses_only_the_top_candidate_window() -> None:
    values = [10.0, 0.0, *[-float(value) for value in range(1, 59)], -1000.0]

    normalized = recommendations._normalize(values, window_size=60)

    assert normalized[1] < 0.9
    assert normalized[-1] == 0.0
