from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _alembic(database: Path, *arguments: str) -> None:
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "APP_SECRET": "migration-test-secret",
        "DATABASE_URL": f"sqlite:///{database}",
        "DATA_DIR": str(database.parent / "migration-data"),
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", *arguments],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def test_sleeper_migration_preserves_manual_pools_and_enforces_identity(tmp_path: Path) -> None:
    import pytest

    database = tmp_path / "sleeper.sqlite3"
    _alembic(database, "upgrade", "0011_ai_context")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO pools (id, name, pool_type, season, rules_json, created_at) "
            "VALUES (1, 'Manual pool', 'survivor', 2026, '{}', '2026-09-08')"
        )
        connection.execute(
            "INSERT INTO pool_entries (id, pool_id, name, active) VALUES (1, 1, 'Manual', 1)"
        )
        connection.execute(
            "INSERT INTO pool_picks (entry_id, week, slot, team) VALUES (1, 1, 1, 'BUF')"
        )
    _alembic(database, "upgrade", "head")
    _alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name, sleeper_league_id, sleeper_snapshot_json FROM pools WHERE id = 1"
        ).fetchone() == ("Manual pool", None, "{}")
        connection.execute("UPDATE pools SET sleeper_league_id = '123' WHERE id = 1")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO pools "
                "(name, pool_type, season, rules_json, created_at, sleeper_league_id) "
                "VALUES ('Duplicate', 'survivor', 2026, '{}', '2026-09-08', '123')"
            )
        connection.execute("UPDATE pool_entries SET sleeper_roster_id = 30 WHERE id = 1")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO pool_entries (pool_id, name, active, sleeper_roster_id) "
                "VALUES (1, 'Duplicate', 1, 30)"
            )
    _alembic(database, "downgrade", "0011_ai_context")
    with sqlite3.connect(database) as connection:
        assert "sleeper_league_id" not in _columns(connection, "pools")
        assert "sleeper_roster_id" not in _columns(connection, "pool_entries")
        assert connection.execute("SELECT name FROM pools").fetchone() == ("Manual pool",)
        assert connection.execute("SELECT team FROM pool_picks").fetchone() == ("BUF",)


def test_my_team_migration_preserves_existing_leagues_and_rosters(tmp_path: Path) -> None:
    database = tmp_path / "my-team.sqlite3"
    _alembic(database, "upgrade", "head")
    _alembic(database, "downgrade", "0008")
    with sqlite3.connect(database) as connection:
        assert "my_team_name" not in _columns(connection, "leagues")
        connection.execute(
            "INSERT INTO leagues (id,name,season,source,scoring_json,roster_slots_json,created_at) "
            "VALUES (1,'Existing',2026,'manual','{}','[]',CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO players (id,league_id,name,pro_team,position,status,ownership,"
            "rostered_by,projected_points,floor,ceiling,ros_value,risk,evidence_json) "
            "VALUES (1,1,'Runner','CHI','RB','Active','Home','Home',12,8,16,0,0.5,'[]')"
        )
    _alembic(database, "upgrade", "head")
    _alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name,my_team_name FROM leagues").fetchone() == (
            "Existing",
            None,
        )
        assert connection.execute(
            "SELECT rostered_by,projected_points FROM players"
        ).fetchone() == ("Home", 12)
        connection.execute("UPDATE leagues SET my_team_name='Home'")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT my_team_name FROM leagues").fetchone() == ("Home",)
    _alembic(database, "downgrade", "0008")
    with sqlite3.connect(database) as connection:
        assert "my_team_name" not in _columns(connection, "leagues")
        assert connection.execute("SELECT count(*) FROM players").fetchone() == (1,)


