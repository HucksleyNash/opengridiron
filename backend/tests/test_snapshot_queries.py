import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from app.draft.ranking_sources import latest_scrape_snapshot
from app.models import DataSnapshot, League
from app.services.decision_freshness import yahoo_roster_source
from app.services.pool_week import _latest_snapshots
from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import Session

NOW = datetime(2026, 9, 17, tzinfo=UTC)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    DataSnapshot.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def seed(db, source, source_id, count, *, status="fresh", received_at=NOW, payload="{}"):
    db.execute(
        insert(DataSnapshot),
        [
            dict(
                source=source,
                source_id=source_id,
                status=status,
                retrieved_at=received_at,
                payload_json=payload,
            )
            for _ in range(count)
        ],
    )
    db.commit()


@contextmanager
def metadata_only(db):
    connection = db.connection().connection.driver_connection

    def authorize(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and (table, column) == ("data_snapshots", "payload_json"):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)
    try:
        yield
    finally:
        connection.set_authorizer(None)


def test_snapshot_lists_do_not_read_large_payloads(db):
    seed(db, "yahoo_scrape", "league", 30, payload='{"body":"' + "x" * 512_000 + '"}')
    with metadata_only(db):
        rows = db.query(DataSnapshot).order_by(DataSnapshot.retrieved_at.desc()).limit(12).all()
        assert len(rows) == 12


def test_selected_payload_loads_once_by_id_after_metadata_selection(db):
    payload = '{"players":[{"name":"Newest player"}]}'
    seed(db, "yahoo_scrape", "league", 1, payload=payload)
    with metadata_only(db):
        row = db.query(DataSnapshot).first()
        assert row is not None
    statements = []

    def capture(_connection, _cursor, statement, parameters, _context, _many):
        statements.append((statement, parameters))

    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        assert row.payload_json == payload
        assert row.payload_json == payload
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert len(statements) == 1
    assert "WHERE data_snapshots.id = ?" in statements[0][0]
    assert statements[0][1] == (row.id,)


def test_pool_latest_snapshots_materializes_only_one_success_per_season(db):
    for season in (2025, 2026):
        seed(db, "nflverse.schedule", str(season), 250)
        seed(db, "nflverse.schedule", str(season), 1, status="error")
    seed(db, "nflverse.schedule", "2024", 1)
    loaded = []
    event.listen(db, "loaded_as_persistent", lambda _db, row: loaded.append(row.id))
    rows = _latest_snapshots(db, {2025, 2026, 2027})
    assert {season: row.id for season, row in rows.items()} == {2025: 250, 2026: 501}
    assert len(loaded) == 2
    assert _latest_snapshots(db, set()) == {}


def test_draft_latest_snapshot_materializes_only_selected_record(db):
    seed(db, "yahoo_scrape", "league", 250)
    seed(db, "yahoo_scrape", "league", 1, status="failed")
    seed(db, "yahoo_scrape", "unrelated", 1)
    loaded = []
    event.listen(db, "loaded_as_persistent", lambda _db, row: loaded.append(row.id))
    row = latest_scrape_snapshot(db, League(yahoo_key="league"))
    assert row.id == 250
    assert loaded == [250]


@pytest.mark.parametrize(
    ("failed_source_id", "failed_age", "expected"),
    [
        ("league:players:0", 5, "partial"),
        ("league:players:0", 31, "available"),
        ("unrelated:players:0", 5, "available"),
    ],
)
def test_roster_freshness_checks_entire_import_without_a_history_page_cutoff(
    db, failed_source_id, failed_age, expected
):
    seed(
        db,
        "yahoo",
        failed_source_id,
        1,
        status="failed",
        received_at=NOW - timedelta(minutes=failed_age),
    )
    seed(db, "yahoo", "league:players:25", 250)
    with metadata_only(db):
        result = yahoo_roster_source(db, League(source="yahoo", yahoo_key="league"), NOW)
    assert result["status"] == expected
    assert result["snapshot_id"] == 251
