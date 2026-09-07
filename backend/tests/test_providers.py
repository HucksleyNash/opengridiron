from __future__ import annotations

import csv
import io
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app import api
from app.config import settings
from app.db import SessionLocal
from app.models import (
    Alert,
    AnalysisProvider,
    AnalysisRun,
    Game,
    NewsItem,
    NewsSource,
    Pool,
    PoolEntry,
    PoolEntryWeek,
    PoolPick,
)
from app.schemas import AnalysisRequest, ProviderModelsRequest
from app.services import providers


def test_analysis_output_schema_is_strict() -> None:
    schema = providers.OUTPUT_SCHEMA

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_scoring_context_canonicalizes_yahoo_default_labels() -> None:
    context = providers.scoring_context(
        {
            "passing_yards_yahoo_default": 0.02,
            "passing_touchdowns_yahoo_default": 6.0,
            "interceptions_yahoo_default": -2.0,
            "receptions_yahoo_default": 1.5,
            "rushing_yards": 0.1,
        }
    )

    assert context["configured"] == {
        "passing_yards": 0.02,
        "passing_tds": 6.0,
        "interceptions": -2.0,
        "receptions": 1.5,
        "rushing_yards": 0.1,
    }
    assert context["effective"]["passing_yards"] == 0.02
    assert context["effective"]["passing_tds"] == 6.0
    assert context["effective"]["interceptions"] == -2.0
    assert context["effective"]["receptions"] == 1.5
    assert context["yardage_rates"]["passing_yards"] == {
        "points_per_yard": 0.02,
        "yards_per_point": 50.0,
    }


@pytest.mark.asyncio
async def test_analysis_run_persists_the_question_and_result(monkeypatch, client) -> None:
    assert client.get("/healthz").status_code == 200
    provider = AnalysisProvider(
        name=f"History provider {uuid4()}",
        provider_type="openai",
        model="gpt-history",
        enabled=True,
        task_defaults_json='["chat"]',
    )

    class FakeAdapter:
        async def analyze(self, question: str, dossier: dict) -> tuple[dict, dict[str, int]]:
            assert question == "Who should I start this week?"
            assert dossier["league"] == {"id": 72, "name": "History League"}
            assert dossier["data_access"]["scope"]["league_id"] == 72
            return (
                {
                    "summary": "Start the higher-volume option.",
                    "recommendations": ["Start Player A"],
                    "risks": ["Late injury news"],
                    "missing_information": [],
                    "citations": [],
                },
                {"input_tokens": 42, "output_tokens": 18},
            )

    monkeypatch.setattr(
        providers,
        "build_dossier",
        lambda _db, _league_id, _pool_id, _draft_session_id: {
            "league": {"id": 72, "name": "History League"}
        },
    )
    monkeypatch.setattr(providers, "adapter_for", lambda _db, _provider: FakeAdapter())

    with SessionLocal() as db:
        db.add(provider)
        db.commit()
        db.refresh(provider)
        run = await providers.run_analysis(
            db,
            provider,
            "chat",
            "Who should I start this week?",
            league_id=72,
            pool_id=None,
        )
        stored = db.get(AnalysisRun, run.id)

    assert stored is not None
    assert stored.question == "Who should I start this week?"
    assert stored.status == "completed"
    assert stored.input_tokens == 42
    assert stored.output_tokens == 18
    assert '"summary": "Start the higher-volume option."' in (stored.output_json or "")


def test_replacement_baselines_count_yahoo_superflex_slots() -> None:
    rows = [
        SimpleNamespace(id=1, athlete_id=1, position="QB", projected_points=30.0),
        SimpleNamespace(id=2, athlete_id=2, position="QB", projected_points=25.0),
        SimpleNamespace(id=3, athlete_id=3, position="QB", projected_points=20.0),
        SimpleNamespace(id=4, athlete_id=4, position="RB", projected_points=28.0),
        SimpleNamespace(id=5, athlete_id=5, position="RB", projected_points=24.0),
        SimpleNamespace(id=6, athlete_id=6, position="RB", projected_points=18.0),
    ]

    baselines = providers._replacement_baselines(  # type: ignore[arg-type]
        rows, ["QB", "RB", "Q/W/R/T"], team_count=1
    )

    assert baselines == {"QB": 20.0, "RB": 24.0}