def test_pool_week_migration_preserves_malformed_legacy_cards(tmp_path: Path) -> None:
    database = tmp_path / "migration.sqlite3"
    _alembic(database, "upgrade", "head")
    _alembic(database, "downgrade", "0001")

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO pools (id,name,pool_type,season,rules_json,created_at) "
            "VALUES (1,'Legacy','confidence',2026,'{}',CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO pool_entries (id,pool_id,name,active) VALUES (1,1,'Main',1)"
        )
        connection.execute(
            "INSERT INTO games "
            "(id,season,week,away_team,home_team,kickoff,home_win_probability,"
            "home_cover_probability,source,source_timestamp) "
            "VALUES (1,2026,1,'GB','CHI',CURRENT_TIMESTAMP,0.5,0.5,'legacy',CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO pool_picks (entry_id,game_id,week,slot,team,confidence) "
            "VALUES (1,1,1,1,'CHI',1)"
        )
        connection.execute(
            "INSERT INTO pool_picks (entry_id,game_id,week,slot,team,confidence) "
            "VALUES (1,1,1,2,'GB',1)"
        )
        connection.execute(
            "INSERT INTO pool_picks (entry_id,game_id,week,slot,team,confidence) "
            "VALUES (1,NULL,1,3,'DET',3)"
        )
        connection.commit()
    finally:
        connection.close()

    _alembic(database, "upgrade", "head")
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT count(*) FROM pool_picks").fetchone()[0] == 3
        assert "source_game_key" in _columns(connection, "games")
        assert "locked_at" in _columns(connection, "games")
        assert "pool_entry_weeks" in {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()

    _alembic(database, "downgrade", "0001")
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT count(*) FROM pool_picks").fetchone()[0] == 3
        assert "source_game_key" not in _columns(connection, "games")
        assert "pool_entry_weeks" not in {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()


def test_analysis_history_migration_preserves_existing_runs(tmp_path: Path) -> None:
    database = tmp_path / "analysis-history-migration.sqlite3"
    _alembic(database, "upgrade", "0005")

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO analysis_runs "
            "(id,task,model,status,prompt_version,schema_version,input_hash,"
            "snapshot_ids_json,output_json,created_at) "
            "VALUES (1,'chat','legacy-model','completed','v1','v1','legacy-hash','[]',"
            '\'{"summary":"Legacy result"}\',CURRENT_TIMESTAMP)'
        )
        connection.commit()
    finally:
        connection.close()

    _alembic(database, "upgrade", "head")
    connection = sqlite3.connect(database)
    try:
        assert "question" in _columns(connection, "analysis_runs")
        assert connection.execute(
            "SELECT question,output_json FROM analysis_runs WHERE id=1"
        ).fetchone() == ("", '{"summary":"Legacy result"}')
    finally:
        connection.close()

    _alembic(database, "downgrade", "0005")
    connection = sqlite3.connect(database)
    try:
        assert "question" not in _columns(connection, "analysis_runs")
        assert connection.execute("SELECT count(*) FROM analysis_runs").fetchone()[0] == 1
    finally:
        connection.close()


def test_projection_context_migration_preserves_legacy_values(tmp_path: Path) -> None:
    database = tmp_path / "projection-context.sqlite3"
    _alembic(database, "upgrade", "head")
    _alembic(database, "downgrade", "0006")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO leagues (id,name,season,source,scoring_json,roster_slots_json,created_at) "
            "VALUES (1,'Legacy',2026,'manual','{}','[]',CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO players (id,league_id,name,pro_team,position,status,ownership,"
            "projected_points,floor,ceiling,ros_value,risk,evidence_json) "
            "VALUES (1,1,'Legacy Zero','CHI','RB','Active','FA',12,8,16,0,0.5,'[]')"
        )
    _alembic(database, "upgrade", "head")
    _alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT projected_points,floor,ceiling,ros_value,projection_context_json FROM players"
        ).fetchone() == (12, 8, 16, 0, "{}")
    _alembic(database, "downgrade", "0006")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT projected_points,ros_value FROM players").fetchone() == (
            12,
            0,
        )


def test_draft_suite_migration_preserves_and_imports_legacy_draft(tmp_path: Path) -> None:
    database = tmp_path / "draft-migration.sqlite3"
    _alembic(database, "upgrade", "0002")

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO leagues "
            "(id,name,season,source,scoring_json,roster_slots_json,created_at) "
            "VALUES (1,'Legacy Draft',2026,'manual','{}','[\"QB\",\"RB\",\"WR\"]',"
            "CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO players "
            "(id,league_id,source_id,name,pro_team,position,status,ownership,"
            "projected_points,floor,ceiling,ros_value,risk,evidence_json) "
            "VALUES (1,1,'legacy-1','Legacy Runner','CHI','RB','Active','FA',"
            "210,170,250,21,0.2,'[]')"
        )
        connection.execute(
            "INSERT INTO identity_maps "
            "(id,canonical_name,pro_team,position,yahoo_key,confidence,manually_verified) "
            "VALUES (1,'Verified Runner','CHI','RB','yahoo-verified',1.0,1)"
        )
        connection.execute(
            "INSERT INTO draft_picks "
            "(id,league_id,overall,round,team_name,player_id,source,picked_at) "
            "VALUES (1,1,1,1,'Legacy Team',1,'manual',CURRENT_TIMESTAMP)"
        )
        connection.commit()
    finally:
        connection.close()

    _alembic(database, "upgrade", "head")
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            "athletes",
            "athlete_aliases",
            "projection_snapshots",
            "draft_sessions",
            "draft_events",
            "draft_input_previews",
        } <= tables
        assert connection.execute("SELECT athlete_id FROM players WHERE id=1").fetchone()[0]
        assert connection.execute("SELECT count(*) FROM athlete_aliases").fetchone()[0] == 2
        assert (
            connection.execute("SELECT count(*) FROM projection_snapshot_rows").fetchone()[0] == 1
        )
        assert connection.execute(
            "SELECT status,current_sequence FROM draft_sessions"
        ).fetchone() == ("PAUSED", 1)
        assert connection.execute(
            "SELECT type,overall_pick,player_id FROM draft_events"
        ).fetchone() == ("pick_recorded", 1, 1)
        assert connection.execute("SELECT count(*) FROM draft_picks").fetchone()[0] == 1
    finally:
        connection.close()

    _alembic(database, "downgrade", "0002")
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "draft_sessions" not in tables
        assert "athletes" not in tables
        assert "athlete_id" not in _columns(connection, "players")
        assert connection.execute("SELECT count(*) FROM draft_picks").fetchone()[0] == 1
    finally:
        connection.close()
