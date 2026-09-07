from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OwnerSetup(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=12, max_length=256)


class LoginRequest(BaseModel):
    username: str
    password: str


class DataSnapshotOut(ORMModel):
    id: int
    source: str
    source_id: str | None
    retrieved_at: datetime
    effective_at: datetime | None
    freshness_seconds: int | None
    status: str


class Projection(BaseModel):
    subject_id: str
    subject_type: Literal["player", "game"] = "player"
    raw_stats: dict[str, float] = Field(default_factory=dict)
    fantasy_score: float | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    uncertainty: float = Field(default=0.5, ge=0, le=1)
    model_version: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    data_as_of: datetime


class LeagueCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    season: int = Field(ge=2000, le=2100)
    scoring: dict[str, float] = Field(default_factory=dict)
    roster_slots: list[str] = Field(
        default_factory=lambda: ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
    )
    faab_budget: int | None = Field(default=None, ge=0)


class LeagueMyTeamUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    my_team_name: str | None = Field(min_length=1, max_length=160)


class LeagueOut(ORMModel):
    id: int
    name: str
    season: int
    source: str
    yahoo_key: str | None
    scoring: dict[str, float] = Field(default_factory=dict)
    roster_slots: list[str] = Field(default_factory=list)
    faab_budget: int | None
    player_count: int = 0
    team_names: list[str] = Field(default_factory=list)
    team_order_source: Literal["yahoo_draft_order", "yahoo_team_id"] | None = None
    my_team_name: str | None = None


