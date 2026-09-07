from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.orm import Session

from ..models import AnalysisProvider, DataSnapshot, Game, League, NewsSource
from ..schemas import Projection


class FantasyPlatform(Protocol):
    async def sync(self, db: Session) -> list[DataSnapshot]: ...


class StatsProvider(Protocol):
    async def sync_schedule(self, db: Session, season: int) -> DataSnapshot: ...

    async def sync_rosters(self, db: Session, season: int) -> DataSnapshot: ...


class NewsSourceProvider(Protocol):
    async def fetch(self, db: Session, source: NewsSource) -> DataSnapshot: ...


class OddsProvider(Protocol):
    async def probabilities(self, game: Game) -> dict[str, float]: ...


class ProjectionProvider(Protocol):
    async def project(self, league: League, week: int) -> list[Projection]: ...


class AnalysisProviderContract(Protocol):
    async def analyze(
        self,
        provider: AnalysisProvider,
        question: str,
        dossier: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, int]]: ...
