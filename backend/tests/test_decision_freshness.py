import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from app.models import DataSnapshot, League
from app.services.decision_freshness import yahoo_roster_source
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 9, 17, tzinfo=UTC)
KEY = "scrape:2026:1463577"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    DataSnapshot.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def snapshot(db, *, source="yahoo_scrape", status="fresh", age_minutes=0, key=KEY):
    row = DataSnapshot(
        source=source,
        source_id=key,
        status=status,
        retrieved_at=NOW - timedelta(minutes=age_minutes),
        payload_json='{"html":"' + "x" * 512_000 + '"}',
    )
    db.add(row)
    db.commit()
    return row.id


def test_roster_freshness_does_not_read_archived_payloads(db):
    """Large archived responses must never enter the freshness query's temp sort."""
    snapshot(db, status="failed", age_minutes=60)
    latest_id = snapshot(db)
    snapshot(db, status="failed", key="another-league")
    db.expire_all()
    connection = db.connection().connection.driver_connection

    def deny_payload_reads(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and (table, column) == ("data_snapshots", "payload_json"):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(deny_payload_reads)
    try:
        result = yahoo_roster_source(db, League(source="yahoo_scrape", yahoo_key=KEY), NOW)
    finally:
        connection.set_authorizer(None)
    assert result == {
        "name": "Yahoo league",
        "status": "available",
        "received_at": NOW.isoformat(),
        "snapshot_id": latest_id,
    }


@pytest.mark.parametrize(
    ("source", "latest_status", "age_minutes", "expected"),
    [
        ("yahoo", "fresh", 0, "partial"),
        ("yahoo_scrape", "fresh", 0, "available"),
        ("yahoo_scrape", "fresh", 361, "stale"),
        ("yahoo_scrape", "failed", 0, "partial"),
    ],
)
def test_roster_freshness_preserves_import_status(db, source, latest_status, age_minutes, expected):
    snapshot(db, source=source, status="failed", age_minutes=age_minutes + 5)
    latest_id = snapshot(db, source=source, status=latest_status, age_minutes=age_minutes)
    result = yahoo_roster_source(db, League(source=source, yahoo_key=KEY), NOW)
    assert result["status"] == expected
    assert result["snapshot_id"] == latest_id


def test_roster_freshness_handles_missing_and_manual_sources(db):
    assert yahoo_roster_source(db, League(source="manual"), NOW) is None
    result = yahoo_roster_source(db, League(source="yahoo", yahoo_key=KEY), NOW)
    assert result["status"] == "unavailable"
