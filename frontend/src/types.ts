export type League = {
  id: number;
  name: string;
  season: number;
  source: string;
  yahoo_key?: string;
  team_names?: string[];
  team_order_source?: "yahoo_draft_order" | "yahoo_team_id" | null;
  my_team_name?: string | null;
  scoring: Record<string, number>;
  roster_slots: string[];
  faab_budget?: number;
  player_count: number;
};

export type ProjectionContext = {
  source: string | null;
  period: "unknown" | "season" | "week" | "rest_of_season";
  season: number | null;
  week: number | null;
  source_updated_at: string | null;
  received_at: string | null;
  scoring_basis: "unknown" | "source_points" | "league_rules";
  scoring: Record<string, number> | null;
  ros_value_state: "provided" | "missing" | "legacy_unknown";
};

export type Player = {
  id: number;
  league_id: number;
  name: string;
  pro_team: string;
  position: string;
  status: string;
  ownership: string;
  rostered_by?: string;
  current_slot?: string;
  projected_points: number;
  floor: number;
  ceiling: number;
  ros_value: number | null;
  projection?: ProjectionContext;
  risk: number;
};

export type Pool = {
  id: number;
  name: string;
  pool_type: "survivor" | "confidence";
  season: number;
  entry_count: number;
  rules: {
    direction: "winner" | "loser";
    basis: "straight_up" | "against_spread";
    picks_per_week: number;
    max_team_uses?: number;
    allowed_teams: string[];
    blocked_teams: string[];
    tie_result: string;
    lock_mode: string;
    confidence_weights: number[];
    future_value_weight: number;
  };
};

export type Game = {
  id: number;
  season: number;
  week: number;
  away_team: string;
  home_team: string;
  kickoff: string;
  home_win_probability: number;
  home_cover_probability: number;
  spread_home?: number;
  total?: number;
  source: string;
  source_timestamp: string;
  source_game_key?: string;
  source_game_key_kind?: string;
  locked_at?: string;
  win_probability_kind: string;
  cover_probability_kind: string;
};

export type PoolRules = Pool["rules"];
export type CardState = "draft" | "complete" | "locked_incomplete" | "locked_complete" | "needs_repair";

export type ScheduleStatus = {
  state: "ready" | "stale" | "missing";
  source: string;
  last_success_at?: string;
};

export type PoolEntrySummary = {
  id: number;
  name: string;
  active: boolean;
  card_state: CardState;
  required_count: number;
  selection_count: number;
  weight_count?: number | null;
  missing_count: number;
};

export type PoolOverviewItem = Omit<Pool, "entry_count"> & {
  suggested_week: number;
  inactive_entry_count: number;
  schedule: ScheduleStatus;
  entries: PoolEntrySummary[];
};

export type PoolOverview = {
  generated_at: string;
  pools: PoolOverviewItem[];
};

export type PoolEntry = {
  id: number;
  pool_id: number;
  name: string;
  active: boolean;
};

export type CardFinding = {
  location: Array<string | number>;
  code: string;
  attempted?: unknown;
  canonical?: unknown;
};

export type WeeklyPick = {
  id?: number;
  slot: number;
  game_id?: number;
  team: string;
  confidence?: number;
  locked?: boolean;
  result?: string | null;
  spread_home?: number | null;
  probability?: number | null;
  probability_kind?: string | null;
};

export type WeeklyPickDraft = {
  slot?: number;
  game_id: number;
  team: string;
  confidence: number | null;
};

export type WeeklyCard = {
  version: number;
  state: CardState;
  required_count: number;
  selection_count: number;
  weight_count?: number | null;
  missing_count: number;
  picks: WeeklyPick[];
  findings: CardFinding[];
};

export type TeamRecommendation = {
  team: string;
  score: number;
  probability: number;
  rationale: string[];
};

export type AnalysisOutput = {
  summary: string;
  recommendations: string[];
  risks: string[];
  missing_information: string[];
  citations: string[];
};

export type AnalysisResult = {
  run_id: number;
  provider: string;
  model: string;
  status: string;
  output?: AnalysisOutput;
  error?: string;
  context?: Record<string, unknown>;
  league_report_id?: number | null;
  parent_run_id?: number | null;
};

export type AnalysisRun = {
  id: number;
  task: string;
  question: string;
  provider?: string;
  model: string;
  status: string;
  output?: AnalysisOutput;
  error?: string;
  input_tokens?: number;
  output_tokens?: number;
  created_at: string;
  completed_at?: string;
  context?: Record<string, unknown>;
  league_report_id?: number | null;
  parent_run_id?: number | null;
};

export type PoolWeekGame = {
  id: number;
  source_game_key?: string;
  source_game_key_kind?: string;
  kickoff: string;
  locked: boolean;
  away_team: string;
  home_team: string;
  spread_home?: number;
  home_score?: number | null;
  away_score?: number | null;
  completed?: boolean;
  model?: Record<string, unknown>;
  probabilities: {
    home_win: number;
    win_kind: string;
    home_cover: number;
    cover_kind: string;
  };
  recommendations: TeamRecommendation[];
  suggested_team?: string;
  suggested_confidence?: number;
};

export type SurvivorChoice = {
  game_id: number;
  team: string;
  eligible: boolean;
  reason?: string;
};

export type SurvivorSlot = {
  slot: number;
  current_pick?: WeeklyPick;
  choices: SurvivorChoice[];
};

export type PoolWeek = {
  pool: Pool;
  week: {
    number: number;
    suggested_week: number;
    first_kickoff?: string;
    last_kickoff?: string;
  };
  schedule: ScheduleStatus;
  entry: PoolEntry & { read_only: boolean };
  card: WeeklyCard;
  games: PoolWeekGame[];
  survivor_slots?: SurvivorSlot[];
  configuration_errors: CardFinding[];
};

export type Provider = {
  id: number;
  name: string;
  provider_type: string;
  model: string;
  base_url?: string;
  enabled: boolean;
  task_defaults: string[];
  has_api_key: boolean;
};
