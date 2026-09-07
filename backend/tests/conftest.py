from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Each pytest invocation owns its database. Spawned simulation workers inherit
# these paths without deleting or replacing the parent's database.
TEST_ROOT = Path(tempfile.mkdtemp(prefix="football-tests-"))
TEST_DATABASE = TEST_ROOT / "football.sqlite3"
os.environ["APP_ENV"] = "test"
os.environ["APP_SECRET"] = "test-secret-with-enough-entropy-for-the-suite"
os.environ["AUTH_REQUIRED"] = "false"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE}"
os.environ["DATA_DIR"] = str(TEST_ROOT / "data")
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["YAHOO_REQUEST_INTERVAL_SECONDS"] = "0"


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
