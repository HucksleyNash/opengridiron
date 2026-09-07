from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app import api
from app.db import Base
from app.models import (
    Alert,
    AnalysisProvider,
    AnalysisRun,
    DataSnapshot,
    Game,
    League,
    LeagueAnalysis,
    NewsItem,
    Player,
    Pool,
    PoolEntry,
)
from app.schemas import AnalysisRequest, ProviderCreate, ProviderUpdate
from app.services import providers
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ANSWER = {
    "summary": "Use the supplied weekly forecast.",
    "recommendations": ["Keep the legal starter"],
    "risks": [],
    "missing_information": [],
    "citations": [],
}


@pytest.fixture
def audit_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        yield db
    engine.dispose()


def seed_league(db: Session) -> tuple[League, Player]:
    league = League(name="Audit league", season=2026, my_team_name="Owner")
    db.add(league)
    db.flush()
    player = Player(
        league_id=league.id,
        name="Runner",
        pro_team="CHI",
        position="RB",
        rostered_by="Owner",
        current_slot="RB",
        projected_points=300,
        projection_context_json=json.dumps({"source": "yahoo", "period": "season", "season": 2026}),
    )
    db.add(player)
    db.commit()
    return league, player


def seed_provider(db: Session, tasks: list[str] | None = None) -> AnalysisProvider:
    provider = AnalysisProvider(
        name="Audit provider",
        provider_type="openai",
        model="audit-model",
        enabled=True,
        task_defaults_json=json.dumps(tasks or ["chat"]),
    )
    db.add(provider)
    db.commit()
    return provider


def test_dossier_preserves_projection_period_and_owner_and_discloses_omissions(
    audit_db,
    monkeypatch,
) -> None:
    league, player = seed_league(audit_db)
    monkeypatch.setattr(providers, "DOSSIER_NEWS_LIMIT", 2)
    monkeypatch.setattr(providers, "DOSSIER_SNAPSHOT_LIMIT", 2)
    monkeypatch.setattr(providers, "DOSSIER_PLAYER_LIMIT", 1)
    audit_db.add(
        Player(
            league_id=league.id,
            name="Higher season total",
            pro_team="GB",
            position="RB",
            projected_points=999,
        )
    )
    for index in range(3):
        audit_db.add(
            NewsItem(
                canonical_url=f"https://example.com/news/{index}",
                title=f"News {index}",
                content_hash=str(index),
            )
        )
        audit_db.add(DataSnapshot(source="yahoo", source_id=str(index)))
    audit_db.commit()

    dossier = providers.prepare_analysis_dossier(audit_db, league.id, None, week=1)

    assert dossier["league"]["my_team_name"] == "Owner"
    assert dossier["data_access"]["scope"]["team_name"] == "Owner"
    assert dossier["league"]["players"][0]["id"] == player.id
    assert dossier["league"]["players"][0]["projection_context"]["period"] == "season"
    assert dossier["data_access"]["coverage"]["news"] == {"stored": 3, "included": 2}
    assert dossier["data_access"]["coverage"]["league_players"] == {"stored": 2, "included": 1}
    assert dossier["data_access"]["coverage"]["source_snapshots"] == {
        "stored": 3,
        "metadata_included": 2,
    }
    assert len(dossier["news"]) == 2


def test_saved_report_followup_uses_frozen_evidence_and_rejects_mixed_scope(audit_db) -> None:
    league, player = seed_league(audit_db)
    frozen = {
        "generated_at": "2026-09-01T00:00:00+00:00",
        "weekly_report": {
            "league_id": league.id,
            "team_name": "Owner",
            "week": 1,
            "forecasts": [{"player_id": player.id, "points": 12}],
        },
        "news": [{"title": "The original headline", "url": "https://example.com/original"}],
    }
    original = AnalysisRun(
        task="recommendation",
        model="audit",
        status="completed",
        input_hash="hash",
        question="Explain the weekly report",
        input_dossier_json=json.dumps(frozen),
        output_json=json.dumps(ANSWER),
    )
    audit_db.add(original)
    audit_db.flush()
    saved = LeagueAnalysis(
        league_id=league.id,
        team_name="Owner",
        season=2026,
        week=1,
        status="completed",
        analysis_run_id=original.id,
        report_json=json.dumps(frozen["weekly_report"]),
    )
    audit_db.add(saved)
    audit_db.commit()
    player.projected_points = 999
    audit_db.commit()

    dossier = providers.prepare_analysis_dossier(
        audit_db,
        None,
        None,
        league_report_id=saved.id,
    )
    assert dossier["weekly_report"]["forecasts"][0]["points"] == 12
    assert dossier["news"] == frozen["news"]
    assert dossier["conversation"]["exchanges"][0]["question"] == original.question
    assert dossier["data_access"]["scope"]["league_report_id"] == saved.id
    assert json.loads(original.input_dossier_json) == frozen
    with pytest.raises(ValueError, match="week does not match"):
        providers.prepare_analysis_dossier(audit_db, None, None, league_report_id=saved.id, week=2)
    with pytest.raises(ValueError, match="cannot be combined"):
        providers.prepare_analysis_dossier(audit_db, None, 1, league_report_id=saved.id)


