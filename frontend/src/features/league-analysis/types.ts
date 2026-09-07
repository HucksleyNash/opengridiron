import type { AnalysisOutput, ProjectionContext } from "../../types";

export type Forecast = {
  player_id: number; name: string; team: string; position: string; rostered_by: string | null;
  current_slot: string | null; status: string; locked: boolean; conditional: boolean;
  points: number | null; floor: number | null; ceiling: number | null;
  confidence: string; reason: string | null; warnings: string[]; sample_games: number;
  recent_usage: number | null; baseline_usage: number | null; opponent: string | null;
  source_projection: ProjectionContext & { points: number; comparable: boolean };
  difference: number | null;
};
export type AnalysisSummary = {
  id: number; league_id: number; team_name: string; season: number; week: number;
  status: string; created_at: string; completed_at: string | null; has_report: boolean; error: string | null;
};
export type WeeklyReport = {
  model_version: string; generated_at: string; season: number; week: number; team_name: string;
  method: string; limitations: string[]; changes: string[];
  coverage: { modeled: number; players: number }; forecasts: Forecast[];
  league_coverage: { team: string; modeled: number; players: number }[];
  league_comparison?: { ranked_teams: number; total_teams: number; note: string; teams: { team: string; rank: number | null; optimized_points: number | null; current_points: number | null; gain: number | null; complete: boolean; projected_starters: number; starting_slots: number; reason: string | null; conditional_players: string[] }[] };
  lineup: {
    error: string | null; current_points?: number; recommended_points?: number; gain?: number;
    partial_total?: boolean; unfilled_slots?: string[]; bench?: string[];
    assignments: { slot: string; player_id: number; name: string; points: number | null; action: string; reason: string | null; conditional: boolean }[];
    waivers: { add_id: number; add: string; drop_id: number | null; drop: string; gain: number; conditional: boolean; reason: string; drop_candidates?: { player_id: number; name: string; weekly_points: number; ros_value: number | null }[] }[];
  };
  sources: { name: string; status: string; url?: string; received_at?: string | null; detail?: string }[];
  analysis: { provider?: string; model?: string; status: string; output?: AnalysisOutput | null; error?: string } | null;
  evaluation: { scored_forecasts: number; mae: number | null; comparison_count: number; paired_model_mae: number | null; source_mae: number | null; note: string };
};
export type SavedAnalysis = AnalysisSummary & { report: WeeklyReport | null; stale_reasons: string[] };