def test_selected_draft_session_supplies_authoritative_analyst_context(client) -> None:
    league = client.post(
        "/api/v1/leagues",
        json={
            "name": f"Analyst draft {uuid4()}",
            "season": 2026,
            "scoring": {"receptions": 1.0},
            "roster_slots": ["QB", "RB", "WR", "TE", "Q/W/R/T"],
        },
    ).json()
    player_inputs = [
        ("Eli Heidenreich", "RB", "Active", 384.22, 802, 141),
        ("Validated RB", "RB", "Active", 320.0, 1, 1),
        ("QB One", "QB", "Active", 310.0, 2, 1),
        ("QB Two", "QB", "Active", 290.0, 10, 2),
        ("Questionable WR", "WR", "Questionable", 300.0, 3, 1),
        ("WR Two", "WR", "Active", 280.0, 15, 2),
        ("TE One", "TE", "Active", 270.0, 4, 1),
        ("TE Two", "TE", "Active", 260.0, 20, 2),
    ]
    players: list[dict] = []
    for index, (name, position, status, projection, _adp, _position_rank) in enumerate(
        player_inputs
    ):
        response = client.post(
            f"/api/v1/leagues/{league['id']}/players",
            json={
                "source_id": f"analysis-{uuid4().hex}-{index}",
                "name": name,
                "pro_team": "CHI",
                "position": position,
                "status": status,
                "projected_points": projection,
                "floor": projection * 0.75,
                "ceiling": projection * 1.2,
                "risk": 0.25,
            },
        )
        assert response.status_code == 201
        players.append(response.json())

    content = io.StringIO()
    writer = csv.DictWriter(
        content,
        fieldnames=[
            "player_id",
            "projected_points",
            "floor",
            "ceiling",
            "risk",
            "adp",
            "position_rank",
        ],
    )
    writer.writeheader()
    for player, (_, _position, _status, projection, adp, position_rank) in zip(
        players, player_inputs, strict=True
    ):
        writer.writerow(
            {
                "player_id": player["id"],
                "projected_points": projection,
                "floor": projection * 0.75,
                "ceiling": projection * 1.2,
                "risk": 0.25,
                "adp": adp,
                "position_rank": position_rank,
            }
        )
    preview = client.post(
        f"/api/v1/leagues/{league['id']}/draft-inputs/preview",
        params={"input_type": "combined"},
        files={"file": ("analysis-draft.csv", content.getvalue().encode(), "text/csv")},
    ).json()
    committed = client.post(
        f"/api/v1/leagues/{league['id']}/draft-inputs/previews/{preview['preview_id']}/commit",
        json={
            "content_hash": preview["content_hash"],
            "acknowledge_warnings": True,
        },
    ).json()
    session = client.post(
        f"/api/v1/leagues/{league['id']}/draft-sessions",
        json={
            "kind": "mock",
            "team_count": 8,
            "round_count": 1,
            "owner_team_slot": 2,
            "owner_team_name": "Analyst Owner",
            "team_names": [
                "Opponent One",
                "Analyst Owner",
                "Opponent Three",
                "Opponent Four",
                "Opponent Five",
                "Opponent Six",
                "Opponent Seven",
                "Opponent Eight",
            ],
        },
    ).json()
    bound = client.post(
        f"/api/v1/draft-sessions/{session['id']}/input-snapshots",
        json={
            "expected_sequence": 0,
            "projection_snapshot_id": committed["projection_snapshot_id"],
            "ranking_snapshot_id": committed["ranking_snapshot_id"],
        },
    )
    assert bound.status_code == 200
    started = client.post(
        f"/api/v1/draft-sessions/{session['id']}/actions/start",
        json={"expected_sequence": 0, "idempotency_key": f"start-{uuid4()}"},
    )
    assert started.status_code == 200
    recorded = client.post(
        f"/api/v1/draft-sessions/{session['id']}/events",
        json={
            "type": "pick_recorded",
            "expected_sequence": 1,
            "idempotency_key": f"pick-{uuid4()}",
            "player_id": players[1]["id"],
        },
    )
    assert recorded.status_code == 201
    recommendations = client.get(f"/api/v1/draft-sessions/{session['id']}/recommendations").json()
    assert recommendations["candidates"]
    owner_pick = client.post(
        f"/api/v1/draft-sessions/{session['id']}/events",
        json={
            "type": "pick_recorded",
            "expected_sequence": 2,
            "idempotency_key": f"owner-pick-{uuid4()}",
            "player_id": players[2]["id"],
            "recommendation_snapshot_id": recommendations["snapshot_id"],
        },
    )
    assert owner_pick.status_code == 201
    preferences = client.put(
        f"/api/v1/draft-sessions/{session['id']}/preferences",
        json={
            "expected_revision": 0,
            "items": [
                {
                    "player_id": players[6]["id"],
                    "queue_rank": 1,
                    "target": True,
                    "fade": False,
                    "note": "Take only at a fair price",
                }
            ],
        },
    )
    assert preferences.status_code == 200
    completed = client.post(
        f"/api/v1/draft-sessions/{session['id']}/actions/complete",
        json={
            "expected_sequence": 3,
            "idempotency_key": f"complete-{uuid4()}",
            "reason": "Analyst dossier regression fixture",
        },
    )
    assert completed.status_code == 200

    with SessionLocal() as db:
        dossier = providers.build_dossier(
            db,
            league_id=league["id"],
            pool_id=None,
            draft_session_id=session["id"],
        )

    league_context = dossier["league"]
    draft_context = league_context["draft_context"]
    assert draft_context["team_count"] == 8
    assert draft_context["round_count"] == 1
    assert draft_context["owner_team_slot"] == 2
    assert draft_context["draft_order"]["teams"][0]["name"] == "Opponent One"
    assert draft_context["board"]["team_rosters"][0]["roster"][0]["name"] == "Validated RB"
    assert draft_context["board"]["completed_picks"] == 2
    assert draft_context["format_inputs"]["keeper_rules"]["status"] == "unsupported"
    assert draft_context["format_inputs"]["auction_budget"]["status"] == "not_applicable_to_snake"
    assert league_context["player_data_quality"]["ros_value_semantics"] == (
        "unavailable_do_not_use"
    )

    by_name = {player["name"]: player for player in draft_context["player_pool"]}
    anomaly = by_name["Eli Heidenreich"]
    assert anomaly["market_rank"] == 802
    assert anomaly["strategy_eligible"] is False
    assert anomaly["projection_warning"]["code"] == "projection_market_mismatch"
    assert all(item["name"] != "Eli Heidenreich" for item in draft_context["recommendations"])
    assert by_name["Questionable WR"]["injury_context"]["status"] == (
        "designation_only_no_attributed_details"
    )
    assert draft_context["recommendations"] == []
    history = draft_context["history"]
    assert [event["type"] for event in history["events"]] == [
        "session_started",
        "pick_recorded",
        "pick_recorded",
        "session_completed",
    ]
    assert len(history["owner_decisions"]) == 1
    decision = history["owner_decisions"][0]
    assert decision["event"]["player_name"] == "QB One"
    assert decision["recommendation_snapshot"]["chosen_candidate"]["name"] == "QB One"
    assert decision["recommendation_snapshot"]["chosen_rank"] is not None
    assert decision["recommendation_snapshot"]["id"] == recommendations["snapshot_id"]
    assert history["latest_recommendation_snapshot"]["id"] >= recommendations["snapshot_id"]
    freshness_refs = {item["ref"] for item in history["recommendation_freshness_contexts"]}
    assert freshness_refs
    assert all(
        item["freshness_ref"] in freshness_refs for item in history["recommendation_snapshots"]
    )
    assert draft_context["preferences"] == [
        {
            "player_id": players[6]["id"],
            "player_name": "TE One",
            "queue_rank": 1,
            "target": True,
            "fade": False,
            "note": "Take only at a fair price",
            "updated_at": draft_context["preferences"][0]["updated_at"],
        }
    ]
    assert draft_context["data_coverage"]["events"] == {"stored": 4, "included": 4}
    assert draft_context["data_coverage"]["linked_owner_decisions"] == {
        "stored": 1,
        "included": 1,
    }
    assert draft_context["data_coverage"]["recommendation_snapshots"]["stored"] == len(
        history["recommendation_snapshots"]
    )
    assert draft_context["data_coverage"]["projection_rows"] == {
        "stored": 8,
        "compact_indexed": 8,
        "detailed": 8,
    }
    assert dossier["data_access"]["scope"] == {
        "league_id": league["id"],
        "pool_id": None,
        "draft_session_id": session["id"],
    }


