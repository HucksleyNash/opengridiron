from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime, timedelta

import pytest
from app.db import SessionLocal
from app.models import AnalysisProvider, AnalysisRun, DataSnapshot, Game, NewsSource
from app.services import pool_analysis, providers

SEASONS = itertools.count(2040)


@pytest.fixture
def pool_case(client, monkeypatch):
    season = next(SEASONS)
    games = [
        client.post(
            "/api/v1/games",
            json={
                "season": season,
                "week": 1,
                "away_team": away,
                "home_team": home,
                "kickoff": f"{season}-09-10T18:00:00Z",
                "home_win_probability": 0.7,
                "home_cover_probability": 0.54,
                "spread_home": -3.5,
            },
        ).json()
        for away, home in [("GB", "CHI"), ("KC", "DEN")]
    ]
    pool = client.post(
        "/api/v1/pools",
        json={
            "name": f"Check in {season}",
            "pool_type": "survivor",
            "season": season,
            "rules": {"direction": "winner", "basis": "straight_up", "lock_mode": "game_start"},
        },
    ).json()
    entry = client.post(f"/api/v1/pools/{pool['id']}/entries", json={"name": "Main"}).json()
    with SessionLocal() as db:
        provider = AnalysisProvider(
            name=f"Pool test {season}",
            provider_type="openai",
            model="test",
            task_defaults_json='["recommendation"]',
            enabled=True,
        )
        db.add(provider)
        db.commit()
        provider_id = provider.id
    state = {"schedule_calls": 0, "news_calls": 0, "provider_calls": 0}

    async def schedule(db, requested_season, trigger):
        assert requested_season == season
        assert trigger == "pool-check-in"
        state["schedule_calls"] += 1
        if state.get("schedule_failure"):
            raise RuntimeError("upstream failed")
        if state.get("empty_schedule"):
            return {"created": 0, "updated": 0, "status": "completed"}
        for game in db.query(Game).filter(Game.season == season):
            game.source_timestamp = datetime.now(UTC)
            if "probability" in state:
                game.home_win_probability = state["probability"]
        db.add(DataSnapshot(source="nflverse.schedule", source_id=str(season)))
        db.commit()
        return {"created": 0, "updated": 2, "status": "completed"}

    async def news(db, source):
        state["news_calls"] += 1
        if state.get("news_failure"):
            raise RuntimeError("feed failed")
        source.last_fetched_at = datetime.now(UTC)
        db.commit()
        return {"created": 0}

    class Adapter:
        async def analyze(self, question, dossier):
            state["provider_calls"] += 1
            state["dossier"] = dossier
            assert self.output_model is pool_analysis.PoolAnalysisOutput
            if state.get("provider_failure"):
                raise RuntimeError("provider failed")
            if state.get("during_analysis"):
                state["during_analysis"]()
            return {
                "summary": "Review",
                "recommendations": ["Use CHI"],
                "risks": [],
                "missing_information": [],
                "citations": [],
                "picks": state.get(
                    "picks",
                    [{"game_id": games[0]["id"], "team": "CHI", "slot": 1, "confidence": None}],
                ),
            }, {}

    monkeypatch.setattr(pool_analysis, "sync_schedule", schedule)
    monkeypatch.setattr(pool_analysis, "fetch_source", news)
    monkeypatch.setattr(providers, "adapter_for", lambda *_: Adapter())
    base = f"/api/v1/pools/{pool['id']}/weeks/1"
    query = f"?entry_id={entry['id']}"
    return {
        "client": client,
        "state": state,
        "pool": pool,
        "entry": entry,
        "games": games,
        "base": base,
        "query": query,
        "provider_id": provider_id,
    }


