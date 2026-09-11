from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from app.db import Base, get_db
from app.main import app
from app.models import AnalysisRun, League
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def library_db(client):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        app.dependency_overrides[get_db] = lambda: db
        yield db
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def saved(
    db,
    identifier,
    *,
    parent=None,
    question=None,
    status="completed",
    scope=None,
    summary="Saved recommendation",
    extra=None,
):
    dossier = {"data_access": {"scope": scope or {}}, "private_evidence": "secret-dossier"}
    if extra:
        dossier.update(extra)
    row = AnalysisRun(
        id=identifier,
        task="chat",
        model="test",
        input_hash=str(identifier),
        parent_run_id=parent,
        question=question or f"Question {identifier}",
        status=status,
        created_at=datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=identifier),
        input_dossier_json=json.dumps(dossier),
        output_json=json.dumps(
            {
                "summary": summary,
                "recommendations": ["Start Player A"],
                "risks": [],
                "missing_information": [],
                "citations": [],
            }
        ),
    )
    db.add(row)
    db.commit()
    return row


def test_library_paginates_all_records_and_old_detail_is_independent(client, library_db):
    for identifier in range(1, 112):
        saved(library_db, identifier)
    first = client.get("/api/v1/analysis/library?limit=25").json()
    assert first["total"] == first["run_count"] == 111
    assert len(first["items"]) == 25 and first["has_more"]
    last = client.get("/api/v1/analysis/library?offset=100&limit=25").json()
    assert len(last["items"]) == 11 and not last["has_more"]
    assert last["items"][-1]["id"] == 1
    detail = client.get("/api/v1/analysis/runs/1").json()
    assert detail["turns"][0]["question"] == "Question 1"
    assert "output" not in first["items"][0]
    assert "secret-dossier" not in json.dumps(first) + json.dumps(detail)


def test_search_matches_full_answer_and_returns_matching_turn(client, library_db):
    saved(library_db, 1, summary="root " + "x" * 500 + " distinctive uncertainty")
    saved(library_db, 2, parent=1)
    response = client.get("/api/v1/analysis/library?q=distinctive%20uncertainty").json()
    assert response["total"] == 1
    assert response["items"][0]["id"] == 1
    assert response["items"][0]["latest_id"] == 2
    assert response["items"][0]["reply_count"] == 1
    assert len(response["items"][0]["preview"]) <= 240
    assert client.get("/api/v1/analysis/library?q=secret-dossier").json()["total"] == 0
    assert client.get("/api/v1/analysis/library?q=%25").json()["total"] == 0


def test_scope_filters_and_generated_titles_use_real_context(client, library_db):
    library_db.add(League(id=1, name="My league", season=2026))
    library_db.commit()
    scope = {"league_id": 1, "week": 2, "team_name": "My team", "private_key": "never expose"}
    saved(library_db, 1, question="Explain this weekly_report for the selected team", scope=scope)
    saved(library_db, 2, status="failed", scope={**scope, "week": 3})
    result = client.get("/api/v1/analysis/library?scope=league:1&week=2&status=completed").json()
    assert result["total"] == 1
    assert result["items"][0]["context"]["league_name"] == "My league"
    assert result["items"][0]["title"] == "Week 2 · League review · My league"
    assert "private_key" not in result["items"][0]["context"]
    assert client.get("/api/v1/analysis/library?q=My%20team").json()["total"] == 2
    assert client.get("/api/v1/analysis/library?scope=pool:1").json()["total"] == 0


def test_conversation_restores_ancestors_and_identifies_branches(client, library_db):
    saved(library_db, 1)
    saved(library_db, 2, parent=1)
    saved(library_db, 3, parent=2)
    saved(library_db, 4, parent=1)
    response = client.get("/api/v1/analysis/runs/3").json()
    assert [turn["id"] for turn in response["turns"]] == [1, 2, 3]
    assert [branch["id"] for branch in response["branches"]] == [4]
    assert response["root_id"] == 1 and not response["lineage_incomplete"]
    assert client.get("/api/v1/analysis/library").json()["items"][0]["reply_count"] == 3


def test_legacy_json_and_cycles_remain_readable(client, library_db):
    first = saved(library_db, 1, parent=2)
    saved(library_db, 2, parent=1)
    first.input_dossier_json = "invalid-json"
    library_db.commit()
    response = client.get("/api/v1/analysis/runs/1")
    assert response.status_code == 200
    assert response.json()["lineage_incomplete"]
    assert len(response.json()["turns"]) == 2
    assert client.get("/api/v1/analysis/library").json()["total"] == 1


def test_missing_ids_and_invalid_filters_are_explicit(client, library_db):
    assert client.get("/api/v1/analysis/runs/9999").status_code == 404
    for query in ("week=19", "offset=-1", "limit=101", "status=unknown"):
        assert client.get(f"/api/v1/analysis/library?{query}").status_code == 422
