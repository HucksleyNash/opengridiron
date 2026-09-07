from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class DraftSessionCreate(BaseModel):
    kind: Literal["live", "mock"] = "live"
    team_count: int = Field(default=12, ge=8, le=16)
    round_count: int = Field(default=16, ge=1, le=30)
    owner_team_slot: int = Field(default=1, ge=1, le=16)
    owner_team_name: str = Field(default="My Team", min_length=1, max_length=160)
    team_names: list[str] = Field(default_factory=list, max_length=16)
    source_mode: Literal[
        "manual",
        "yahoo_scrape_shadow",
        "yahoo_oauth_shadow",
    ] = "manual"
    strategy_mode: Literal["adaptive", "locked"] = "adaptive"
    opponent_mode: Literal["manual", "automatic"] = "manual"

    @model_validator(mode="after")
    def validate_order(self) -> DraftSessionCreate:
        if self.owner_team_slot > self.team_count:
            raise ValueError("owner_team_slot must be within the configured team count")
        if self.team_names and len(self.team_names) != self.team_count:
            raise ValueError("team_names must contain exactly team_count names")
        return self


class DraftSetupUpdate(BaseModel):
    expected_sequence: int = Field(ge=0)
    team_count: int = Field(ge=8, le=16)
    round_count: int = Field(ge=1, le=30)
    owner_team_slot: int = Field(ge=1, le=16)
    team_names: list[str] = Field(min_length=8, max_length=16)

    @model_validator(mode="after")
    def validate_order(self) -> DraftSetupUpdate:
        if self.owner_team_slot > self.team_count:
            raise ValueError("owner_team_slot must be within the configured team count")
        if len(self.team_names) != self.team_count:
            raise ValueError("team_names must contain exactly team_count names")
        return self


class DraftActionRequest(BaseModel):
    expected_sequence: int = Field(ge=0)
    idempotency_key: str = Field(min_length=4, max_length=120)
    reason: str | None = Field(default=None, max_length=1000)


class DraftRankingSyncRequest(BaseModel):
    session_id: int = Field(ge=1)
    expected_sequence: int = Field(ge=0)
    refresh: bool = False


class DraftOpponentPickRequest(BaseModel):
    expected_sequence: int = Field(ge=0)
    idempotency_key: str = Field(min_length=4, max_length=120)


class DraftArchiveRequest(BaseModel):
    expected_sequence: int = Field(ge=0)
    archived: bool


class DraftEventRequest(BaseModel):
    type: Literal["pick_recorded", "pick_reversed", "pick_replaced"]
    expected_sequence: int = Field(ge=0)
    idempotency_key: str = Field(min_length=4, max_length=120)
    player_id: int | None = Field(default=None, ge=1)
    overall_pick: int | None = Field(default=None, ge=1)
    target_event_id: int | None = Field(default=None, ge=1)
    recommendation_snapshot_id: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_event_shape(self) -> DraftEventRequest:
        if self.type in {"pick_recorded", "pick_replaced"} and self.player_id is None:
            raise ValueError("player_id is required for a recorded or replacement pick")
        if self.type in {"pick_reversed", "pick_replaced"} and self.target_event_id is None:
            raise ValueError("target_event_id is required for a reversal or replacement")
        if self.type in {"pick_reversed", "pick_replaced"} and not self.reason:
            raise ValueError("a reason is required for a reversal or replacement")
        return self


class DraftPreferenceItem(BaseModel):
    player_id: int = Field(ge=1)
    queue_rank: int | None = Field(default=None, ge=1)
    target: bool = False
    fade: bool = False
    note: str | None = Field(default=None, max_length=1000)


class DraftPreferenceUpdate(BaseModel):
    expected_revision: int = Field(ge=0)
    items: list[DraftPreferenceItem] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def unique_players_and_ranks(self) -> DraftPreferenceUpdate:
        player_ids = [item.player_id for item in self.items]
        if len(set(player_ids)) != len(player_ids):
            raise ValueError("preference player IDs must be unique")
        ranks = [item.queue_rank for item in self.items if item.queue_rank is not None]
        if len(set(ranks)) != len(ranks):
            raise ValueError("queue ranks must be unique")
        return self


class DraftYahooObservation(BaseModel):
    provider_key: str = Field(min_length=1, max_length=160)
    provider_revision: str = Field(default="1", min_length=1, max_length=80)
    overall_pick: int = Field(ge=1, le=480)
    team_slot: int = Field(ge=1, le=16)
    player_id: int | None = Field(default=None, ge=1)
    external_player_id: str | None = Field(default=None, max_length=160)
    player_name: str | None = Field(default=None, max_length=160)


class DraftYahooSyncRequest(BaseModel):
    expected_sequence: int = Field(ge=0)
    fixture_observations: list[DraftYahooObservation] | None = Field(default=None, max_length=480)


class DraftConflictResolution(BaseModel):
    expected_sequence: int = Field(ge=0)
    action: Literal[
        "keep_canonical",
        "accept_incoming",
        "map_player_and_accept",
        "ignore_incoming",
    ]
    player_id: int | None = Field(default=None, ge=1)


class DraftSourceModeUpdate(BaseModel):
    expected_sequence: int = Field(ge=0)
    mode: Literal[
        "manual",
        "yahoo_scrape_shadow",
        "yahoo_oauth_shadow",
        "yahoo_scrape_authoritative",
        "yahoo_oauth_authoritative",
    ]
    authority_evidence_id: int | None = Field(default=None, ge=1)
    owner_confirmed: bool = False


class DraftInputCommit(BaseModel):
    content_hash: str = Field(min_length=64, max_length=64)
    acknowledge_warnings: bool = False


class DraftInputBinding(BaseModel):
    expected_sequence: int = Field(ge=0)
    projection_snapshot_id: int | None = Field(default=None, ge=1)
    ranking_snapshot_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_an_input(self) -> DraftInputBinding:
        if self.projection_snapshot_id is None and self.ranking_snapshot_id is None:
            raise ValueError("Bind at least one projection or ranking snapshot")
        return self


class DraftSimulationRequest(BaseModel):
    expected_sequence: int = Field(ge=0)
    player_ids: list[int] = Field(min_length=1, max_length=3)
    playouts: int = Field(default=200, ge=100, le=500)

    @model_validator(mode="after")
    def unique_candidates(self) -> DraftSimulationRequest:
        if len(set(self.player_ids)) != len(self.player_ids):
            raise ValueError("simulation candidate IDs must be unique")
        return self
