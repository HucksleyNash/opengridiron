from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from app.db import Base
from app.models import DataSnapshot, Game, League, LeagueAnalysis, Player
from app.services import player_points as service
from app.services.player_points_scoring import score_actual, weekly_actuals
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

NOW = datetime(2026, 9, 20, 16, tzinfo=UTC)
RULES = {"rushing_yards": 0.1}


@pytest.fixture
def workspace(client, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        league = League(
            name="Points League",
            season=2026,
            source="yahoo_scrape",
            yahoo_key="scrape:2026:1",
            scoring_json=json.dumps(RULES),
        )
        db.add(league)
        db.flush()
        player = Player(
            league_id=league.id,
            name="Test Runner",
            pro_team="CHI",
            position="RB",
            source_id="449.p.7",
            status="Active",
        )
        db.add(player)
        games = [
            Game(
                season=2026,
                week=week,
                home_team="CHI",
                away_team=opponent,
                kickoff=NOW + timedelta(days=7 * (week - 2), hours=1),
                source="nflverse",
                completed=week == 1,
            )
            for week, opponent in [(1, "GB"), (2, "ATL"), (3, "BUF")]
        ]
        db.add_all(games)
        db.commit()
        roster = "yahoo_id,gsis_id,full_name,team,position\n7,g1,Test Runner,CHI,RB\n"
        header = (
            "player_id,season,week,season_type,position,team,opponent_team,rushing_yards,carries\n"
        )
        contents = {
            2025: header
            + "".join(f"g1,2025,{week},REG,RB,CHI,GB,100,10\n" for week in range(1, 9)),
            2026: header + "g1,2026,1,REG,RB,CHI,GB,100,10\n",
        }
        sources = [
            {
                "name": "NFL statistics 2026",
                "status": "available",
                "received_at": NOW.isoformat(),
                "sha256": "test-stats",
            }
        ]

        async def inputs(_season):
            return roster, contents, sources.copy()

        monkeypatch.setattr(service, "fetch_weekly_inputs", inputs)
        monkeypatch.setattr(service, "now", lambda: NOW)
        yield db, league, player, games, contents
    engine.dispose()


def snapshot(db, source, source_id, payload, captured):
    row = DataSnapshot(
        source=source,
        source_id=source_id,
        payload_json=json.dumps(payload),
        status="fresh",
        retrieved_at=captured,
    )
    db.add(row)
    db.commit()
    return row


def yahoo(db, league, target_week, points, captured, **context):
    return snapshot(
        db,
        "yahoo_weekly_projection",
        f"{league.id}:2026:{target_week}",
        {
            "players": {"7": {"points": points}},
            "context": {
                "period": "week",
                "season": 2026,
                "week": target_week,
                "scoring": RULES,
                **context,
            },
        },
        captured,
    )


def report(db, league, player, game, points, captured, **overrides):
    forecast = {
        "player_id": player.id,
        "gsis_id": "g1",
        "name": player.name,
        "team": "CHI",
        "points": points,
        "method": "baseline",
        "kickoff": service.utc(game.kickoff).isoformat(),
        "warnings": [],
        **overrides.pop("forecast", {}),
    }
    payload = {
        "generated_at": captured.isoformat(),
        "scoring": RULES,
        "model_version": "test",
        "forecasts": [forecast],
        **overrides.pop("payload", {}),
    }
    run = LeagueAnalysis(
        league_id=league.id,
        season=2026,
        week=game.week,
        team_name="My Team",
        status="completed",
        completed_at=captured,
        report_json=json.dumps(payload),
        **overrides,
    )
    db.add(run)
    db.commit()
    return run


@pytest.mark.asyncio
async def test_full_season_preserves_pregame_sources_actuals_and_caches_new_forecasts(workspace):
    db, league, player, games, _ = workspace
    kickoff = service.utc(games[0].kickoff)
    report(db, league, player, games[0], 8.5, kickoff - timedelta(hours=2))
    report(db, league, player, games[0], 999, kickoff + timedelta(hours=1))
    yahoo(db, league, 1, 9.5, kickoff - timedelta(hours=1))
    yahoo(db, league, 1, 999, kickoff + timedelta(hours=1))
    before = player.projected_points

    result = await service.player_points(db, player)
    assert len(result["weeks"]) == 18
    assert result["league_name"] == league.name
    first, current = result["weeks"][:2]
    assert first["opengridiron"]["points"] == 8.5
    assert first["yahoo"]["points"] == 9.5
    assert first["actual"]["points"] == 10
    assert current["opengridiron"]["points"] == 10
    assert current["actual"]["points"] is None
    captures = list(
        db.scalars(select(DataSnapshot).where(DataSnapshot.source == service.CACHE_SOURCE))
    )
    assert len(captures) == 2
    assert player.projected_points == before
    await service.player_points(db, player)
    assert (
        len(
            list(
                db.scalars(select(DataSnapshot).where(DataSnapshot.source == service.CACHE_SOURCE))
            )
        )
        == 2
    )


@pytest.mark.asyncio
async def test_missing_history_never_becomes_retrospective_projection(workspace):
    db, _, player, _, _ = workspace
    first = (await service.player_points(db, player))["weeks"][0]
    assert first["actual"]["points"] == 10
    assert first["opengridiron"]["state"] == "not_saved"
    assert first["yahoo"]["state"] == "not_saved"


@pytest.mark.asyncio
async def test_refresh_crossing_kickoff_cannot_create_historical_prediction(workspace, monkeypatch):
    db, league, player, games, _ = workspace
    kickoff = service.utc(games[0].kickoff)
    report(
        db,
        league,
        player,
        games[0],
        100,
        kickoff + timedelta(seconds=1),
        payload={"generated_at": (kickoff - timedelta(minutes=1)).isoformat()},
    )
    future = service.utc(games[1].kickoff)

    async def refresh(_db, _league, week):
        assert week == 2
        monkeypatch.setattr(service, "now", lambda: future + timedelta(seconds=1))
        yahoo(db, league, 2, 999, future + timedelta(seconds=1))
        return {}, {"name": "Yahoo weekly comparison", "status": "available"}

    monkeypatch.setattr(service, "refresh_weekly_projections", refresh)
    result = await service.player_points(db, player, refresh=True)
    assert result["weeks"][0]["opengridiron"]["points"] is None
    assert result["weeks"][1]["opengridiron"]["points"] is None
    assert result["weeks"][1]["yahoo"]["points"] is None


@pytest.mark.asyncio
async def test_post_computation_capture_is_checked_against_kickoff(workspace, monkeypatch):
    db, _, player, games, _ = workspace
    original = service.forecast_players

    def slow_forecast(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(service, "now", lambda: service.utc(games[1].kickoff))
        return result

    monkeypatch.setattr(service, "forecast_players", slow_forecast)
    result = await service.player_points(db, player)
    assert result["weeks"][1]["opengridiron"]["points"] is None


@pytest.mark.asyncio
async def test_scoring_warnings_and_yahoo_settings_evidence(workspace):
    db, league, player, games, _ = workspace
    kickoff = service.utc(games[0].kickoff)
    report(
        db,
        league,
        player,
        games[0],
        8,
        kickoff - timedelta(hours=2),
        payload={"scoring": {"rushing_yards": 0.2}},
    )
    yahoo(db, league, 1, 11, kickoff - timedelta(hours=1))
    first = (await service.player_points(db, player))["weeks"][0]
    assert "Scoring changed" in first["opengridiron"]["warnings"][0]
    assert "unverified" in first["yahoo"]["warnings"][0]
    evidence = snapshot(
        db,
        "yahoo_scrape",
        league.yahoo_key,
        {"league": {"season": 2026, "scoring": RULES}},
        kickoff - timedelta(hours=2),
    )
    first = (await service.player_points(db, player))["weeks"][0]
    assert first["yahoo"]["scoring_evidence"]["snapshot_id"] == evidence.id
    assert not first["yahoo"]["warnings"]


@pytest.mark.asyncio
async def test_wrong_identity_week_season_and_source_fallback_are_rejected(workspace):
    db, league, player, games, _ = workspace
    captured = service.utc(games[0].kickoff) - timedelta(hours=2)
    report(db, league, player, games[0], 8, captured, forecast={"gsis_id": "wrong"})
    report(db, league, player, games[0], 9, captured, forecast={"method": "source_weekly_fallback"})
    yahoo(db, league, 1, 500, captured, season=2025)
    yahoo(db, league, 1, 600, captured, week=2)
    first = (await service.player_points(db, player))["weeks"][0]
    assert first["opengridiron"]["points"] is None
    assert first["yahoo"]["points"] is None


@pytest.mark.asyncio
async def test_league_and_player_isolation(workspace):
    db, league, player, games, _ = workspace
    other = League(name="Other", season=2026, scoring_json=json.dumps(RULES))
    db.add(other)
    db.commit()
    captured = service.utc(games[0].kickoff) - timedelta(hours=2)
    report(db, other, player, games[0], 999, captured)
    yahoo(db, other, 1, 999, captured)
    first = (await service.player_points(db, player))["weeks"][0]
    assert first["opengridiron"]["points"] is None
    assert first["yahoo"]["points"] is None
    assert (await service.player_points(db, player))["league_id"] == league.id


@pytest.mark.asyncio
async def test_traded_player_uses_historical_team(workspace):
    db, _, player, _, _ = workspace
    player.pro_team = "BUF"
    db.commit()
    first = (await service.player_points(db, player))["weeks"][0]
    assert first["team"] == "CHI" and first["opponent"] == "GB"
    assert first["actual"]["points"] == 10


@pytest.mark.asyncio
async def test_traded_player_replaces_future_forecast_for_old_team(workspace):
    db, _, player, games, _ = workspace
    await service.player_points(db, player)
    player.pro_team = "BUF"
    new_game = Game(
        season=2026,
        week=2,
        home_team="BUF",
        away_team="MIA",
        source="nflverse",
        kickoff=service.utc(games[1].kickoff) + timedelta(hours=3),
    )
    db.add(new_game)
    db.commit()
    row = (await service.player_points(db, player))["weeks"][1]
    assert row["team"] == "BUF" and row["opponent"] == "MIA"
    assert row["kickoff"] == service.utc(new_game.kickoff).isoformat()
    saved = db.scalar(
        select(DataSnapshot)
        .where(
            DataSnapshot.source == service.CACHE_SOURCE,
            DataSnapshot.source_id == f"{player.league_id}:{player.id}:2026:2",
        )
        .order_by(DataSnapshot.id.desc())
    )
    payload = json.loads(saved.payload_json)
    assert payload["team"] == "BUF" and payload["kickoff"] == row["kickoff"]


@pytest.mark.asyncio
async def test_status_zero_is_not_extended_to_future_weeks_and_byes_are_explicit(workspace):
    db, _, player, _, _ = workspace
    player.status = "Out"
    for week in range(4, 19):
        if week != 10:
            db.add(
                Game(
                    season=2026,
                    week=week,
                    home_team="CHI",
                    away_team="GB",
                    source="nflverse",
                    kickoff=NOW + timedelta(days=7 * (week - 2)),
                )
            )
    db.commit()
    rows = (await service.player_points(db, player))["weeks"]
    assert rows[1]["opengridiron"]["points"] == 0
    assert "assumes this status" in rows[1]["opengridiron"]["warnings"][0]
    assert rows[2]["opengridiron"]["points"] is None
    assert "Availability unresolved" in rows[2]["opengridiron"]["reason"]
    assert rows[9]["state"] == "bye"
    assert all(rows[9][key]["state"] == "bye" for key in ["actual", "yahoo", "opengridiron"])


@pytest.mark.asyncio
async def test_missing_sources_and_manual_league_remain_explicit(workspace, monkeypatch):
    db, league, player, _, _ = workspace
    league.source = "manual"
    db.commit()

    async def unavailable(_season):
        return "", {}, [{"name": "NFL statistics 2026", "status": "unavailable"}]

    monkeypatch.setattr(service, "fetch_weekly_inputs", unavailable)
    result = await service.player_points(db, player)
    assert all(row["actual"]["points"] is None for row in result["weeks"])
    assert all(row["yahoo"]["points"] is None for row in result["weeks"])
    assert "not connected" in result["weeks"][0]["yahoo"]["reason"]
    assert result["sources"][0]["status"] == "unavailable"


@pytest.mark.parametrize("value,expected", [("0", 0), ("-10", -1), ("100", 10)])
def test_actual_zero_negative_and_positive(value, expected):
    assert score_actual({"rushing_yards": value}, RULES, "RB")["points"] == expected


@pytest.mark.parametrize("value", [None, "", "NA", "nan", "inf", "garbage"])
def test_missing_required_stat_is_never_zero(value):
    result = score_actual({"rushing_yards": value}, RULES, "RB")
    assert result["points"] is None and "Missing scoring stats" in result["reason"]


def test_scoring_coverage_and_kicker_bins():
    assert score_actual({"rushing_yards": "100"}, {}, "RB")["points"] is None
    assert (
        score_actual({"rushing_yards": "100"}, {**RULES, "long_td_bonus": 3}, "RB")["points"]
        is None
    )
    rules = {"field_goals_50_yards": 5, "point_after_attempt_made": 1}
    assert score_actual({"fg_made_50_59": "1", "pat_made": "2"}, rules, "K")["points"] is None
    assert (
        score_actual({"fg_made_50_59": "1", "fg_made_60_": "0", "pat_made": "2"}, rules, "K")[
            "points"
        ]
        == 7
    )
    assert (
        score_actual({"def_sacks": "2"}, {"sacks": 1, "forced_fumble": 2}, "DEF")["points"] is None
    )
    assert score_actual({"def_sacks": "2"}, {"sacks": 1}, "DEF")["points"] == 2


def test_actual_identity_period_and_conflicts():
    header = "player_id,season,week,season_type,team,rushing_yards\n"
    contents = {
        2026: header
        + "x,2026,1,REG,CHI,10\nx,2026,1,REG,BUF,20\nx,2025,2,REG,CHI,90\nx,2026,3,POST,CHI,90\n"
    }
    rows = weekly_actuals(contents, 2026, "x", "RB", RULES)
    assert list(rows) == [1]
    assert rows[1]["points"] is None and "Conflicting" in rows[1]["reason"]
    assert weekly_actuals(contents, 2026, "different", "RB", RULES) == {}


@pytest.mark.asyncio
async def test_report_and_yahoo_reads_extract_only_relevant_payload(workspace):
    db, league, player, games, _ = workspace
    captured = service.utc(games[0].kickoff) - timedelta(hours=2)
    report(db, league, player, games[0], 8, captured, payload={"unused": "x" * 500_000})
    yahoo(db, league, 1, 9, captured)
    statements = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        await service.player_points(db, player)
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert not any("SELECT league_analyses.report_json" in sql for sql in statements)
    assert any("json_each(league_analyses.report_json" in sql for sql in statements)
    assert not any(
        "data_snapshots.payload_json" in sql.split("FROM")[0] and "ORDER BY" in sql
        for sql in statements
    )


def test_missing_player_api(client):
    assert client.get("/api/v1/players/9999999/points").status_code == 404


def test_points_api_uses_selected_league_scoring_and_serializes_zero_actuals(client, workspace):
    from app.db import get_db
    from app.main import app

    db, league, player, _, contents = workspace
    league.source = "manual"
    db.commit()
    contents[2026] = contents[2026].replace(",GB,100,10", ",GB,0,10")
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = client.get(f"/api/v1/players/{player.id}/points?refresh=true")
    finally:
        del app.dependency_overrides[get_db]
    assert response.status_code == 200
    data = response.json()
    assert (data["player_id"], data["league_id"], data["season"]) == (player.id, league.id, 2026)
    assert data["scoring"] == RULES and len(data["weeks"]) == 18
    assert data["weeks"][0]["actual"]["points"] == 0
    assert data["weeks"][0]["opengridiron"]["points"] is None
    assert data["weeks"][0]["yahoo"]["state"] == "unavailable"
