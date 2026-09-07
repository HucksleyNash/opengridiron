from __future__ import annotations

import json
from copy import deepcopy

import pytest
from app.models import AnalysisRun
from app.services import providers


def large_draft(completed_picks: int = 90) -> dict:
    """Full player coverage plus the rich profiles and history of a long draft."""
    players = [
        {
            "id": player_id,
            "name": f"Player {player_id}",
            "position": ["QB", "RB", "WR", "TE", "K", "DEF"][player_id % 6],
            "status": "Questionable" if player_id % 4 == 0 else "Active",
            "projection": 350 - player_id / 10,
            "available": player_id > completed_picks,
            "range_validation": {
                "confidence": "medium",
                "risk_factors": [
                    "Weekly workload varies with game script; the estimate uses historical usage. "
                    * 30,
                ],
            },
            "injury_context": {"status": "designation_only_no_attributed_details", "evidence": []},
        }
        for player_id in range(1, 322)
    ]
    draft = {
        "session_id": 3,
        "status": "COMPLETE" if completed_picks == 180 else "LIVE",
        "player_pool": players,
        "player_index": {
            "format": "columnar",
            "columns": ["player_id", "name", "position", "projection", "available"],
            "rows": [
                [
                    player_id,
                    f"Player {player_id}",
                    "QB",
                    350 - player_id / 10,
                    player_id > completed_picks,
                ]
                for player_id in range(1, 1201)
            ],
        },
        "recommendations": [{"player_id": 319, "name": "Player 319", "score": 90}],
        "board": {
            "completed_picks": completed_picks,
            "team_rosters": [
                {"is_owner": True, "roster": [{"player_id": 80, "name": "Player 80"}]},
                {"is_owner": False, "roster": [{"player_id": 81, "name": "Player 81"}]},
            ],
        },
        "history": {
            "events": [
                {"id": number, "type": "pick_recorded", "player_id": number}
                for number in range(1, completed_picks + 1)
            ],
            "owner_decisions": [
                {
                    "at_time": True,
                    "recommendation_snapshot": {
                        "chosen_candidate": {
                            "player_id": number,
                            "why_now": "Value above replacement",
                        },
                        "leading_candidates": [{"player_id": number + 1, "score": 88}],
                    },
                }
                for number in range(6, completed_picks, 12)
            ],
            "latest_recommendation_snapshot": {
                "leading_candidates": [{"player_id": 318}],
                "additional_position_leaders": [{"player_id": 317}],
            },
        },
        "preferences": [{"player_id": 320, "queue_rank": 1}, {"player_id": 321, "target": True}],
        "projection_anomalies": [{"player_id": 300, "code": "projection_market_mismatch"}],
        "data_coverage": {
            "projection_rows": {"stored": 1200, "compact_indexed": 1200, "detailed": len(players)}
        },
    }
    return {
        "league": {"id": 2, "draft_context": draft},
        "data_access": {"scope": {"league_id": 2, "draft_session_id": 3}},
        "news": [{"title": "Latest injury evidence", "url": "https://example.com/injury"}],
    }


@pytest.mark.parametrize("completed_picks", [90, 180])
def test_large_draft_fits_with_complete_index_and_unchanged_decision_evidence(completed_picks):
    dossier = large_draft(completed_picks)
    original = deepcopy(dossier)
    assert len(json.dumps(dossier).encode()) > providers.MAX_DOSSIER_BYTES

    frozen, encoded = providers._frozen_dossier(dossier)

    assert len(encoded.encode()) <= providers.MAX_DOSSIER_BYTES
    assert json.loads(encoded) == frozen
    assert dossier == original
    source = original["league"]["draft_context"]
    draft = frozen["league"]["draft_context"]
    for key in (
        "player_index",
        "board",
        "history",
        "recommendations",
        "preferences",
        "projection_anomalies",
    ):
        assert draft[key] == source[key]
    assert frozen["news"] == original["news"]
    retained = {row["id"]: row for row in draft["player_pool"]}
    assert {80, 317, 318, 319, 320, 321} <= retained.keys()
    assert len(retained) < len(source["player_pool"])
    for row in source["player_pool"]:
        if row["id"] in retained:
            assert retained[row["id"]] == row
    assert draft["data_coverage"]["projection_rows"]["compact_indexed"] == 1200
    assert draft["data_coverage"]["projection_rows"]["detailed"] == len(retained)
    budget = frozen["data_access"]["request_budget"]["draft_player_details"]
    assert budget["original"] == 321
    assert budget["included"] == len(retained)
    assert budget["omitted"] == 321 - len(retained)


def test_compacted_review_preserves_frozen_evidence_and_disclosure_in_followups():
    frozen, _ = providers._frozen_dossier(large_draft())
    original = deepcopy(frozen)
    parent = AnalysisRun(
        id=1,
        task="recommendation",
        model="fixture",
        status="completed",
        question="Explain the draft",
        input_hash="fixture",
        output_json=json.dumps(
            {
                "summary": "Review the injury report. " * 500,
                "recommendations": [],
                "risks": [],
                "missing_information": [],
                "citations": [],
            }
        ),
    )
    providers._append_exchange(frozen, parent)
    followup, encoded = providers._frozen_dossier(frozen)
    assert len(encoded.encode()) <= providers.MAX_DOSSIER_BYTES
    assert followup["league"] == original["league"]
    assert followup["data_access"]["request_budget"] == original["data_access"]["request_budget"]
    assert followup["conversation"]["exchanges"][0]["answer"] == json.loads(parent.output_json)


def test_small_draft_keeps_all_existing_detail():
    dossier = large_draft()
    dossier["league"]["draft_context"]["player_pool"] = [{"id": 1, "name": "Player 1"}]
    frozen, _ = providers._frozen_dossier(dossier)
    assert frozen["league"] == dossier["league"]
    assert "draft_player_details" not in frozen["data_access"]["request_budget"]


def test_oversized_draft_without_an_index_does_not_silently_drop_players():
    dossier = large_draft()
    dossier["league"]["draft_context"].pop("player_index")
    with pytest.raises(ValueError, match="input limit"):
        providers._frozen_dossier(dossier)


def test_irreducible_evidence_still_respects_the_hard_limit():
    dossier = large_draft()
    dossier["league"]["draft_context"]["history"]["events"][0]["reason"] = "x" * 750_000
    original = deepcopy(dossier)
    with pytest.raises(ValueError, match="input limit"):
        providers._frozen_dossier(dossier)
    assert dossier == original
