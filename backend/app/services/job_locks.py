from __future__ import annotations

import fcntl
import re
from collections.abc import Iterator
from contextlib import contextmanager

from ..config import settings


@contextmanager
def job_lock(name: str) -> Iterator[bool]:
    """A nonblocking process lease for workers sharing the application's data volume."""
    if not re.fullmatch(r"[a-z0-9_-]+", name):
        raise ValueError("Invalid job lock name")
    directory = settings.data_dir / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{name}.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
