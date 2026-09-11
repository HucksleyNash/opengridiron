import copy
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.db import SessionLocal
from app.models import AnalysisProvider, AnalysisRun, Game, League, Player
from app.services import football_sources, player_synopsis
from app.services import injury_report as service
from test_player_synopsis import INJURIES, news_feed


@pytest.fixture
def injury_data(client, monkeypatch):
    now = datetime.now(UTC)
    season = service.nfl_season_for_date(now)
    state = {"html": INJURIES.replace("2026", str(season)), "fail": False, "dossiers": []}
    player_synopsis._cache.clear()
    real_client = httpx.AsyncClient

    def handle(request):
        if state["fail"]:
            return httpx.Response(503)
        return httpx.Response(
            200, text=state["html"] if request.url.host == "www.nfl.com" else news_feed()
        )

    monkeypatch.setattr(
        player_synopsis.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )

    async def refresh(request):
        return {}

    monkeypatch.setattr(football_sources, "refresh_source", refresh)
    monkeypatch.setattr(
        football_sources,
        "source_evidence",
        lambda db, request: {
            "rows": [
                {
                    "name": "Supplemental Player",
                    "team": "BUF",
                    "position": "WR",
                    "status": "Active",
                    "injury_status": "Questionable",
                    "injury_body_part": "Knee",
                    "practice_participation": "Limited",
                }
            ],
            "name": "Sleeper player status",
            "url": football_sources.SLEEPER_URL,
            "status": "available",
            "received_at": now.isoformat(),
        },
    )
    with SessionLocal() as db:
        first = League(name="Injury alpha", season=season, my_team_name="My A")
        second = League(name="Injury beta", season=season, my_team_name="My B")
        old = League(name="Old injury league", season=season - 1, my_team_name="My A")
        unset = League(name="Choose injury team", season=season)
        db.add_all([first, second, old, unset])
        db.flush()
        players = [
            Player(
                league_id=first.id,
                name="Patrick Mahomes",
                pro_team="KC",
                position="QB",
                status="Q",
                rostered_by="My A",
                current_slot="QB",
            ),
            Player(
                league_id=second.id,
                name="Patrick Mahomes",
                pro_team="KC",
                position="QB",
                status="Active",
                rostered_by="Rival",
            ),
            Player(
                league_id=first.id,
                name="Reserve Player",
                pro_team="BUF",
                position="RB",
                status="IR",
                rostered_by="My A",
            ),
            Player(
                league_id=first.id,
                name="Healthy Player",
                pro_team="BUF",
                position="QB",
                status="Active",
                rostered_by="My A",
            ),
            Player(
                league_id=old.id, name="Old Reserve", pro_team="BUF", position="WR", status="IR"
            ),
            Player(
                league_id=second.id,
                name="Patrick Mahomes",
                pro_team="BUF",
                position="QB",
                status="Q",
                rostered_by="My B",
            ),
        ]
        provider = AnalysisProvider(name="Injury test", provider_type="openai", model="test")
        game = Game(
            season=season, week=1, away_team="KC", home_team="BUF", kickoff=now + timedelta(days=2)
        )
        db.add_all([*players, provider, game])
        db.commit()
        state.update(
            first=first.id,
            second=second.id,
            provider=provider.id,
            player=players[0].id,
            game=game.id,
            leagues=[first.id, second.id, old.id, unset.id],
        )
    yield state
    with SessionLocal() as db:
        db.query(AnalysisRun).filter(AnalysisRun.task == service.TASK).delete()
        for identifier in state["leagues"]:
            db.delete(db.get(League, identifier))
        db.delete(db.get(AnalysisProvider, state["provider"]))
        db.delete(db.get(Game, state["game"]))
        db.commit()
    player_synopsis._cache.clear()


def player_key():
    return service.identity("Patrick Mahomes", "KC", "QB")