def test_analyst_dossier_includes_all_stored_alerts_and_news() -> None:
    marker = uuid4().hex
    old = datetime(2020, 1, 1, tzinfo=UTC)
    with SessionLocal() as db:
        source = NewsSource(
            name=f"Analyst source {marker}",
            url=f"https://example.com/{marker}",
            source_type="rss",
            enabled=True,
            official=True,
            last_fetched_at=old,
        )
        db.add(source)
        db.flush()
        for index in range(13):
            db.add(
                Alert(
                    fingerprint=f"{marker}-alert-{index}",
                    title=f"{marker} alert {index}",
                    message="Stored alert evidence",
                    severity="info",
                    created_at=old + timedelta(seconds=index),
                )
            )
        for index in range(21):
            db.add(
                NewsItem(
                    source_id=source.id,
                    canonical_url=f"https://example.com/{marker}/news/{index}",
                    content_hash=f"{marker}-{index}"[:64],
                    title=f"{marker} news {index}",
                    excerpt="Stored news evidence",
                    category="news",
                    severity="info",
                    retrieved_at=old + timedelta(seconds=index),
                )
            )
        db.commit()
        dossier = providers.build_dossier(db, league_id=None, pool_id=None)

    assert {item["title"] for item in dossier["alerts"]}.issuperset(
        {f"{marker} alert {index}" for index in range(13)}
    )
    assert {item["title"] for item in dossier["news"]}.issuperset(
        {f"{marker} news {index}" for index in range(21)}
    )
    source_context = next(
        item for item in dossier["news_sources"] if item["name"] == f"Analyst source {marker}"
    )
    assert source_context["official"] is True
    assert dossier["data_access"]["security_exclusions"]["secrets"] is True
    assert dossier["data_access"]["coverage"]["alerts"]["stored"] == len(dossier["alerts"])
    assert dossier["data_access"]["coverage"]["news"]["stored"] == len(dossier["news"])


