import type { League } from "../../types";

export type DraftStatus = "SETUP" | "READY" | "LIVE" | "PAUSED" | "COMPLETE" | "ABANDONED";

export type DraftTeam = {
  id: number;
  slot: number;
  name: string;
  is_owner: boolean;
  provider_team_key?: string;
  roster?: DraftPick[];
};

export type DraftSession = {
  id: number;
  league_id: number;
  league_name: string;
  kind: "live" | "mock";
  opponent_mode: "manual" | "automatic";
  format: "snake";
  status: DraftStatus;
  strategy_mode: "adaptive" | "locked";
  team_count: number;
  round_count: number;
  owner_team_slot: number;
  projection_snapshot_id?: number;
  ranking_snapshot_id?: number;
  source_mode: string;
  current_sequence: number;
  preference_revision: number;
  replay_generation: number;
  teams: DraftTeam[];
  readiness: {
    ready: boolean;
    findings: Array<{ code: string; message: string }>;
  };
  created_at: string;
  started_at?: string;
  completed_at?: string;
  archived_at?: string;
};

export type DraftPlayer = {
  id: number;
  athlete_id: number;
  name: string;
  pro_team: string;
  bye_week?: number;
  position: string;
  projected_points: number;
  floor: number;
  ceiling: number;
  risk: number;
  queue_rank?: number;
  target: boolean;
  fade: boolean;
  note?: string;
};

export type DraftPick = {
  event_id: number;
  sequence: number;
  overall_pick: number;
  round: number;
  team_slot: number;
  player_id: number;
  player_name: string;
  position?: string;
  pro_team?: string;
  bye_week?: number;
  projected_points?: number;
  source: string;
  recommendation_snapshot_id?: number;
};

export type DraftBoard = {
  session_id: number;
  status: DraftStatus;
  current_sequence: number;
  preference_revision: number;
  total_picks: number;
  completed_picks: number;
  current_overall_pick?: number;
  current_round?: number;
  current_team_slot?: number;
  owner_on_clock: boolean;
  opponent_mode: "manual" | "automatic";
  next_owner_pick?: number;
  teams: DraftTeam[];
  picks: DraftPick[];
  available_players: DraftPlayer[];
  queue: DraftPlayer[];
  freshness: {
    source_mode: string;
    projection_snapshot_id?: number;
    provisional: boolean;
  };
};

export type RecommendationCandidate = {
  player_id: number;
  athlete_id: number;
  name: string;
  pro_team: string;
  bye_week?: number;
  position: string;
  projected_points: number;
  floor: number;
  ceiling: number;
  risk: number;
  range_model?: {
    source: string;
    model_version: string;
    confidence: "high" | "medium" | "low";
    range_definition: string;
    risk_definition: string;
    risk_factors: string[];
  };
  score: number;
  vor: number;
  tier_cliff: boolean;
  vor_drop: number;
  why_now: string;
  roster_impact: string;
  tradeoff: string;
  evidence?: Array<{
    id: number;
    title: string;
    category: string;
    severity: string;
    url: string;
    source: string;
    official: boolean;
    published_at: string;
    stale: boolean;
    score_effect: 0;
  }>;
  next_turn: { status: string; label: string };
  components: {
    normalized_vor: number;
    roster_need: number;
    depth_need: number;
    lineup_delta: number;
    tier_urgency: number;
    risk_adjustment: number;
    market_value?: number;
    market_rank?: number;
    weights: Record<string, number>;
  };
};

export type DraftRecommendations = {
  snapshot_id?: number;
  status: "ready" | "unavailable" | "computing";
  session_sequence: number;
  algorithm_version: string;
  candidates: RecommendationCandidate[];
  alternatives: RecommendationCandidate[];
  forecast_status: string;
  next_owner_pick?: number;
  freshness: { positional_run?: string; projection_snapshot_id?: number };
};

export type DraftReplay = {
  session_id: number;
  status: DraftStatus;
  replay_generation: number;
  message: string;
  decisions: Array<{
    event: { id: number; overall_pick: number; player_id?: number; player_name: string };
    snapshot_id: number;
    candidates: RecommendationCandidate[];
    decision_quality: number;
    at_time: boolean;
  }>;
  coaching: {
    status: "ready" | "insufficient_history";
    minimum_linked_decisions: number;
    lane?: "mock" | "live";
    findings: Array<{
      code: string;
      observation: string;
      exercise: string;
      evidence_event_ids: number[];
      version: string;
    }>;
  };
  waiver_priorities: DraftPlayer[];
  waiver_moves: Array<{
    add: DraftPlayer;
    drop: DraftPick;
    projected_point_gain: number;
    reason: string;
  }>;
};

export type DraftConflict = {
  id: number;
  overall_pick: number;
  status: "unresolved" | "resolved" | "ignored";
  canonical_event_id?: number;
  incoming: {
    player_id?: number;
    player_name?: string;
    team_slot: number;
    provider_key: string;
  };
  detected_sequence: number;
  resolution_action?: string;
  created_at: string;
  resolved_at?: string;
};

export type DraftSimulation = {
  run_id: number;
  status: "queued" | "running" | "ready" | "unavailable" | "stale";
  reason?: string;
  playouts?: number;
  results: Array<{
    player_id: number;
    name: string;
    mean_utility: number;
    lower_utility: number;
    upper_utility: number;
    completed_playouts: number;
  }>;
};

export type DraftExposure = {
  session_id: number;
  total_other_leagues: number;
  strategy_enabled: boolean;
  players: Array<{
    player_id: number;
    athlete_id: number;
    name: string;
    league_count: number;
    share: number;
    leagues: Array<{
      id: number;
      name: string;
      league_player_id: number;
      source_id: string | null;
      rostered_by: string;
    }>;
    informational_only: true;
  }>;
};

export type DraftRankingSync = {
  status: "ready" | "already_running";
  ranking_snapshot_id?: number;
  row_count?: number;
  coverage?: number;
  source?: string;
  retrieved_at?: string;
  session: DraftSession;
};

export type DraftRouteLeague = Pick<League, "id" | "name" | "season" | "source" | "player_count" | "yahoo_key" | "team_order_source" | "my_team_name"> & {
  team_names?: string[];
};
