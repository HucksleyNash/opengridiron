from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.db import SessionLocal
from app.models import AnalysisProvider, Game, League, LeagueAnalysis, Player
from app.services import league_analysis as jobs
from app.services.weekly_forecast import (
    assign_lineup,
    build_weekly_report,
    forecast_players,
    match_identities,
    parse_weekly_stats,
    team_decisions,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def stats_csv(values: dict[str, float], season=2025) -> str:
    return (
        "player_id,season,week,season_type,passing_yards,rushing_yards,receptions,attempts,carries,targets,team\n"
        + "\n".join(
            f"{identity},{season},{week},REG,0,{points * 10},0,0,10,2,CHI"
            for identity, points in values.items()
            for week in range(1, 9)
        )
    )


def fixture_data():
    league = League(
        id=1,
        name="Test",
        season=2026,
        scoring_json='{"rushing_yards":0.1}',
        roster_slots_json='["RB","FLEX","K"]',
    )
    players = [
        Player(
            id=i,
            league_id=1,
            name=name,
            pro_team="CHI",
            position=pos,
            source_id=f"yahoo:{i}",
            rostered_by=owner,
            ownership="FA" if owner is None else "owned",
            current_slot=slot,
            status="Active",
            projected_points=300 + i,
            projection_context_json='{"period":"season","season":2026,"source":"Yahoo"}',
        )
        for i, name, pos, owner, slot in [
            (1, "Starter", "RB", "Mine", "RB"),
            (2, "Flex", "RB", "Mine", "FLEX"),
            (3, "Bench", "RB", "Mine", "BN"),
            (4, "Available", "RB", None, None),
            (5, "Rival", "RB", "Rival", "RB"),
            (6, "Kicker", "K", "Mine", "K"),
        ]
    ]
    games = [
        Game(
            id=1,
            season=2026,
            week=1,
            home_team="CHI",
            away_team="GB",
            kickoff=NOW + timedelta(days=10),
        )
    ]
    stats = parse_weekly_stats(
        {2025: stats_csv({"g1": 5, "g2": 10, "g3": 15, "g4": 20, "g5": 100})},
        {"rushing_yards": 0.1},
    )
    identities = {i: f"g{i}" for i in range(1, 7)}
    return league, players, games, stats, identities


def test_independent_forecast_does_not_use_yahoo_and_preserves_period():
    league, players, games, stats, identities = fixture_data()
    report = build_weekly_report(league, players, games, 1, "Mine", stats, identities, NOW)
    first = report["forecasts"][0]
    assert first["points"] == 5
    assert first["difference"] is None
    assert first["source_projection"]["period"] == "season"
    assert report["lineup"]["gain"] == 10
    assert report["lineup"]["partial_total"] is True
    assert report["lineup"]["waivers"][0]["add"] == "Available"
    assert report["lineup"]["waivers"][0]["drop_id"] is None
    assert "Starter" in {p["name"] for p in report["lineup"]["waivers"][0]["drop_candidates"]}
    assert report["lineup"]["waivers"][0]["gain"] == 10
    assert all(a["name"] != "Rival" for a in report["lineup"]["assignments"])
    players[0].projected_points = 9999
    assert forecast_players(league, players, games, 1, stats, identities, NOW)[0]["points"] == 5
    assert players[1].projected_points == 302


def test_future_samples_cannot_leak_into_forecast_and_ppr_is_scored():
    league, players, games, stats, identities = fixture_data()
    stats["g1"].append({**stats["g1"][-1], "season": 2026, "week": 1, "points": 999})
    assert forecast_players(league, players, games, 1, stats, identities, NOW)[0]["points"] == 5
    contents = (
        "player_id,season,week,season_type,receptions,receiving_yards\n"
        "x,2025,1,REG,5,60\nx,2025,2,POST,20,200"
    )
    rows = parse_weekly_stats({2025: contents}, {"receptions": 1, "receiving_yards": 0.1})
    assert len(rows["x"]) == 1 and rows["x"][0]["points"] == 11


def test_locked_and_unmodeled_starters_are_never_displaced():
    league, players, games, stats, identities = fixture_data()
    rows = forecast_players(league, players, games, 1, stats, identities, NOW)
    rows[0]["locked"] = True
    decisions = team_decisions(rows, ["RB", "FLEX", "K"], "Mine")
    assert any(a["name"] == "Starter" and a["action"] == "Hold" for a in decisions["assignments"])
    assert any(a["name"] == "Kicker" and a["action"] == "Hold" for a in decisions["assignments"])
    assert all(move["drop"] != "Starter" for move in decisions["waivers"])
    rows[2]["locked"] = True
    assert all(
        a["name"] != "Bench"
        for a in team_decisions(rows, ["RB", "FLEX", "K"], "Mine")["assignments"]
    )


def test_missing_history_unknown_scoring_and_no_game_are_not_zero():
    league, players, games, stats, identities = fixture_data()
    assert forecast_players(league, players, [], 1, stats, identities, NOW)[0]["points"] is None
    assert forecast_players(league, players, games, 1, {}, identities, NOW)[0]["points"] is None
    league.scoring_json = '{"rushing_yards":0.1,"long_td_bonus":3}'
    assert (
        "Unsupported scoring"
        in forecast_players(league, players, games, 1, stats, identities, NOW)[0]["reason"]
    )


def test_weekly_comparison_requires_matching_scoring():
    league, players, games, stats, identities = fixture_data()
    context = {
        "source": "Yahoo",
        "period": "week",
        "season": 2026,
        "week": 1,
        "scoring_basis": "league_rules",
        "scoring": {"rushing_yards": 0.1},
    }
    players[0].projection_context_json = json.dumps(context)
    row = forecast_players(league, players, games, 1, stats, identities, NOW)[0]
    assert row["difference"] == -296
    context["scoring"] = {"rushing_yards": 0.2}
    players[0].projection_context_json = json.dumps(context)
    assert (
        forecast_players(league, players, games, 1, stats, identities, NOW)[0]["difference"] is None
    )


def test_identity_uses_yahoo_id_and_rejects_ambiguity():
    _, players, _, _, _ = fixture_data()
    csv = (
        "yahoo_id,gsis_id,full_name,team,position\n"
        "1,g1,Starter,CHI,RB\n2,g2,Same,CHI,RB\n2,other,Same,CHI,RB"
    )
    assert match_identities(players, csv) == {1: "g1"}


def test_assignment_fills_negative_slots_and_handles_flex():
    players = [
        {"player_id": 1, "position": "RB", "points": -2, "locked": False, "current_slot": "RB"},
        {"player_id": 2, "position": "WR", "points": 20, "locked": False, "current_slot": "BN"},
    ]
    lineup = assign_lineup(players, ["RB", "FLEX"], {})
    assert lineup[0]["player_id"] == 1 and lineup[1]["player_id"] == 2


@pytest.fixture
def saved_league(client, monkeypatch):
    suffix = uuid4().hex
    with SessionLocal() as db:
        league = League(
            name=suffix,
            season=2026,
            scoring_json='{"rushing_yards":0.1}',
            roster_slots_json='["RB"]',
        )
        db.add(league)
        db.flush()
        for i, slot, owner in [(1, "RB", "Mine"), (2, "BN", "Mine"), (3, None, None)]:
            db.add(
                Player(
                    league_id=league.id,
                    name=f"{suffix}-{i}",
                    source_id=f"yahoo:{i}",
                    pro_team="CHI",
                    position="RB",
                    status="Active",
                    rostered_by=owner,
                    current_slot=slot,
                    ownership="FA" if owner is None else "owned",
                    projected_points=300,
                )
            )
        # A unique week prevents fixtures from clashing with existing schedule tests.
        game = Game(
            season=2026,
            week=17,
            home_team=f"A{suffix[:5]}",
            away_team="CHI",
            kickoff=datetime.now(UTC) + timedelta(days=100),
        )
        db.add(game)
        provider = AnalysisProvider(
            name=suffix, provider_type="codex", model="test", enabled=True, task_defaults_json="[]"
        )
        db.add(provider)
        db.commit()
        league_id, provider_id = league.id, provider.id

    async def refresh(db, league):
        return [{"name": "Test sources", "status": "refreshed"}]

    async def inputs(season):
        roster = "yahoo_id,gsis_id,full_name,team,position\n" + "\n".join(
            f"{i},g{i},{suffix}-{i},CHI,RB" for i in (1, 2, 3)
        )
        return roster, {2025: stats_csv({"g1": 5, "g2": 10, "g3": 20})}, []

    class Adapter:
        async def analyze(self, question, dossier):
            assert "weekly_report" in dossier
            assert len(dossier["weekly_report"]["forecasts"]) == 3
            return {
                "summary": "A supported improvement.",
                "recommendations": ["Review the suggested swap."],
                "risks": [],
                "missing_information": [],
                "citations": [],
            }, {}

    monkeypatch.setattr(jobs, "refresh_sources", refresh)
    monkeypatch.setattr(jobs, "fetch_weekly_inputs", inputs)
    monkeypatch.setattr("app.services.providers.adapter_for", lambda *a: Adapter())
    return league_id, provider_id


def test_run_persists_report_history_ai_and_unchanged_imports(client, saved_league):
    league_id, provider_id = saved_league
    response = client.post(
        f"/api/v1/leagues/{league_id}/analyses",
        json={"team_name": "Mine", "week": 17, "provider_id": provider_id},
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    detail = client.get(f"/api/v1/leagues/{league_id}/analyses/{run_id}").json()
    assert detail["status"] == "completed", detail
    report = detail["report"]
    assert report["lineup"]["gain"] == 5
    assert report["analysis"]["output"]["summary"] == "A supported improvement."
    assert len(client.get(f"/api/v1/leagues/{league_id}/analyses?team_name=Mine").json()) == 1
    assert client.get(f"/api/v1/leagues/{league_id}/analyses?team_name=Other").json() == []
    with SessionLocal() as db:
        player = db.query(Player).filter_by(league_id=league_id).first()
        assert player.projected_points == 300
        player.status = "Out"
        db.commit()
    assert (
        "changed"
        in client.get(f"/api/v1/leagues/{league_id}/analyses/{run_id}").json()["stale_reasons"][0]
    )
    again = client.post(
        f"/api/v1/leagues/{league_id}/analyses",
        json={"team_name": "Mine", "week": 17, "provider_id": provider_id},
    ).json()
    updated = client.get(f"/api/v1/leagues/{league_id}/analyses/{again['id']}").json()
    assert any("status changed" in c for c in updated["report"]["changes"])


def test_provider_failure_keeps_forecasts_and_records_failure(client, saved_league, monkeypatch):
    league_id, provider_id = saved_league

    class Broken:
        async def analyze(self, *a):
            raise RuntimeError("simulated provider failure")

    monkeypatch.setattr("app.services.providers.adapter_for", lambda *a: Broken())
    run = client.post(
        f"/api/v1/leagues/{league_id}/analyses",
        json={"team_name": "Mine", "week": 17, "provider_id": provider_id},
    ).json()
    detail = client.get(f"/api/v1/leagues/{league_id}/analyses/{run['id']}").json()
    assert detail["status"] == "partial"
    assert detail["report"]["forecasts"][0]["points"] == 5
    assert detail["report"]["analysis"]["status"] == "failed"


def test_validation_active_deduplication_and_restart_recovery(client, saved_league, monkeypatch):
    league_id, provider_id = saved_league
    assert (
        client.post(
            f"/api/v1/leagues/{league_id}/analyses", json={"team_name": "Other", "week": 17}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/leagues/{league_id}/analyses", json={"team_name": "Mine", "week": 19}
        ).status_code
        == 422
    )
    with SessionLocal() as db:
        run = LeagueAnalysis(
            league_id=league_id,
            team_name="Mine",
            season=2026,
            week=17,
            status="queued",
            provider_id=provider_id,
        )
        db.add(run)
        db.commit()
        run_id = run.id
    duplicate = client.post(
        f"/api/v1/leagues/{league_id}/analyses", json={"team_name": "Mine", "week": 17}
    ).json()
    assert duplicate["id"] == run_id
    assert (
        client.post(
            f"/api/v1/leagues/{league_id}/analyses", json={"team_name": "Mine", "week": 16}
        ).status_code
        == 409
    )
    with SessionLocal() as db:
        jobs.recover_league_analyses(db)
        assert db.get(LeagueAnalysis, run_id).status == "failed"


def test_yahoo_defensive_interception_does_not_overwrite_passing_penalty():
    from app.services.weekly_forecast import scoring_rules

    scoring = {
        "passing_yards_yahoo_default": 0.02,
        "passing_touchdowns_yahoo_default": 6,
        "interceptions_yahoo_default": -2,
        "interception": 2,
        "sack": 1,
        "block_kick": 2,
        "kickoff_and_punt_return_touchdowns": 6,
        "extra_point_returned": 2,
        "points_allowed_0_points": 10,
    }
    rules, unsupported = scoring_rules(scoring)
    assert rules["interceptions"] == -2
    assert unsupported == []
    content = (
        "player_id,season,week,passing_yards,passing_tds,passing_interceptions,carries,targets\n"
        "qb,2025,1,200,2,1,NA,NA"
    )
    assert parse_weekly_stats({2025: content}, scoring)["qb"][0]["points"] == 14


def test_total_fumbles_include_return_fumbles():
    content = "player_id,season,week,fumbles_lost_total,receiving_fumbles_lost\nreturner,2025,1,2,1"
    assert parse_weekly_stats({2025: content}, {"fumbles_lost": -2})["returner"][0]["points"] == -4


def test_bye_is_zero_only_with_a_complete_verified_schedule():
    league, players, games, stats, identities = fixture_data()
    # Complete official schedule has CHI games in other weeks, but no week-one game.
    complete = [
        Game(
            id=i,
            season=2026,
            week=2 + i % 17,
            home_team="CHI",
            away_team="GB",
            source="nflverse",
            kickoff=NOW + timedelta(days=20),
        )
        for i in range(270)
    ]
    complete.append(
        Game(
            id=271,
            season=2026,
            week=1,
            home_team="SEA",
            away_team="NYJ",
            source="nflverse",
            kickoff=NOW + timedelta(days=10),
        )
    )
    row = forecast_players(league, players, complete, 1, stats, identities, NOW)[0]
    assert row["points"] == 0
    assert "Bye week" in row["warnings"][0]
    assert row["bye"] is True
    assert row["kickoff"] is None
    incomplete = forecast_players(league, players, complete[:1], 1, stats, identities, NOW)[0]
    assert incomplete["points"] is None
    assert incomplete["bye"] is False
    scheduled = forecast_players(league, players, games, 1, stats, identities, NOW)[0]
    assert scheduled["bye"] is False
    assert scheduled["kickoff"] == games[0].kickoff.isoformat()


def test_source_outage_saves_explicit_partial_report(client, saved_league, monkeypatch):
    league_id, provider_id = saved_league

    async def missing_inputs(season):
        return "", {}, [{"name": "NFL statistics 2025", "status": "unavailable"}]

    monkeypatch.setattr(jobs, "fetch_weekly_inputs", missing_inputs)
    run = client.post(
        f"/api/v1/leagues/{league_id}/analyses",
        json={"team_name": "Mine", "week": 17, "provider_id": provider_id},
    ).json()
    detail = client.get(f"/api/v1/leagues/{league_id}/analyses/{run['id']}").json()
    assert detail["status"] == "partial"
    assert detail["report"]["coverage"]["modeled"] == 0
    assert all(p["points"] is None for p in detail["report"]["forecasts"])


def test_evaluation_excludes_postkickoff_runs_and_deduplicates(client, saved_league):
    league_id, _ = saved_league
    fixture_league, players, games, stats, identities = fixture_data()
    report = build_weekly_report(fixture_league, players, games, 1, "Mine", stats, identities, NOW)
    with SessionLocal() as db:
        # Latest post-kickoff report cannot overwrite an eligible pregame forecast.
        for generated in (NOW, NOW + timedelta(hours=1), NOW + timedelta(days=20)):
            saved = {**report, "generated_at": generated.isoformat()}
            db.add(
                LeagueAnalysis(
                    league_id=league_id,
                    team_name="Mine",
                    season=2026,
                    week=1,
                    status="completed",
                    report_json=json.dumps(saved),
                )
            )
        db.commit()
        result = jobs.evaluate_history(db, league_id, {2026: stats_csv({"g1": 7}, season=2026)})
    assert result["scored_forecasts"] == 1
    assert result["mae"] == 2
    assert result["comparison_count"] == 0


def test_arbitrary_import_id_is_not_treated_as_a_yahoo_identity():
    _, players, _, _, _ = fixture_data()
    players[0].source_id = "manual-player-1"
    roster = "yahoo_id,gsis_id,full_name,team,position\n1,wrong,Someone Else,CHI,RB"
    assert players[0].id not in match_identities(players, roster)
