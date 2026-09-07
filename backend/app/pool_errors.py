from __future__ import annotations

from typing import Any


class PoolDomainError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.context = context or {}


class ScheduleSyncError(PoolDomainError):
    pass
