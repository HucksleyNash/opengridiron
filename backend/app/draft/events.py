from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from threading import Lock, RLock
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.orm import Session

_locks: defaultdict[int, RLock] = defaultdict(RLock)
_locks_guard = Lock()
_COORDINATOR_KEY = "draft_mutation_session_id"


def _lock_for(session_id: int) -> RLock:
    with _locks_guard:
        return _locks[session_id]


@contextmanager
def session_mutation(db: Session, session_id: int) -> Iterator[None]:
    """Serialize a canonical mutation and acquire SQLite's write reservation up front."""
    active_session = db.info.get(_COORDINATOR_KEY)
    if active_session is not None:
        if active_session != session_id:
            raise RuntimeError("A draft mutation cannot coordinate two sessions at once")
        yield
        return

    with _lock_for(session_id):
        # Auth/read dependencies can leave a harmless implicit read transaction open.
        # End it before reserving the SQLite writer so all invariants are re-read inside
        # the same short mutation boundary.
        if db.in_transaction():
            db.rollback()
        if db.bind is not None and db.bind.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        db.info[_COORDINATOR_KEY] = session_id
        try:
            yield
        except Exception:
            db.rollback()
            raise
        finally:
            db.info.pop(_COORDINATOR_KEY, None)
            # Idempotent no-op paths intentionally do not commit.
            if db.in_transaction():
                db.rollback()


def coordinated_mutation[F: Callable[..., Any]](function: F) -> F:
    """Decorate a `(db, session-id-or-model, ...)` canonical mutation."""

    @wraps(function)
    def wrapped(db: Session, target: object, *args: object, **kwargs: object):
        session_id = target if isinstance(target, int) else target.id
        with session_mutation(db, int(session_id)):
            return function(db, target, *args, **kwargs)

    return cast(F, wrapped)