def test_pool_dossier_includes_saved_week_and_game_provenance() -> None:
    marker = uuid4().hex
    kickoff = datetime(2026, 9, 6, 17, 0, tzinfo=UTC)
    with SessionLocal() as db:
        pool = Pool(
            name=f"Analyst pool {marker}",
            pool_type="confidence",
            season=2026,
            rules_json='{"picks_per_week": 1}',
        )
        db.add(pool)
        db.flush()
        entry = PoolEntry(pool_id=pool.id, name="Primary", active=True)
        db.add(entry)
        db.flush()
        week = PoolEntryWeek(entry_id=entry.id, week=1, version=7)
        game = Game(
            season=2026,
            week=1,
            away_team="CHI",
            home_team="GB",
            kickoff=kickoff,
            home_win_probability=0.61,
            home_cover_probability=0.54,
            spread_home=-3.5,
            total=44.5,
            source="fixture",
            source_timestamp=kickoff - timedelta(days=1),
            source_game_key=f"analyst-{marker}",
            source_game_key_kind="fixture",
            win_probability_kind="model",
            cover_probability_kind="model",
        )
        db.add_all([week, game])
        db.flush()
        pick = PoolPick(
            entry_id=entry.id,
            game_id=game.id,
            week=1,
            slot=1,
            team="GB",
            confidence=16,
            result="pending",
        )
        db.add(pick)
        db.commit()
        dossier = providers.build_dossier(db, league_id=None, pool_id=pool.id)

    assert dossier["pool"]["entries"][0]["weeks"][0]["version"] == 7
    assert dossier["pool"]["entries"][0]["picks"][0]["game_id"] == game.id
    saved_game = next(item for item in dossier["pool"]["games"] if item["id"] == game.id)
    assert saved_game["total"] == 44.5
    assert saved_game["source_game_key_kind"] == "fixture"
    assert saved_game["win_probability_kind"] == "model"