def test_pool_analysis_contains_selected_card_outcomes_and_season_plan(audit_db) -> None:
    pool = Pool(name="Survivor", pool_type="survivor", season=2026, rules_json="{}")
    audit_db.add(pool)
    audit_db.flush()
    entries = [PoolEntry(pool_id=pool.id, name=name, active=True) for name in ("First", "Second")]
    audit_db.add_all(entries)
    audit_db.add(
        Game(
            season=2026,
            week=1,
            away_team="GB",
            home_team="CHI",
            kickoff=datetime.now(UTC) + timedelta(days=7),
            source="manual",
            source_timestamp=datetime.now(UTC),
            home_win_probability=0.7,
            win_probability_kind="manual",
        )
    )
    audit_db.commit()
    dossier = providers.prepare_analysis_dossier(
        audit_db,
        None,
        pool.id,
        pool_entry_id=entries[1].id,
        week=1,
    )
    assert dossier["pool"]["weekly_card"]["entry"]["id"] == entries[1].id
    assert dossier["pool"]["weekly_card"]["games"][0]["recommendations"]
    assert len(dossier["pool"]["standings"]["entries"]) == 2
    assert dossier["pool"]["season_strategy"]["entries"][0]["picks"]
    assert dossier["data_access"]["scope"]["pool_entry_id"] == entries[1].id
    providers._frozen_dossier(dossier)
    with pytest.raises(ValueError, match="does not belong"):
        providers.prepare_analysis_dossier(audit_db, None, pool.id, pool_entry_id=999, week=1)


@pytest.mark.asyncio
async def test_runs_freeze_inputs_snapshot_ids_and_parent_exchange(audit_db, monkeypatch) -> None:
    league, player = seed_league(audit_db)
    provider = seed_provider(audit_db)
    snapshot = DataSnapshot(source="yahoo", source_id="league")
    audit_db.add(snapshot)
    audit_db.commit()
    captured = []

    class Adapter:
        async def analyze(self, question, dossier):
            captured.append(dossier)
            return ANSWER, {"input_tokens": 42, "output_tokens": 12}

    monkeypatch.setattr(providers, "adapter_for", lambda *_: Adapter())
    first = await providers.run_analysis(audit_db, provider, "chat", "Who starts?", league.id, None)
    assert first.status == "completed"
    assert json.loads(first.snapshot_ids_json) == [snapshot.id]
    assert json.loads(first.input_dossier_json) == captured[0]
    player.projected_points = 999
    audit_db.commit()

    second = await providers.run_analysis(
        audit_db,
        provider,
        "chat",
        "Why?",
        None,
        None,
        parent_run_id=first.id,
    )
    assert second.parent_run_id == first.id
    assert captured[1]["league"]["players"][0]["projection"] == 300
    assert captured[1]["conversation"]["exchanges"][-1]["answer"] == ANSWER
    assert providers.analysis_context(second)["league_id"] == league.id
    with pytest.raises(ValueError, match="conflicts"):
        await providers.run_analysis(
            audit_db,
            provider,
            "chat",
            "Mix contexts",
            None,
            77,
            parent_run_id=first.id,
        )
    assert audit_db.query(AnalysisRun).count() == 2


@pytest.mark.asyncio
async def test_budget_failure_prevents_provider_call_and_running_row(audit_db, monkeypatch) -> None:
    provider = seed_provider(audit_db)
    monkeypatch.setattr(providers, "MAX_DOSSIER_BYTES", 100)
    monkeypatch.setattr(providers, "adapter_for", lambda *_: pytest.fail("Provider must not run"))
    with pytest.raises(ValueError, match="input limit"):
        await providers.run_analysis(
            audit_db,
            provider,
            "chat",
            "Question",
            None,
            None,
            dossier_override={"news": [{"title": "x" * 1000}]},
        )
    assert audit_db.query(AnalysisRun).count() == 0


