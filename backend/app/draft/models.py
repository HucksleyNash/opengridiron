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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base

if TYPE_CHECKING:
    from ..models import Player


def utcnow() -> datetime:
    return datetime.now(UTC)


class Athlete(Base):
    __tablename__ = "athletes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(30), default="active")
    verified_metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    aliases: Mapped[list[AthleteAlias]] = relationship(
        back_populates="athlete", cascade="all, delete-orphan"
    )
    players: Mapped[list[Player]] = relationship(back_populates="athlete")


class AthleteAlias(Base):
    __tablename__ = "athlete_aliases"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "namespace",
            "season_scope",
            "external_id",
            name="uq_athlete_alias_external_identity",
        ),
        Index("ix_athlete_aliases_athlete_id", "athlete_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(40))
    namespace: Mapped[str] = mapped_column(String(120))
    season_scope: Mapped[str] = mapped_column(String(80))
    external_id: Mapped[str] = mapped_column(String(160))
    observed_name: Mapped[str] = mapped_column(String(160))
    observed_team: Mapped[str | None] = mapped_column(String(12), nullable=True)
    observed_position: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(20), default="mapped")
    manually_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    athlete: Mapped[Athlete] = relationship(back_populates="aliases")


class ProjectionSnapshot(Base):
    __tablename__ = "projection_snapshots"
    __table_args__ = (UniqueConstraint("league_id", "dataset_hash", name="uq_projection_dataset"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(80))
    import_type: Mapped[str] = mapped_column(String(40), default="legacy-player-state")
    content_hash: Mapped[str] = mapped_column(String(64))
    dataset_hash: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(40), default="draft-input-v1")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    canonical_coverage: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(30), default="ready")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    rows: Mapped[list[ProjectionSnapshotRow]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class ProjectionSnapshotRow(Base):
    __tablename__ = "projection_snapshot_rows"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "athlete_id", name="uq_projection_athlete"),
        Index("ix_projection_rows_snapshot_id", "snapshot_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("projection_snapshots.id", ondelete="CASCADE")
    )
    athlete_id: Mapped[int] = mapped_column(
        ForeignKey("athletes.id", ondelete="RESTRICT"), index=True
    )
    league_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    source_row_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    position: Mapped[str] = mapped_column(String(20))
    eligibility_json: Mapped[str] = mapped_column(Text, default="[]")
    raw_stats_json: Mapped[str] = mapped_column(Text, default="{}")
    projected_points: Mapped[float] = mapped_column(Float, default=0.0)
    floor: Mapped[float] = mapped_column(Float, default=0.0)
    ceiling: Mapped[float] = mapped_column(Float, default=0.0)
    source_value: Mapped[float] = mapped_column(Float, default=0.0)
    risk: Mapped[float] = mapped_column(Float, default=0.5)
    row_hash: Mapped[str] = mapped_column(String(64))

    snapshot: Mapped[ProjectionSnapshot] = relationship(back_populates="rows")
    athlete: Mapped[Athlete] = relationship()
    league_player: Mapped[Player | None] = relationship()


