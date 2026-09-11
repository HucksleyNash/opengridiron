export type Source = { name: string; url: string; status: "ok" | "stale" | "unavailable"; fetched_at?: string | null; checked_at?: string | null; message?: string | null };
export type TargetGame = { id: number; season: number; week: number; home_team: string; away_team: string; kickoff: string };
export type Membership = { player_id: number; league_id: number; league_name: string; fantasy_team?: string | null; is_mine: boolean; status: string; slot?: string | null };
export type OfficialReport = { player_name: string; team: string; injury: string; practice_status: string; game_status: string; report_period: string; url: string; retrieved_at: string };
export type Supplemental = { source: string; url: string; status?: string | null; injury?: string | null; practice?: string | null; retrieved_at?: string | null; source_status: string };
export type InjuryRow = {
  key: string; name: string; team: string; position: string; injury: string; practice_status: string; game_status: string; status_source: string;
  official_reports: OfficialReport[]; supplemental: Supplemental[]; memberships: Membership[]; is_mine: boolean; next_game: TargetGame | null;
};
export type InjuryBoard = { season: number; items: InjuryRow[]; total: number; available: number; my_players: number; checked_at: string; sources: Source[]; leagues: { id: number; name: string; my_team_name?: string | null }[] };
export type Article = { title: string; url: string; source: string; excerpt: string; category: string; published_at?: string | null; retrieved_at: string };
export type InjuryDetail = InjuryRow & { articles: Article[]; sources: Source[] };
export type Evidence = { id: string; title: string; kind: string; url: string; published_at?: string | null; retrieved_at?: string | null };
export type Finding = { text: string; evidence_ids: string[] };
export type InjuryCheck = {
  id: number; key: string; status: string; model: string; created_at: string; completed_at?: string | null; target_game: TargetGame | null;
  evidence: Evidence[]; error?: string | null; stale: boolean;
  output?: { summary: string; outlook: string; confidence: string; availability: Finding; workload: Finding; fantasy_advice: Finding; next_update: string; missing_information: string[] } | null;
};