def analyze(case):
    response = case["client"].post(
        case["base"] + "/analysis" + case["query"], json={"provider_id": case["provider_id"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


def apply(case, result):
    return case["client"].post(
        case["base"] + "/analysis/apply" + case["query"], json={"run_id": result["run_id"]}
    )


def card(case):
    return case["client"].get(case["base"] + case["query"]).json()["card"]


def test_check_in_refreshes_existing_inputs_and_shares_cooldown(pool_case):
    case, state = pool_case, pool_case["state"]
    state["probability"] = 0.82
    url = case["base"] + "/check-in" + case["query"]
    result = case["client"].post(url)
    assert result.status_code == 200
    assert result.json()["status"] == "ready"
    assert state["schedule_calls"] == 1 and state["news_calls"] > 0
    workspace = case["client"].get(case["base"] + case["query"]).json()
    assert workspace["games"][0]["probabilities"]["home_win"] == 0.82
    case["client"].post(url)
    assert state["schedule_calls"] == 1
    case["client"].post(url + "&force=true")
    assert state["schedule_calls"] == 2


def test_partial_refresh_preserves_card_and_does_not_offer_ai_save(pool_case):
    case = pool_case
    case["state"]["schedule_failure"] = True
    result = analyze(case)
    assert result["freshness"]["status"] == "partial"
    assert result["freshness"]["sources"][0]["status"] == "unavailable"
    assert not result["can_apply"]
    assert card(case)["version"] == 0
    assert apply(case, result).status_code == 409


@pytest.mark.parametrize("kind", ["survivor", "confidence", "loser", "ats"])
def test_preview_then_apply_uses_validated_saved_card(pool_case, kind):
    case = pool_case
    if kind != "survivor":
        pool = case["pool"]
        pool_type = "confidence" if kind == "confidence" else "survivor"
        rules = {
            **pool["rules"],
            "direction": "loser" if kind == "loser" else "winner",
            "basis": "against_spread" if kind == "ats" else "straight_up",
        }
        response = case["client"].put(
            f"/api/v1/pools/{pool['id']}",
            json={
                "name": pool["name"],
                "season": pool["season"],
                "pool_type": pool_type,
                "rules": rules,
            },
        )
        assert response.status_code == 200
        if kind == "confidence":
            case["state"]["picks"] = [
                {"game_id": g["id"], "team": g["home_team"], "confidence": i, "slot": None}
                for i, g in enumerate(case["games"], start=1)
            ]
    result = analyze(case)
    assert result["can_apply"], result
    assert card(case)["picks"] == []  # Reviewing never saves.
    assert case["state"]["dossier"]["freshness"]["status"] == "ready"
    saved = apply(case, result)
    assert saved.status_code == 200, saved.text
    assert saved.json()["card"]["state"] == "complete"
    assert saved.json()["card"]["version"] == 1
    if kind == "ats":
        assert saved.json()["card"]["picks"][0]["spread_home"] == -3.5


@pytest.mark.parametrize("change", ["odds", "card", "kickoff", "expiry"])
def test_changed_evidence_or_card_cannot_be_applied(pool_case, change):
    case = pool_case
    result = analyze(case)
    assert result["can_apply"]
    with SessionLocal() as db:
        if change == "odds":
            db.get(Game, case["games"][0]["id"]).home_win_probability = 0.4
        elif change == "kickoff":
            db.get(Game, case["games"][0]["id"]).kickoff = datetime.now(UTC) - timedelta(minutes=1)
        elif change == "expiry":
            run = db.get(AnalysisRun, result["run_id"])
            dossier = json.loads(run.input_dossier_json)
            dossier["proposal"]["created_at"] = (
                datetime.now(UTC) - timedelta(minutes=6)
            ).isoformat()
            run.input_dossier_json = json.dumps(dossier)
        db.commit()
    if change == "card":
        saved = case["client"].put(
            f"/api/v1/entries/{case['entry']['id']}/weeks/1/picks",
            json={
                "version": 0,
                "picks": [{"game_id": case["games"][0]["id"], "team": "GB", "slot": 1}],
            },
        )
        assert saved.status_code == 200
    response = apply(case, result)
    assert response.status_code == 409, response.text
    assert card(case)["version"] == (1 if change == "card" else 0)


@pytest.mark.parametrize("failure", ["news_failure", "provider_failure"])
def test_source_and_provider_failures_are_explicit(pool_case, failure):
    case = pool_case
    case["state"][failure] = True
    result = analyze(case)
    assert not result["can_apply"]
    assert result["reason"]
    assert card(case)["picks"] == []


@pytest.mark.parametrize(
    "bad_pick",
    [
        {"game_id": -1, "team": "CHI", "slot": 1, "confidence": None},
        {"team": "FAKE", "slot": 1, "confidence": None},
        {"team": "CHI", "slot": 2, "confidence": None},
        {"team": "CHI", "slot": 1, "confidence": 2},
    ],
)
def test_invalid_ai_output_never_becomes_an_action(pool_case, bad_pick):
    case = pool_case
    case["state"]["picks"] = [{"game_id": case["games"][0]["id"], **bad_pick}]
    result = analyze(case)
    assert not result["can_apply"]
    assert apply(case, result).status_code == 409
    assert card(case)["picks"] == []


def test_analysis_cannot_be_applied_to_another_entry(pool_case):
    case = pool_case
    result = analyze(case)
    entry = (
        case["client"]
        .post(f"/api/v1/pools/{case['pool']['id']}/entries", json={"name": "Other"})
        .json()
    )
    response = case["client"].post(
        case["base"] + f"/analysis/apply?entry_id={entry['id']}", json={"run_id": result["run_id"]}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "analysis_scope_mismatch"


def test_source_configuration_change_invalidates_cooldown(pool_case):
    case = pool_case
    url = case["base"] + "/check-in" + case["query"]
    case["client"].post(url)
    with SessionLocal() as db:
        source = NewsSource(name="Extra pool feed", url=f"https://example.com/{case['pool']['id']}")
        db.add(source)
        db.commit()
    case["client"].post(url)
    assert case["state"]["schedule_calls"] == 2


def test_survivor_slot_cannot_be_switched_to_a_started_game(pool_case):
    case = pool_case
    url = f"/api/v1/entries/{case['entry']['id']}/weeks/1/picks"
    first, second = case["games"]
    response = case["client"].put(
        url, json={"version": 0, "picks": [{"game_id": first["id"], "team": "CHI", "slot": 1}]}
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        db.get(Game, second["id"]).kickoff = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    response = case["client"].put(
        url, json={"version": 1, "picks": [{"game_id": second["id"], "team": "DEN", "slot": 1}]}
    )
    assert response.status_code == 409
    assert card(case)["picks"][0]["team"] == "CHI"


def test_empty_schedule_is_not_reported_as_current(pool_case):
    case = pool_case
    case["state"]["empty_schedule"] = True
    result = case["client"].post(case["base"] + "/check-in" + case["query"]).json()
    assert result["status"] == "partial"
    assert result["sources"][0]["status"] == "unavailable"
    assert len(case["client"].get(case["base"] + case["query"]).json()["games"]) == 2


def test_locked_confidence_pick_is_preserved_when_ai_fills_remaining_game(pool_case):
    case = pool_case
    pool = case["pool"]
    case["client"].put(
        f"/api/v1/pools/{pool['id']}",
        json={
            "name": pool["name"],
            "season": pool["season"],
            "pool_type": "confidence",
            "rules": pool["rules"],
        },
    )
    first, second = case["games"]
    saved = case["client"].put(
        f"/api/v1/entries/{case['entry']['id']}/weeks/1/picks",
        json={"version": 0, "picks": [{"game_id": first["id"], "team": "GB", "confidence": 2}]},
    )
    assert saved.status_code == 200
    with SessionLocal() as db:
        db.get(Game, first["id"]).kickoff = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    case["state"]["picks"] = [
        {"game_id": first["id"], "team": "GB", "confidence": 2, "slot": None},
        {"game_id": second["id"], "team": "DEN", "confidence": 1, "slot": None},
    ]
    result = analyze(case)
    assert result["can_apply"], result
    response = apply(case, result)
    assert response.status_code == 200, response.text
    locked = next(p for p in response.json()["card"]["picks"] if p["game_id"] == first["id"])
    assert locked["team"] == "GB" and locked["confidence"] == 2 and locked["locked"]


def test_missing_probability_withholds_actionable_ai_picks(pool_case):
    case = pool_case
    with SessionLocal() as db:
        db.get(Game, case["games"][0]["id"]).win_probability_kind = "unavailable"
        db.commit()
    result = analyze(case)
    assert not result["can_apply"]
    assert "probability" in result["reason"]


def test_refresh_in_progress_is_reported_without_duplicate_collection(pool_case):
    from app.services.job_locks import job_lock

    case = pool_case
    with job_lock("pool-source-refresh") as acquired:
        assert acquired
        response = case["client"].post(case["base"] + "/check-in" + case["query"])
    assert response.json()["status"] == "running"
    assert case["state"]["schedule_calls"] == 0


def test_evidence_changed_while_ai_was_running_is_not_actionable(pool_case):
    case = pool_case

    def change_game():
        with SessionLocal() as db:
            db.get(Game, case["games"][0]["id"]).home_win_probability = 0.4
            db.commit()

    case["state"]["during_analysis"] = change_game
    result = analyze(case)
    assert not result["can_apply"]
    assert "changed during analysis" in result["reason"]


def test_proposal_expiring_during_source_refresh_cannot_save(pool_case, monkeypatch):
    case = pool_case
    result = analyze(case)
    original_refresh = pool_analysis.refresh_pool_sources

    async def slow_refresh(*args, **kwargs):
        freshness = await original_refresh(*args, **kwargs)

        class Later(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(minutes=6)

        monkeypatch.setattr(pool_analysis, "datetime", Later)
        return freshness

    monkeypatch.setattr(pool_analysis, "refresh_pool_sources", slow_refresh)
    response = apply(case, result)
    assert response.status_code == 409
    assert response.json()["error"] == "analysis_expired"
    assert card(case)["picks"] == []
