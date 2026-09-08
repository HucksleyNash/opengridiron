from __future__ import annotations

import json
from copy import deepcopy

import pytest
from app.db import Base
from app.models import AnalysisProvider, AnalysisRun, League, Player
from app.services import providers
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def large_league() -> dict:
    scoring = {f"scoring_category_{index}": index / 10 for index in range(42)}
    definition = {
        "kind": "projection_range_model",
        "source": "nflverse",
        "source_url": "https://example.com/football-statistics",
        "license": "CC-BY-4.0",
        "model_version": "nflverse-ranges-v1",
        "range_definition": "empirical P20-P80 season-points estimate around Yahoo mean",
        "risk_definition": "estimated probability of finishing below 80% of Yahoo projection",
    }
    return {
        "league": {
            "id": 2,
            "my_team_name": "Owner",
            "scoring": scoring,
            "players": [
                {
                    "id": index,
                    "name": f"Player {index}",
                    "position": "RB",
                    "rostered_by": "Owner" if index < 16 else None,
                    "projection": 300 - index / 10,
                    "projection_context": {
                        "source": "Yahoo Fantasy",
                        "period": "season",
                        "season": 2026,
                        "week": None,
                        "scoring_basis": "source_points",
                        "scoring": deepcopy(scoring),
                        "received_at": f"2026-09-08T12:00:00.{index:06d}+00:00",
                        "ros_value_state": "missing",
                    },
                    "risk": 0.4,
                    "evidence": [
                        {
                            **definition,
                            "historical_games": index % 60,
                            "historical_seasons": [2023, 2024, 2025],
                            "weekly_cv": 0.1234,
                            "confidence": "medium",
                            "matched": index % 2 == 0,
                            "risk_factors": ["Availability and workload remain uncertain."],
                            "yahoo_id": str(index),
                        }
                    ],
                }
                for index in range(500)
            ],
        },
        "data_access": {
            "scope": {"league_id": 2, "team_name": "Owner"},
            "coverage": {"league_players": {"stored": 1200, "included": 500}},
        },
        "news": [{"title": "Source evidence", "excerpt": "Relevant news. " * 8000}],
    }


def expanded_players(dossier: dict) -> list[dict]:
    league = dossier["league"]
    players = deepcopy(league["players"])
    for player in players:
        context = player["projection_context"]
        if "context_ref" in context:
            context.update(league["player_projection_contexts"][context.pop("context_ref")])
        for evidence in player["evidence"]:
            if "definition_ref" in evidence:
                evidence.update(
                    league["player_evidence_definitions"][evidence.pop("definition_ref")]
                )
    return players


def test_large_league_fits_without_losing_player_or_source_evidence():
    dossier = large_league()
    # Different historical scoring and model versions must never inherit today's rules.
    players = dossier["league"]["players"]
    for player in players[:2]:
        player["projection_context"]["scoring"] = {"passing_td": 4.0}
        player["evidence"][0]["model_version"] = "older-ranges"
    players[2]["projection_context"]["scoring"] = None
    players[3]["projection_context"]["scoring"] = {}
    players[4]["projection_context"].pop("scoring")
    players[5]["evidence"].append({"kind": "injury", "source_url": "https://example.com/injury"})
    original = deepcopy(dossier)
    assert len(json.dumps(dossier, separators=(",", ":")).encode()) > providers.MAX_DOSSIER_BYTES

    frozen, encoded = providers._frozen_dossier(dossier)

    assert len(encoded.encode()) <= providers.MAX_DOSSIER_BYTES
    assert json.loads(encoded) == frozen
    assert dossier == original
    assert expanded_players(frozen) == original["league"]["players"]
    assert frozen["news"] == original["news"]
    assert frozen["data_access"]["coverage"] == original["data_access"]["coverage"]
    assert len(frozen["league"]["player_projection_contexts"]) == 2
    assert len(frozen["league"]["player_evidence_definitions"]) == 2


def test_shared_league_context_survives_frozen_followups():
    frozen, encoded = providers._frozen_dossier(large_league())
    original = deepcopy(frozen)
    parent = AnalysisRun(
        id=1,
        task="chat",
        status="completed",
        model="fixture",
        question="Evaluate my roster",
        input_hash="fixture",
        input_dossier_json=encoded,
        output_json=json.dumps({"summary": "Review the injury report. " * 500}),
    )
    providers._append_exchange(frozen, parent)
    followup, encoded = providers._frozen_dossier(frozen)

    assert len(encoded.encode()) <= providers.MAX_DOSSIER_BYTES
    assert followup["league"] == original["league"]
    assert followup["data_access"] == original["data_access"]
    assert expanded_players(followup) == large_league()["league"]["players"]
    assert followup["conversation"]["exchanges"][0]["answer"] == json.loads(parent.output_json)


def test_small_league_keeps_existing_format():
    dossier = large_league()
    dossier["league"]["players"] = dossier["league"]["players"][:2]
    frozen, _ = providers._frozen_dossier(dossier)
    assert frozen["league"] == dossier["league"]


def test_irreducible_league_still_enforces_budget_without_mutating_input():
    dossier = large_league()
    dossier["news"][0]["excerpt"] = "x" * providers.MAX_DOSSIER_BYTES
    original = deepcopy(dossier)
    with pytest.raises(ValueError, match="input limit"):
        providers._frozen_dossier(dossier)
    assert dossier == original


@pytest.mark.asyncio
async def test_chat_saves_shared_evidence_and_followup_keeps_original_scoring(monkeypatch):
    captured = []
    answer = {
        "summary": "Use the supplied evidence.",
        "recommendations": [],
        "risks": [],
        "missing_information": [],
        "citations": [],
    }

    class Adapter:
        async def analyze(self, question, dossier):
            captured.append(deepcopy(dossier))
            return answer, {}

    monkeypatch.setattr(providers, "adapter_for", lambda *_: Adapter())
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        league = League(name="Large league", season=2026, my_team_name="Owner")
        provider = AnalysisProvider(name="Fixture", provider_type="openai", model="fixture")
        db.add_all([league, provider])
        db.flush()
        for row in large_league()["league"]["players"]:
            db.add(
                Player(
                    league_id=league.id,
                    name=row["name"],
                    pro_team="CHI",
                    position=row["position"],
                    rostered_by=row["rostered_by"],
                    projected_points=row["projection"],
                    projection_context_json=json.dumps(row["projection_context"]),
                    evidence_json=json.dumps(row["evidence"]),
                )
            )
        db.commit()
        original = providers.prepare_analysis_dossier(db, league.id, None)

        first = await providers.run_analysis(
            db, provider, "chat", "Evaluate my roster", league.id, None
        )
        assert first.status == "completed"
        assert json.loads(first.input_dossier_json) == captured[0]
        assert expanded_players(captured[0]) == original["league"]["players"]
        assert len(first.input_dossier_json.encode()) <= providers.MAX_DOSSIER_BYTES

        db.query(Player).update({"projection_context_json": "{}", "projected_points": 999})
        db.commit()
        second = await providers.run_analysis(
            db, provider, "chat", "Why?", None, None, parent_run_id=first.id
        )
        assert second.status == "completed"
        assert second.parent_run_id == first.id
        assert captured[1]["league"] == captured[0]["league"]
        assert captured[1]["conversation"]["exchanges"][0]["answer"] == answer
    engine.dispose()
