from __future__ import annotations

import hashlib
import json
import sqlite3
import stat

import pytest
from app.operations.recovery import create_bundle, restore_bundle
from app.services.backups import create_compact_snapshot


@pytest.fixture
def recovery_inputs(tmp_path):
    data, codex = tmp_path / "data", tmp_path / "codex"
    data.mkdir()
    codex.mkdir()
    (data / "master-secret").write_text("synthetic-local-master-secret")
    (data / "cache").mkdir()
    (data / "cache" / "large.csv").write_text("regenerable cache")
    (data / "models").mkdir()
    (data / "models" / "model.json").write_text('{"version":1}')
    (codex / "auth.json").write_text('{"credential":"synthetic-codex-session"}')
    (codex / "fixture-hook").write_text("#!/bin/sh\nexit 0\n")
    (codex / "fixture-hook").chmod(0o755)
    with sqlite3.connect(data / "football.db") as db:
        db.execute("CREATE TABLE evidence (value TEXT)")
        db.execute("INSERT INTO evidence VALUES ('saved league')")
    environment_file = tmp_path / "fixture.env"
    environment_file.write_text("APP_SECRET=synthetic-environment-secret\n")
    return data, codex, environment_file


def test_encrypted_recovery_round_trip_retains_credentials_and_database(tmp_path, recovery_inputs):
    data, codex, environment_file = recovery_inputs
    output = tmp_path / "recovery.enc"
    create_bundle(
        data,
        codex,
        output,
        "a-long-fixture-passphrase",
        environment={
            "APP_SECRET": "synthetic-environment-secret",
            "UNRELATED_SECRET": "exclude-me",
        },
        environment_file=environment_file,
    )
    encrypted = output.read_bytes()
    assert b"synthetic" not in encrypted and b"saved league" not in encrypted
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    restored = restore_bundle(output, tmp_path / "restored", "a-long-fixture-passphrase")
    with sqlite3.connect(restored / "data/football.db") as db:
        assert db.execute("SELECT value FROM evidence").fetchone() == ("saved league",)
    assert (restored / "data/master-secret").read_text() == "synthetic-local-master-secret"
    assert (restored / "codex-home/auth.json").read_text() == (codex / "auth.json").read_text()
    assert (restored / "configuration.env").read_text() == environment_file.read_text()
    assert not (restored / "data/cache").exists()
    assert (restored / "data/models/model.json").exists()
    assert (
        "UNRELATED_SECRET"
        not in json.loads((restored / "manifest.json").read_text())["environment"]
    )
    assert stat.S_IMODE((restored / "codex-home/auth.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((restored / "codex-home/fixture-hook").stat().st_mode) == 0o700


def test_wrong_passphrase_and_occupied_target_leave_destination_untouched(
    tmp_path, recovery_inputs
):
    data, codex, _ = recovery_inputs
    output = create_bundle(data, codex, tmp_path / "recovery.enc", "a-long-fixture-passphrase")
    with pytest.raises(ValueError, match="Wrong passphrase"):
        restore_bundle(output, tmp_path / "wrong-password", "a-different-passphrase")
    assert not (tmp_path / "wrong-password").exists()
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep").write_text("original")
    with pytest.raises(ValueError, match="empty destination"):
        restore_bundle(output, occupied, "a-long-fixture-passphrase")
    assert (occupied / "keep").read_text() == "original"
    with pytest.raises(ValueError, match="overwrite"):
        create_bundle(data, codex, output, "a-long-fixture-passphrase")


def test_recovery_rejects_links_outside_input_tree(tmp_path, recovery_inputs):
    data, codex, _ = recovery_inputs
    outside = tmp_path / "unrelated-private-file"
    outside.write_text("must never be archived")
    (data / "linked-secret").symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic links"):
        create_bundle(data, codex, tmp_path / "unsafe.enc", "a-long-fixture-passphrase")
    assert not (tmp_path / "unsafe.enc").exists()


def test_compact_snapshot_omits_free_pages_and_never_changes_source(tmp_path):
    source = tmp_path / "bloated.sqlite3"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE retained (id INTEGER PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO retained VALUES (7, 'league evidence')")
        db.execute("CREATE TABLE discarded (payload BLOB)")
        db.executemany("INSERT INTO discarded VALUES (zeroblob(100000))", [()] * 20)
        db.execute("DROP TABLE discarded")
        db.commit()
        assert db.execute("PRAGMA freelist_count").fetchone()[0] > 100
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    snapshot = create_compact_snapshot(source, tmp_path / "compact.sqlite3")
    assert snapshot.stat().st_size < source.stat().st_size / 10
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    with sqlite3.connect(snapshot) as db:
        assert db.execute("SELECT * FROM retained").fetchall() == [(7, "league evidence")]
        assert db.execute("PRAGMA freelist_count").fetchone() == (0,)
    with pytest.raises(FileExistsError):
        create_compact_snapshot(source, snapshot)
