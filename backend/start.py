from __future__ import annotations

import os

from alembic import command
from alembic.config import Config


def main() -> None:
    config = Config("/app/backend/alembic.ini")
    command.upgrade(config, "head")
    os.execvp(
        "uvicorn",
        [
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8787",
            "--no-access-log",
        ],
    )


if __name__ == "__main__":
    main()