class ProjectionInput(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    source: str | None = Field(default=None, max_length=160)
    period: Literal["unknown", "season", "week", "rest_of_season"] = "unknown"
    season: int | None = Field(default=None, ge=2000, le=2100)
    week: int | None = Field(default=None, ge=1, le=30)
    source_updated_at: datetime | None = None
    scoring_basis: Literal["unknown", "source_points", "league_rules"] = "unknown"
    scoring: dict[str, float] | None = None

    @model_validator(mode="after")
    def validate_context(self) -> ProjectionInput:
        if self.period != "unknown" and self.season is None:
            raise ValueError("Projection season is required for a known period")
        if self.period == "week" and self.week is None:
            raise ValueError("Projection week is required for weekly points")
        if self.week is not None and self.period not in {"week", "rest_of_season"}:
            raise ValueError("Projection week requires a weekly or rest-of-season period")
        if self.source_updated_at and self.source_updated_at.tzinfo is None:
            raise ValueError("Source update time must include a timezone")
        if self.scoring_basis == "league_rules" and self.scoring is None:
            raise ValueError("Include scoring rules when declaring league-rules scoring")
        return self


class ProjectionContext(ProjectionInput):
    received_at: datetime | None = None
    ros_value_state: Literal["provided", "missing", "legacy_unknown"] = "legacy_unknown"


class PlayerCreate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    source_id: str | None = None
    name: str = Field(min_length=1, max_length=160)
    pro_team: str = Field(min_length=2, max_length=4)
    position: str = Field(min_length=1, max_length=16)
    status: str = "Active"
    ownership: str = "FA"
    rostered_by: str | None = None
    current_slot: str | None = None
    projected_points: float = 0
    floor: float = 0
    ceiling: float = 0
    ros_value: float | None = Field(default=None, allow_inf_nan=False)
    projection: ProjectionInput | None = None
    risk: float = Field(default=0.5, ge=0, le=1)
    evidence: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("name", mode="after")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("player name is required")
        return normalized

    @field_validator("pro_team", "position", mode="after")
    @classmethod
    def uppercase(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("team and position are required")
        return normalized


class PlayerOut(ORMModel):
    id: int
    league_id: int
    source_id: str | None
    name: str
    pro_team: str
    position: str
    status: str
    ownership: str
    rostered_by: str | None
    current_slot: str | None
    projected_points: float
    floor: float
    ceiling: float
    ros_value: float | None
    risk: float
    projection: ProjectionContext = Field(default_factory=ProjectionContext)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class DraftPickCreate(BaseModel):
    overall: int = Field(ge=1)
    round: int = Field(ge=1)
    team_name: str
    player_id: int | None = None


class DraftPickOut(ORMModel):
    id: int
    league_id: int
    overall: int
    round: int
    team_name: str
    player_id: int | None
    player_name: str | None
    source: str
    picked_at: datetime


class GameCreate(BaseModel):
    season: int = Field(ge=2000, le=2100)
    week: int = Field(ge=1, le=30)
    away_team: str = Field(min_length=2, max_length=4)
    home_team: str = Field(min_length=2, max_length=4)
    kickoff: datetime
    home_win_probability: float = Field(default=0.5, gt=0, lt=1)
    home_cover_probability: float = Field(default=0.5, gt=0, lt=1)
    spread_home: float | None = None
    total: float | None = Field(default=None, ge=0)
    source: str = "manual"

    @field_validator("away_team", "home_team", mode="after")
    @classmethod
    def uppercase(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) < 2:
            raise ValueError("team abbreviation must contain at least two characters")
        return normalized

    @model_validator(mode="after")
    def distinct_teams(self) -> GameCreate:
        if self.away_team == self.home_team:
            raise ValueError("away team and home team must be different")
        return self


class GameOut(ORMModel):
    id: int
    season: int
    week: int
    away_team: str
    home_team: str
    kickoff: datetime
    home_win_probability: float
    home_cover_probability: float
    spread_home: float | None
    total: float | None
    source: str
    source_timestamp: datetime
    source_game_key: str | None = None
    source_game_key_kind: str | None = None
    locked_at: datetime | None = None
    win_probability_kind: str = "legacy_unknown"
    cover_probability_kind: str = "legacy_unknown"
    home_score: int | None = None
    away_score: int | None = None
    completed: bool = False


class GameResultUpdate(BaseModel):
    home_score: int | None = Field(default=None, ge=0, le=100)
    away_score: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def both_scores(self) -> GameResultUpdate:
        if (self.home_score is None) != (self.away_score is None):
            raise ValueError("Provide both final scores, or neither to clear a result.")
        return self


class PoolRules(BaseModel):
    direction: Literal["winner", "loser"] = "winner"
    basis: Literal["straight_up", "against_spread"] = "straight_up"
    picks_per_week: int = Field(default=1, ge=1, le=10)
    max_team_uses: int | None = Field(default=1, ge=1)
    allowed_teams: list[str] = Field(default_factory=list)
    blocked_teams: list[str] = Field(default_factory=list)
    tie_result: Literal["survive", "eliminate", "push"] = "push"
    lock_mode: Literal["game_start", "week_start"] = "game_start"
    confidence_weights: list[int] = Field(default_factory=list)
    future_value_weight: float = Field(default=0.1, ge=0, le=1)


class PoolCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    pool_type: Literal["survivor", "confidence"]
    season: int = Field(ge=2000, le=2100)
    rules: PoolRules = Field(default_factory=PoolRules)


class PoolOut(ORMModel):
    id: int
    name: str
    pool_type: str
    season: int
    rules: PoolRules
    entry_count: int = 0


class PoolEntryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class PoolEntryOut(ORMModel):
    id: int
    pool_id: int
    name: str
    active: bool


class PoolPickCreate(BaseModel):
    game_id: int | None = None
    week: int = Field(ge=1, le=30)
    slot: int = Field(default=1, ge=1)
    team: str
    confidence: int | None = Field(default=None, ge=1)
    result: str | None = None

    @field_validator("team", mode="after")
    @classmethod
    def uppercase(cls, value: str) -> str:
        return value.upper()


class PoolPickOut(ORMModel):
    id: int
    entry_id: int
    game_id: int | None
    week: int
    slot: int
    team: str
    confidence: int | None
    result: str | None


PathSegment = str | int


class CardFinding(BaseModel):
    location: list[PathSegment] = Field(default_factory=list)
    code: str
    attempted: Any | None = None
    canonical: Any | None = None


class ScheduleStatus(BaseModel):
    state: Literal["ready", "stale", "missing"]
    source: str = "nflverse.schedule"
    last_success_at: datetime | None = None


class PoolEntrySummary(BaseModel):
    id: int
    name: str
    active: bool
    card_state: Literal["draft", "complete", "locked_incomplete", "locked_complete", "needs_repair"]
    required_count: int
    selection_count: int
    weight_count: int | None = None
    missing_count: int


class PoolOverviewItem(BaseModel):
    id: int
    name: str
    season: int
    pool_type: Literal["survivor", "confidence"]
    rules: PoolRules
    suggested_week: int
    inactive_entry_count: int
    schedule: ScheduleStatus
    entries: list[PoolEntrySummary] = Field(default_factory=list)


class PoolOverviewOut(BaseModel):
    generated_at: datetime
    pools: list[PoolOverviewItem] = Field(default_factory=list)


class WeeklyPickInput(BaseModel):
    slot: int | None = Field(default=None, ge=1)
    game_id: int
    team: str = Field(min_length=2, max_length=8)
    confidence: int | None = Field(default=None, ge=1)

    @field_validator("team", mode="after")
    @classmethod
    def normalize_team(cls, value: str) -> str:
        return value.strip().upper()


class WeeklyCardUpdate(BaseModel):
    version: int = Field(ge=0)
    picks: list[WeeklyPickInput] = Field(default_factory=list, max_length=40)


class WeeklyPickOut(BaseModel):
    id: int | None = None
    slot: int | None = None
    game_id: int | None
    team: str
    confidence: int | None = None
    locked: bool = False
    result: str | None = None
    spread_home: float | None = None
    probability: float | None = None
    probability_kind: str | None = None


class WeeklyCardOut(BaseModel):
    version: int
    state: Literal["draft", "complete", "locked_incomplete", "locked_complete", "needs_repair"]
    required_count: int
    selection_count: int
    weight_count: int | None = None
    missing_count: int
    picks: list[WeeklyPickOut] = Field(default_factory=list)
    findings: list[CardFinding] = Field(default_factory=list)


class TeamRecommendation(BaseModel):
    team: str
    score: float
    probability: float
    rationale: list[str] = Field(default_factory=list)


class PoolWeekGame(BaseModel):
    id: int
    source_game_key: str | None = None
    source_game_key_kind: str | None = None
    kickoff: datetime
    locked: bool
    away_team: str
    home_team: str
    spread_home: float | None = None
    home_score: int | None = None
    away_score: int | None = None
    completed: bool = False
    model: dict[str, Any] = Field(default_factory=dict)
    probabilities: dict[str, float | str]
    recommendations: list[TeamRecommendation] = Field(default_factory=list)
    suggested_team: str | None = None
    suggested_confidence: int | None = None


class SurvivorChoice(BaseModel):
    game_id: int
    team: str
    eligible: bool
    reason: str | None = None


class SurvivorSlot(BaseModel):
    slot: int
    current_pick: WeeklyPickOut | None = None
    choices: list[SurvivorChoice] = Field(default_factory=list)


class PoolWeekOut(BaseModel):
    pool: dict[str, Any]
    week: dict[str, Any]
    schedule: ScheduleStatus
    entry: dict[str, Any]
    card: WeeklyCardOut
    games: list[PoolWeekGame] = Field(default_factory=list)
    survivor_slots: list[SurvivorSlot] | None = None
    configuration_errors: list[CardFinding] = Field(default_factory=list)


class WeeklyCardUpdateOut(BaseModel):
    card: WeeklyCardOut


class Evidence(BaseModel):
    label: str
    value: str
    source: str
    url: str | None = None
    as_of: datetime | None = None


class Recommendation(BaseModel):
    rank: int
    action: str
    subject: str
    player_id: int | None = None
    expected_value: float
    confidence: float = Field(ge=0, le=1)
    rationale: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    data_as_of: datetime


class LineupAssignment(BaseModel):
    slot: str
    player: PlayerOut
    score: float


class LineupRecommendation(BaseModel):
    mode: Literal["floor", "balanced", "ceiling"]
    assignments: list[LineupAssignment]
    projected_total: float
    current_total: float
    projected_gain: float
    unfilled_slots: list[str] = Field(default_factory=list)
    data_as_of: datetime


class TradeRequest(BaseModel):
    outgoing_player_ids: list[int]
    incoming_player_ids: list[int]


class NewsSourceCreate(BaseModel):
    name: str
    url: HttpUrl
    source_type: Literal["rss", "html"] = "rss"
    official: bool = False


class NewsSourceOut(ORMModel):
    id: int
    name: str
    url: str
    source_type: str
    enabled: bool
    official: bool
    last_fetched_at: datetime | None


class NewsItemOut(ORMModel):
    id: int
    canonical_url: str
    title: str
    excerpt: str
    category: str
    severity: str
    published_at: datetime | None
    retrieved_at: datetime


class PlayerArticle(BaseModel):
    title: str
    url: str
    excerpt: str = ""
    source: str
    category: str
    published_at: datetime | None = None
    retrieved_at: datetime


class PlayerInjuryReport(BaseModel):
    player_name: str
    team: str
    injury: str
    practice_status: str
    game_status: str
    report_period: str
    url: str
    retrieved_at: datetime


class PlayerReportSource(BaseModel):
    name: str
    url: str
    status: Literal["ok", "stale", "unavailable"]
    checked_at: datetime
    fetched_at: datetime | None = None
    message: str | None = None


class PlayerSynopsisOut(BaseModel):
    player: PlayerOut
    synopsis: str
    injury_reports: list[PlayerInjuryReport]
    articles: list[PlayerArticle]
    sources: list[PlayerReportSource]
    news_window_days: int = 30


class ProviderCreate(BaseModel):
    name: str
    provider_type: Literal["openai", "anthropic", "openai_compatible", "codex"]
    model: str
    base_url: str | None = None
    api_key: str | None = None
    enabled: bool = True
    task_defaults: list[Literal["chat", "recommendation", "news"]] = Field(default_factory=list)


class ProviderModelsRequest(BaseModel):
    provider_type: Literal["openai", "anthropic", "codex"]
    api_key: str | None = Field(default=None, max_length=1000)


class ProviderOut(ORMModel):
    id: int
    name: str
    provider_type: str
    model: str
    base_url: str | None
    enabled: bool
    task_defaults: list[str] = Field(default_factory=list)
    has_api_key: bool = False


class ProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    base_url: str | None = None
    api_key: str | None = Field(default=None, max_length=1000)
    enabled: bool | None = None
    task_defaults: list[Literal["chat", "recommendation", "news"]] | None = None

    @model_validator(mode="after")
    def nonnull_fields(self) -> ProviderUpdate:
        for key in ("name", "model", "enabled", "task_defaults"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        for key in ("name", "model"):
            value = getattr(self, key)
            if value is not None:
                value = value.strip()
                if not value:
                    raise ValueError(f"{key} cannot be blank")
                setattr(self, key, value)
        return self


class AnalysisRequest(BaseModel):
    task: Literal["chat", "recommendation", "news"] = "chat"
    provider_id: int | None = Field(default=None, ge=1)
    question: str = Field(min_length=1, max_length=10000)
    league_id: int | None = Field(default=None, ge=1)
    draft_session_id: int | None = Field(default=None, ge=1)
    pool_id: int | None = Field(default=None, ge=1)
    pool_entry_id: int | None = Field(default=None, ge=1)
    league_report_id: int | None = Field(default=None, ge=1)
    parent_run_id: int | None = Field(default=None, ge=1)
    team_name: str | None = Field(default=None, min_length=1, max_length=160)
    week: int | None = Field(default=None, ge=1, le=18)


class AnalysisResult(BaseModel):
    run_id: int
    provider: str
    model: str
    status: str
    output: dict[str, Any] | None = None
    error: str | None = None
    parent_run_id: int | None = None
    league_report_id: int | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class AnalysisRunOut(BaseModel):
    id: int
    task: str
    question: str
    provider: str | None = None
    model: str
    status: str
    output: dict[str, Any] | None = None
    error: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    created_at: datetime
    completed_at: datetime | None = None
    parent_run_id: int | None = None
    league_report_id: int | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class YahooSettings(BaseModel):
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)
    redirect_uri: str = Field(min_length=1)

    @field_validator("client_id", "client_secret", "redirect_uri", mode="after")
    @classmethod
    def nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Yahoo credentials and redirect URI cannot be blank")
        return normalized


class YahooScraperSettings(BaseModel):
    league_urls: list[str] = Field(min_length=1, max_length=20)
    cookie: str | None = Field(default=None, max_length=65536)

    @field_validator("league_urls", mode="after")
    @classmethod
    def nonblank_urls(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if not normalized:
            raise ValueError("At least one Yahoo league URL is required")
        return normalized


class PushSubscriptionCreate(BaseModel):
    endpoint: str
    keys: dict[str, str]