class DraftRankingSnapshot(Base):
    __tablename__ = "draft_ranking_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(80))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    dataset_hash: Mapped[str] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    canonical_coverage: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(30), default="ready")
    rows_json: Mapped[str] = mapped_column(Text, default="[]")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DraftInputPreview(Base):
    __tablename__ = "draft_input_previews"
    __table_args__ = (
        Index("ix_draft_input_previews_league_status", "league_id", "status"),
        Index("ix_draft_input_previews_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)
    input_type: Mapped[str] = mapped_column(String(30))
    filename: Mapped[str] = mapped_column(String(240))
    content_hash: Mapped[str] = mapped_column(String(64))
    rows_json: Mapped[str] = mapped_column(Text, default="[]")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    blocking_errors_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(20), default="staged")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class YahooAuthorityEvidence(Base):
    __tablename__ = "yahoo_authority_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)
    transport: Mapped[str] = mapped_column(String(30))
    provider_version: Mapped[str] = mapped_column(String(80))
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    controlled_draft_id: Mapped[str] = mapped_column(String(160))
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consecutive_pick_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    observation_delays_json: Mapped[str] = mapped_column(Text, default="[]")
    partial_response_checks: Mapped[int] = mapped_column(Integer, default=0)
    rate_limit_responses: Mapped[int] = mapped_column(Integer, default=0)
    polling_interval_seconds: Mapped[int] = mapped_column(Integer, default=10)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DraftSession(Base):
    __tablename__ = "draft_sessions"
    __table_args__ = (
        Index("ix_draft_sessions_league_status", "league_id", "status"),
        Index("ix_draft_sessions_league_archived", "league_id", "archived_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="live")
    format: Mapped[str] = mapped_column(String(20), default="snake")
    status: Mapped[str] = mapped_column(String(20), default="SETUP")
    strategy_mode: Mapped[str] = mapped_column(String(20), default="adaptive")
    strategy_config_json: Mapped[str] = mapped_column(Text, default="{}")
    team_count: Mapped[int] = mapped_column(Integer)
    round_count: Mapped[int] = mapped_column(Integer)
    owner_team_slot: Mapped[int] = mapped_column(Integer)
    projection_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("projection_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    ranking_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("draft_ranking_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    source_mode: Mapped[str] = mapped_column(String(40), default="manual")
    authority_evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("yahoo_authority_evidence.id", ondelete="SET NULL"), nullable=True
    )
    current_sequence: Mapped[int] = mapped_column(Integer, default=0)
    preference_revision: Mapped[int] = mapped_column(Integer, default=0)
    replay_generation: Mapped[int] = mapped_column(Integer, default=1)
    scoring_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    roster_slots_snapshot_json: Mapped[str] = mapped_column(Text, default="[]")
    format_config_json: Mapped[str] = mapped_column(Text, default="{}")
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    provider_league_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provider_draft_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    teams: Mapped[list[DraftTeam]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    events: Mapped[list[DraftEvent]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    preferences: Mapped[list[DraftBoardPreference]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    recommendation_snapshots: Mapped[list[DraftRecommendationSnapshot]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class DraftTeam(Base):
    __tablename__ = "draft_teams"
    __table_args__ = (UniqueConstraint("session_id", "slot", name="uq_draft_team_slot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("draft_sessions.id", ondelete="CASCADE"), index=True
    )
    slot: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(160))
    provider_team_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)

    session: Mapped[DraftSession] = relationship(back_populates="teams")


class DraftRecommendationSnapshot(Base):
    __tablename__ = "draft_recommendation_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "session_sequence",
            "input_hash",
            name="uq_draft_recommendation_input",
        ),
        Index(
            "ix_draft_recommendation_latest",
            "session_id",
            "session_sequence",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("draft_sessions.id", ondelete="CASCADE"))
    session_sequence: Mapped[int] = mapped_column(Integer)
    algorithm_version: Mapped[str] = mapped_column(String(40), default="draft-score-v1")
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    input_hash: Mapped[str] = mapped_column(String(64))
    projection_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("projection_snapshots.id", ondelete="RESTRICT")
    )
    ranking_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("draft_ranking_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="ready")
    candidates_json: Mapped[str] = mapped_column(Text, default="[]")
    alternatives_json: Mapped[str] = mapped_column(Text, default="[]")
    forecast_status: Mapped[str] = mapped_column(String(30), default="unavailable")
    scenario_summaries_json: Mapped[str] = mapped_column(Text, default="[]")
    freshness_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[DraftSession] = relationship(back_populates="recommendation_snapshots")


class DraftEvent(Base):
    __tablename__ = "draft_events"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_draft_event_sequence"),
        UniqueConstraint("session_id", "idempotency_key", name="uq_draft_event_idempotency"),
        Index("ix_draft_events_session_overall", "session_id", "overall_pick"),
        Index("ix_draft_events_session_player", "session_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("draft_sessions.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(40))
    overall_pick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    round: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    athlete_id: Mapped[int | None] = mapped_column(
        ForeignKey("athletes.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(40), default="manual")
    idempotency_key: Mapped[str] = mapped_column(String(120))
    provider_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provider_revision: Mapped[str | None] = mapped_column(String(80), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    early: Mapped[bool] = mapped_column(Boolean, default=False)
    supersedes_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("draft_events.id", ondelete="SET NULL"), nullable=True
    )
    recommendation_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("draft_recommendation_snapshots.id", ondelete="SET NULL"), nullable=True
    )

    session: Mapped[DraftSession] = relationship(back_populates="events")
    player: Mapped[Player | None] = relationship(foreign_keys=[player_id])
    athlete: Mapped[Athlete | None] = relationship()
    recommendation_snapshot: Mapped[DraftRecommendationSnapshot | None] = relationship()


class DraftBoardPreference(Base):
    __tablename__ = "draft_board_preferences"
    __table_args__ = (
        UniqueConstraint("session_id", "player_id", name="uq_draft_preference_player"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("draft_sessions.id", ondelete="CASCADE"), index=True
    )
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    queue_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target: Mapped[bool] = mapped_column(Boolean, default=False)
    fade: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    session: Mapped[DraftSession] = relationship(back_populates="preferences")
    player: Mapped[Player] = relationship()


class DraftReconciliationConflict(Base):
    __tablename__ = "draft_reconciliation_conflicts"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "provider_key", "provider_revision", name="uq_draft_conflict_provider"
        ),
        Index("ix_draft_conflicts_session_status", "session_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("draft_sessions.id", ondelete="CASCADE"))
    overall_pick: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="unresolved")
    canonical_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("draft_events.id", ondelete="SET NULL"), nullable=True
    )
    incoming_payload_json: Mapped[str] = mapped_column(Text)
    provider_key: Mapped[str] = mapped_column(String(160))
    provider_revision: Mapped[str] = mapped_column(String(80), default="1")
    detected_sequence: Mapped[int] = mapped_column(Integer)
    resolution_action: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resolving_event_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DraftComputationRun(Base):
    __tablename__ = "draft_computation_runs"
    __table_args__ = (Index("ix_draft_computation_session_status", "session_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("draft_sessions.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    session_sequence: Mapped[int] = mapped_column(Integer)
    input_hash: Mapped[str] = mapped_column(String(64))
    request_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
