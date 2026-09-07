from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ..config import settings


def database_path() -> Path:
    prefix = "sqlite:///"
    if not settings.database_url.startswith(prefix):
        raise RuntimeError("Built-in backups currently support SQLite only")
    return Path(settings.database_url.removeprefix(prefix))


def create_compact_snapshot(source_path: Path, destination: Path) -> Path:
    """Create a consistent compact copy while opening the original read-only."""
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        with sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro", uri=True) as source:
            source.execute("VACUUM INTO ?", (str(destination.resolve()),))
        with sqlite3.connect(f"{destination.resolve().as_uri()}?mode=ro", uri=True) as snapshot:
            if snapshot.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("The database snapshot failed its integrity check")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def create_backup() -> Path:
    source_path = database_path()
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    backup_dir = settings.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backup_dir / f"football-{stamp}.sqlite3"
    create_compact_snapshot(source_path, destination)
    prune_backups(keep=7)
    return destination


def list_backups() -> list[Path]:
    backup_dir = settings.data_dir / "backups"
    return sorted(backup_dir.glob("football-*.sqlite3"), reverse=True)


def prune_backups(keep: int = 7) -> None:
    for backup in list_backups()[keep:]:
        backup.unlink(missing_ok=True)


def restore_backup(path: Path) -> Path:
    if path.parent.resolve() != (settings.data_dir / "backups").resolve():
        raise ValueError("Backup must come from the managed backup directory")
    if not path.exists():
        raise FileNotFoundError(path)
    with sqlite3.connect(path) as source:
        integrity = source.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError("Backup failed SQLite integrity validation")
        safety_copy = create_backup()
        with sqlite3.connect(database_path()) as target:
            source.backup(target)
    return safety_copy