def fake_assessment():
    finding = {
        "text": "Limited practice leaves workload uncertain.",
        "evidence_ids": ["official-0"],
    }
    return {
        "summary": "Monitor the final practice report.",
        "outlook": "game_time_decision",
        "confidence": "medium",
        "availability": copy.deepcopy(finding),
        "workload": copy.deepcopy(finding),
        "fantasy_advice": copy.deepcopy(finding),
        "next_update": "Check the final official injury report.",
        "missing_information": [],
    }


def install_adapter(monkeypatch, state, output=None, fail=False):
    class Adapter:
        async def analyze(self, question, dossier):
            state["dossiers"].append(dossier)
            if fail:
                raise RuntimeError("secret-token-provider-error")
            return output or fake_assessment(), {"input_tokens": 20, "output_tokens": 10}

    monkeypatch.setattr(service.providers, "adapter_for", lambda db, provider: Adapter())


def run_check(client, state):
    response = client.post(
        f"/api/v1/injuries/{player_key()}/checks", json={"provider_id": state["provider"]}
    )
    assert response.status_code == 202, response.text
    return client.get(f"/api/v1/injuries/checks/{response.json()['id']}").json()


def test_board_merges_leagues_without_crossing_team_or_season(client, injury_data):
    response = client.get("/api/v1/injuries")
    assert response.status_code == 200, response.text
    result = response.json()
    row = next(r for r in result["items"] if r["key"] == player_key())
    assert row["injury"] == "Ankle" and row["status_source"] == "NFL"
    assert len(row["memberships"]) == 2 and row["is_mine"]
    assert row["next_game"]["id"] == injury_data["game"]
    names = {r["name"] for r in result["items"]}
    assert {"Reserve Player", "Supplemental Player", "Another Player"} <= names
    assert "Healthy Player" not in names and "Old Reserve" not in names
    assert len([r for r in result["items"] if r["name"] == "Patrick Mahomes"]) == 2
    assert "QB" in result["facets"]["positions"]
    scoped = client.get(
        "/api/v1/injuries", params={"mine": True, "league_id": injury_data["second"]}
    ).json()["items"]
    assert len(scoped) == 1 and scoped[0]["team"] == "BUF"
    assert (
        client.get(
            "/api/v1/injuries",
            params={"search": "ankle", "team": "KC", "status": "Questionable", "position": "QB"},
        ).json()["total"]
        == 1
    )


