from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

if TYPE_CHECKING:
    from .draft.models import Athlete


def utcnow() -> datetime:
    return datetime.now(UTC)


class Owner(Base):
    __tablename__ = "owners"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SecretSetting(Base):
    __tablename__ = "secret_settings"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    encrypted_value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DataSnapshot(Base):
    __tablename__ = "data_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(80), index=True)
    source_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    freshness_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="fresh")
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class League(Base):
    __tablename__ = "leagues"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    season: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(30), default="manual")
    yahoo_key: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    scoring_json: Mapped[str] = mapped_column(Text, default="{}")
    roster_slots_json: Mapped[str] = mapped_column(
        Text, default='["QB","RB","RB","WR","WR","TE","FLEX","K","DEF"]'
    )
    faab_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    my_team_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    players: Mapped[list[Player]] = relationship(
        back_populates="league", cascade="all, delete-orphan"
    )
    draft_picks: Mapped[list[DraftPick]] = relationship(
        back_populates="league", cascade="all, delete-orphan"
    )


class Player(Base):
    __tablename__ = "players"
    __table_args__ = (UniqueConstraint("league_id", "source_id", name="uq_player_league_source"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)
    athlete_id: Mapped[int | None] = mapped_column(
        ForeignKey("athletes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    pro_team: Mapped[str] = mapped_column(String(8), index=True)
    position: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(40), default="Active")
    ownership: Mapped[str] = mapped_column(String(30), default="FA")
    rostered_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    current_slot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    projected_points: Mapped[float] = mapped_column(Float, default=0)
    floor: Mapped[float] = mapped_column(Float, default=0)
    ceiling: Mapped[float] = mapped_column(Float, default=0)
    ros_value: Mapped[float] = mapped_column(Float, default=0)
    risk: Mapped[float] = mapped_column(Float, default=0.5)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    projection_context_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    league: Mapped[League] = relationship(back_populates="players")
    athlete: Mapped[Athlete | None] = relationship(back_populates="players")


class DraftPick(Base):
    __tablename__ = "draft_picks"
    __table_args__ = (UniqueConstraint("league_id", "overall", name="uq_draft_league_overall"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)
    overall: Mapped[int] = mapped_column(Integer)
    round: Mapped[int] = mapped_column(Integer)
    team_name: Mapped[str] = mapped_column(String(160))
    player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(30), default="manual")
    picked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    league: Mapped[League] = relationship(back_populates="draft_picks")
    player: Mapped[Player | None] = relationship()

    @property
    def player_name(self) -> str | None:
        return self.player.name if self.player else None


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        UniqueConstraint("season", "week", "away_team", "home_team", name="uq_game_matchup"),
        Index(
            "uq_game_source_identity",
            "source_game_key_kind",
            "source_game_key",
            unique=True,
            sqlite_where=text("source_game_key IS NOT NULL"),
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    week: Mapped[int] = mapped_column(Integer, index=True)
    away_team: Mapped[str] = mapped_column(String(8))
    home_team: Mapped[str] = mapped_column(String(8))
    kickoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    home_win_probability: Mapped[float] = mapped_column(Float, default=0.5)
    home_cover_probability: Mapped[float] = mapped_column(Float, default=0.5)
    spread_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    total: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="manual")
    source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_game_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_game_key_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    win_probability_kind: Mapped[str] = mapped_column(String(30), default="legacy_unknown")
    cover_probability_kind: Mapped[str] = mapped_column(String(30), default="legacy_unknown")
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    model_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")


class Pool(Base):
    __tablename__ = "pools"
    __table_args__ = (UniqueConstraint("sleeper_league_id", name="uq_pool_sleeper_league"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    pool_type: Mapped[str] = mapped_column(String(30))
    season: Mapped[int] = mapped_column(Integer)
    rules_json: Mapped[str] = mapped_column(Text)
    sleeper_league_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sleeper_snapshot_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    entries: Mapped[list[PoolEntry]] = relationship(
        back_populates="pool", cascade="all, delete-orphan"
    )


class PoolEntry(Base):
    __tablename__ = "pool_entries"
    __table_args__ = (
        UniqueConstraint("pool_id", "sleeper_roster_id", name="uq_pool_sleeper_roster"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("pools.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sleeper_roster_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pool: Mapped[Pool] = relationship(back_populates="entries")
    picks: Mapped[list[PoolPick]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )
    weeks: Mapped[list[PoolEntryWeek]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )


class PoolEntryWeek(Base):
    __tablename__ = "pool_entry_weeks"
    __table_args__ = (UniqueConstraint("entry_id", "week", name="uq_pool_entry_week"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("pool_entries.id", ondelete="CASCADE"), index=True
    )
    week: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    entry: Mapped[PoolEntry] = relationship(back_populates="weeks")


class PoolPick(Base):
    __tablename__ = "pool_picks"
    __table_args__ = (UniqueConstraint("entry_id", "week", "slot", name="uq_pick_entry_week_slot"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("pool_entries.id", ondelete="CASCADE"), index=True
    )
    game_id: Mapped[int | None] = mapped_column(
        ForeignKey("games.id", ondelete="SET NULL"), nullable=True
    )
    week: Mapped[int] = mapped_column(Integer)
    slot: Mapped[int] = mapped_column(Integer, default=1)
    team: Mapped[str] = mapped_column(String(8))
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[str | None] = mapped_column(String(20), nullable=True)
    spread_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    probability_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entry: Mapped[PoolEntry] = relationship(back_populates="picks")
    game: Mapped[Game | None] = relationship()


class NewsSource(Base):
    __tablename__ = "news_sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(Text, unique=True)
    source_type: Mapped[str] = mapped_column(String(20), default="rss")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    official: Mapped[bool] = mapped_column(Boolean, default=False)
    etag: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NewsItem(Base):
    __tablename__ = "news_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("news_sources.id", ondelete="SET NULL"), nullable=True
    )
    canonical_url: Mapped[str] = mapped_column(Text, unique=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(40), default="news")
    severity: Mapped[str] = mapped_column(String(20), default="info")
    teams_json: Mapped[str] = mapped_column(Text, default="[]")
    players_json: Mapped[str] = mapped_column(Text, default="[]")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[NewsSource | None] = relationship()


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), default="info")
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdentityMap(Base):
    __tablename__ = "identity_maps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(160), index=True)
    pro_team: Mapped[str] = mapped_column(String(8))
    position: Mapped[str] = mapped_column(String(16))
    yahoo_key: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
    gsis_id: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    manually_verified: Mapped[bool] = mapped_column(Boolean, default=False)


class AnalysisProvider(Base):
    __tablename__ = "analysis_providers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    provider_type: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(160))
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_setting: Mapped[str | None] = mapped_column(String(120), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    task_defaults_json: Mapped[str] = mapped_column(Text, default="[]")


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task: Mapped[str] = mapped_column(String(80), index=True)
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_providers.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    prompt_version: Mapped[str] = mapped_column(String(40), default="v1")
    schema_version: Mapped[str] = mapped_column(String(40), default="v1")
    question: Mapped[str] = mapped_column(Text, default="", server_default="")
    input_hash: Mapped[str] = mapped_column(String(64))
    snapshot_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    input_dossier_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True
    )
    league_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("league_analyses.id", ondelete="SET NULL"), nullable=True
    )
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider: Mapped[AnalysisProvider | None] = relationship()


class LeagueAnalysis(Base):
    """A frozen weekly report; source Player values are never overwritten."""

    __tablename__ = "league_analyses"
    __table_args__ = (
        Index(
            "uq_league_analysis_active",
            "league_id",
            unique=True,
            sqlite_where=text("status IN ('queued','refreshing','forecasting','analyzing')"),
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)
    team_name: Mapped[str] = mapped_column(String(160))
    season: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer)
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_providers.id", ondelete="SET NULL"), nullable=True
    )
    analysis_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), default="queued")
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint_hash: Mapped[str] = mapped_column(String(64), unique=True)
    subscription_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Keep every declarative model registered when callers import this legacy module.
# Draft models refer back to League and Player through SQLAlchemy's string registry,
# so importing them here does not create a Python-level circular dependency.
from .draft import models as draft_models  # noqa: E402,F401