@pytest.mark.asyncio
async def test_health_checks_real_model_metadata_and_redacts_provider_errors(monkeypatch) -> None:
    requests = []
    response_status = 200

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        return httpx.Response(
            response_status,
            json={
                "data": [{"id": "audit-model"}],
                "error": "secret-do-not-return",
            },
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    config = AnalysisProvider(name="Audit", provider_type="openai", model="audit-model")
    assert not (await providers.OpenAIAdapter(config, None).health())["ok"]
    assert not requests
    assert (await providers.OpenAIAdapter(config, "private-key").health())["ok"]
    config.model = "missing"
    assert not (await providers.OpenAIAdapter(config, "private-key").health())["ok"]
    response_status = 401
    result = await providers.OpenAIAdapter(config, "private-key").health()
    assert not result["ok"]
    assert "authentication" in result["error"]
    assert "private-key" not in json.dumps(result)
    assert "secret-do-not-return" not in json.dumps(result)


def test_provider_updates_exclusively_assign_defaults_and_preserve_other_tasks(audit_db) -> None:
    first = api.create_provider(
        ProviderCreate(
            name="First",
            provider_type="openai",
            model="first",
            task_defaults=["chat", "news"],
        ),
        audit_db,
    )
    second = api.create_provider(
        ProviderCreate(
            name="Second",
            provider_type="openai",
            model="second",
            task_defaults=["recommendation"],
        ),
        audit_db,
    )
    result = api.update_provider(
        second.id,
        ProviderUpdate(
            task_defaults=["chat", "recommendation"],
            enabled=True,
        ),
        audit_db,
    )
    assert result.task_defaults == ["chat", "recommendation"]
    assert json.loads(audit_db.get(AnalysisProvider, first.id).task_defaults_json) == ["news"]
    with pytest.raises(HTTPException) as error:
        api.update_provider(
            second.id, ProviderUpdate(name="First", task_defaults=["news"]), audit_db
        )
    assert error.value.status_code == 409
    assert json.loads(audit_db.get(AnalysisProvider, first.id).task_defaults_json) == ["news"]


@pytest.mark.asyncio
async def test_explicit_disabled_or_missing_provider_never_falls_back(
    audit_db, monkeypatch
) -> None:
    provider = seed_provider(audit_db)
    provider.enabled = False
    audit_db.commit()
    monkeypatch.setattr(
        api, "run_analysis", lambda *_: pytest.fail("Must reject disabled provider")
    )
    for requested_id in (provider.id, 999):
        with pytest.raises(HTTPException) as error:
            await api.analyze(
                AnalysisRequest(question="Question", provider_id=requested_id), audit_db
            )
        assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_news_classification_is_opt_in_validates_ids_and_is_not_replayed(
    audit_db, monkeypatch
):
    provider = seed_provider(audit_db)
    item = NewsItem(
        canonical_url="https://example.com/article",
        title="Player ruled out",
        excerpt="Injury news",
        content_hash="headline-version",
        category="news",
        severity="info",
        retrieved_at=datetime.now(UTC),
    )
    audit_db.add(item)
    audit_db.commit()
    calls = []
    wrong_id = True

    class Adapter:
        async def analyze(self, question, dossier):
            calls.append(dossier)
            return {
                "classifications": [
                    {
                        "item_id": 999 if wrong_id else item.id,
                        "category": "injury",
                        "severity": "urgent",
                    }
                ]
            }, {"input_tokens": 10, "output_tokens": 3}

    monkeypatch.setattr(providers, "adapter_for", lambda *_: Adapter())
    assert (await providers.classify_new_news(audit_db, [item.id]))["status"] == "disabled"
    assert not calls
    provider.task_defaults_json = '["news"]'
    audit_db.commit()
    failed = await providers.classify_new_news(audit_db, [item.id])
    assert failed["status"] == "failed"
    assert (item.category, item.severity) == ("news", "info")
    assert audit_db.query(Alert).count() == 0
    wrong_id = False
    result = await providers.classify_new_news(audit_db, [item.id])
    assert result["classified"] == 1
    assert (item.category, item.severity) == ("injury", "urgent")
    assert audit_db.query(Alert).one().severity == "urgent"
    assert audit_db.query(DataSnapshot).one().source == "ai_news_classification"
    assert (await providers.classify_new_news(audit_db, [item.id]))["classified"] == 0
    assert len(calls) == 2