def test_official_only_detail_and_failure_preserve_imported_rows(client, injury_data):
    key = service.identity("Another Player", "KC", "WR")
    detail = client.get(f"/api/v1/injuries/{key}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["official_reports"][0]["game_status"] == "Out"
    assert not detail.json()["memberships"]
    for item in player_synopsis._cache.values():
        item["checked_at"] -= timedelta(minutes=11)
    injury_data["fail"] = True
    stale = client.get("/api/v1/injuries").json()
    assert stale["sources"][0]["status"] == "stale"
    assert any(row["key"] == key for row in stale["items"])
    player_synopsis._cache.clear()
    failed = client.get("/api/v1/injuries").json()
    assert failed["sources"][0]["status"] == "unavailable"
    row = next(r for r in failed["items"] if r["key"] == player_key())
    assert row["status_source"] == "Roster import" and not row["official_reports"]


def test_check_collects_evidence_saves_result_and_keeps_import_unchanged(
    client, injury_data, monkeypatch
):
    install_adapter(monkeypatch, injury_data)
    check = run_check(client, injury_data)
    assert check["status"] == "completed", check
    assert check["target_game"]["id"] == injury_data["game"]
    assert check["output"]["outlook"] == "game_time_decision"
    assert check["output"]["citations"] == [player_synopsis.NFL_INJURIES_URL]
    dossier = injury_data["dossiers"][0]
    assert dossier["can_assess"] and any(e["kind"] == "headline_only" for e in dossier["evidence"])
    assert all(e["url"].startswith("https:") for e in dossier["evidence"])
    history = client.get(f"/api/v1/injuries/{player_key()}/checks").json()
    assert history[0]["id"] == check["id"]
    with SessionLocal() as db:
        assert db.get(Player, injury_data["player"]).status == "Q"


@pytest.mark.parametrize("failure", ["invented_citation", "provider"])
def test_check_failures_are_saved_without_exposing_provider_secrets(
    client, injury_data, monkeypatch, failure
):
    output = fake_assessment()
    if failure == "invented_citation":
        output["availability"]["evidence_ids"] = ["invented-source"]
    install_adapter(monkeypatch, injury_data, output, fail=failure == "provider")
    check = run_check(client, injury_data)
    assert check["status"] == "failed" and check["output"] is None
    assert "secret-token" not in check["error"]


@pytest.mark.parametrize("missing", ["wrong_week", "wrong_season", "no_game", "stale"])
def test_unknown_is_enforced_when_target_evidence_cannot_be_verified(
    client, injury_data, monkeypatch, missing
):
    if missing == "wrong_week":
        injury_data["html"] = injury_data["html"].replace("WEEK 1", "WEEK 18")
    elif missing == "wrong_season":
        injury_data["html"] = injury_data["html"].replace(str(datetime.now(UTC).year), "2024")
    elif missing == "no_game":
        with SessionLocal() as db:
            db.get(Game, injury_data["game"]).completed = True
            db.commit()
    else:
        client.get("/api/v1/injuries")
        for item in player_synopsis._cache.values():
            item["checked_at"] -= timedelta(minutes=11)
        injury_data["fail"] = True
    output = fake_assessment()
    output.update(outlook="likely_to_play", confidence="high", summary="Definitely start him")
    install_adapter(monkeypatch, injury_data, output)
    check = run_check(client, injury_data)
    assert check["status"] == "completed", check
    assert check["output"]["outlook"] == "unknown" and check["output"]["confidence"] == "low"
    assert "Definitely" not in check["output"]["summary"]


def test_official_out_overrides_model_optimism(client, injury_data, monkeypatch):
    injury_data["html"] = injury_data["html"].replace("Questionable", "Out")
    output = fake_assessment()
    output.update(outlook="likely_to_play", summary="Start him")
    install_adapter(monkeypatch, injury_data, output)
    check = run_check(client, injury_data)
    assert check["output"]["outlook"] == "ruled_out"
    assert "replacement" in check["output"]["fantasy_advice"]["text"]


def test_duplicate_active_checks_and_recovery(client, injury_data, monkeypatch):
    async def noop(run_id):
        pass

    monkeypatch.setattr(service, "execute_check", noop)
    first = run_check(client, injury_data)
    second = run_check(client, injury_data)
    assert first["id"] == second["id"] and first["status"] == "queued"
    with SessionLocal() as db:
        service.recover_injury_checks(db)
        run = db.get(AnalysisRun, first["id"])
        assert run.status == "failed" and "restarted" in run.error
        run.created_at = datetime.now(UTC) - timedelta(hours=7)
        db.commit()
        assert service.check_out(run)["stale"]


def test_provider_and_player_validation(client, injury_data):
    assert (
        client.post(
            f"/api/v1/injuries/{player_key()}/checks", json={"provider_id": 999999}
        ).status_code
        == 422
    )
    assert client.get("/api/v1/injuries/not-a-player").status_code == 404
    assert client.get("/api/v1/injuries/checks/999999").status_code == 404


def test_identity_requires_team_and_position_and_handles_suffixes():
    assert service.identity("John Doe Jr.", "Chiefs", "QB") == service.identity(
        "John Doe", "KC", "QB"
    )
    assert service.identity("John Doe", "KC", "QB") != service.identity("John Doe", "KC", "WR")
    assert service.nfl_season_for_date(datetime(2027, 1, 15, tzinfo=UTC)) == 2026