@pytest.mark.asyncio
async def test_analysis_route_forwards_selected_draft_session(monkeypatch) -> None:
    provider = AnalysisProvider(
        name=f"Draft context provider {uuid4()}",
        provider_type="openai",
        model="gpt-test",
        enabled=True,
        task_defaults_json='["chat"]',
    )
    with SessionLocal() as db:
        db.add(provider)
        db.commit()
        db.refresh(provider)
        captured: dict[str, int | None] = {}

        async def fake_run_analysis(
            _db,
            _provider,
            _task,
            _question,
            _league_id,
            _pool_id,
            draft_session_id,
        ):
            captured["draft_session_id"] = draft_session_id
            return SimpleNamespace(
                id=44,
                model="gpt-test",
                status="completed",
                output_json=(
                    '{"summary":"ok","recommendations":[],"risks":[],'
                    '"missing_information":[],"citations":[]}'
                ),
                error=None,
            )

        monkeypatch.setattr(api, "run_analysis", fake_run_analysis)
        result = await api.analyze(
            AnalysisRequest(
                provider_id=provider.id,
                question="Build my draft plan",
                league_id=12,
                draft_session_id=34,
            ),
            db,
        )

    assert captured == {"draft_session_id": 34}
    assert result.run_id == 44


def test_codex_provider_uses_openai_environment_key(monkeypatch) -> None:
    monkeypatch.setattr(
        providers,
        "settings",
        replace(settings, openai_api_key="test-openai-key"),
    )
    provider = AnalysisProvider(
        name="Codex sidecar",
        provider_type="codex",
        model="gpt-5.3-codex",
    )

    assert providers.provider_secret(None, provider) == "test-openai-key"  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_codex_model_discovery_uses_authenticated_runner(monkeypatch) -> None:
    async def runner_request(
        method: str,
        path: str,
        *,
        timeout: float,
        json: dict[str, str | None],
    ) -> dict[str, list[str]]:
        assert method == "POST"
        assert path == "/v1/models"
        assert timeout == 30
        assert json == {"api_key": None}
        return {"models": ["gpt-5.6-sol", "gpt-5.6-terra"]}

    monkeypatch.setattr(api, "_codex_runner_request", runner_request)

    result = await api.provider_models(ProviderModelsRequest(provider_type="codex"))

    assert result == {"models": ["gpt-5.6-sol", "gpt-5.6-terra"]}


@pytest.mark.asyncio
async def test_openai_model_discovery_uses_account_models(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.openai.com/v1/models"
        assert request.headers["authorization"] == "Bearer openai-test-key"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "gpt-z", "object": "model"},
                    {"id": "gpt-a", "object": "model"},
                ],
            },
        )

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **_kwargs: original_client(transport=transport),
    )

    assert await providers.discover_provider_models("openai", "openai-test-key") == [
        "gpt-a",
        "gpt-z",
    ]


@pytest.mark.asyncio
async def test_anthropic_model_discovery_follows_pagination(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["x-api-key"] == "anthropic-test-key"
        assert request.headers["anthropic-version"] == providers.ANTHROPIC_API_VERSION
        if request.url.params.get("after_id"):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "claude-second"}],
                    "has_more": False,
                    "last_id": "claude-second",
                },
            )
        return httpx.Response(
            200,
            json={
                "data": [{"id": "claude-first"}],
                "has_more": True,
                "last_id": "claude-first",
            },
        )

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **_kwargs: original_client(transport=transport),
    )

    assert await providers.discover_provider_models("anthropic", "anthropic-test-key") == [
        "claude-first",
        "claude-second",
    ]
    assert len(requests) == 2
    assert requests[1].url.params["after_id"] == "claude-first"
