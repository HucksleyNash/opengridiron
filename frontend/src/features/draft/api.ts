import { api, post, put } from "../../api";
import type {
  DraftBoard,
  DraftConflict,
  DraftExposure,
  DraftPick,
  DraftRecommendations,
  DraftRankingSync,
  DraftReplay,
  DraftSimulation,
  DraftSession,
} from "./types";

export type DraftSessionInput = {
  kind: "live" | "mock";
  team_count: number;
  round_count: number;
  owner_team_slot: number;
  owner_team_name: string;
  source_mode: "manual" | "yahoo_scrape_shadow" | "yahoo_oauth_shadow";
  opponent_mode: "manual" | "automatic";
};

export type YahooLeagueSync = {
  league_id: number;
  players: number;
  draft_order_teams: number;
  draft_picks: number;
  partial: number;
};

export type YahooDraftOrderRefresh = {
  teamNames: string[];
  orderVerified: boolean;
  sessionUpdated: boolean;
  ownerMatchRequired: boolean;
};

export const draftApi = {
  sessions: (leagueId: number) =>
    api<DraftSession[]>(`/leagues/${leagueId}/draft-sessions`),
  createSession: (leagueId: number, input: DraftSessionInput) =>
    post<DraftSession>(`/leagues/${leagueId}/draft-sessions`, input),
  syncRankings: (
    leagueId: number,
    sessionId: number,
    expectedSequence: number,
    refresh = false,
  ) => post<DraftRankingSync>(`/leagues/${leagueId}/draft-rankings/sync`, {
    session_id: sessionId,
    expected_sequence: expectedSequence,
    refresh,
  }),
  syncYahooLeague: (leagueId: number) =>
    post<YahooLeagueSync>(`/leagues/${leagueId}/sync/yahoo-scraper`),
  session: (sessionId: number) => api<DraftSession>(`/draft-sessions/${sessionId}`),
  updateSetup: (
    sessionId: number,
    input: {
      expectedSequence: number;
      teamCount: number;
      roundCount: number;
      ownerTeamSlot: number;
      teamNames: string[];
    },
  ) => put<DraftSession>(`/draft-sessions/${sessionId}/setup`, {
    expected_sequence: input.expectedSequence,
    team_count: input.teamCount,
    round_count: input.roundCount,
    owner_team_slot: input.ownerTeamSlot,
    team_names: input.teamNames,
  }),
  archiveSession: (sessionId: number, expectedSequence: number, archived: boolean) =>
    put<DraftSession>(`/draft-sessions/${sessionId}/archive`, {
      expected_sequence: expectedSequence,
      archived,
    }),
  board: (sessionId: number) => api<DraftBoard>(`/draft-sessions/${sessionId}/board`),
  recommendations: (sessionId: number) =>
    api<DraftRecommendations>(`/draft-sessions/${sessionId}/recommendations`),
  replay: (sessionId: number) => api<DraftReplay>(`/draft-sessions/${sessionId}/replay`),
  conflicts: (sessionId: number) =>
    api<DraftConflict[]>(`/draft-sessions/${sessionId}/conflicts`),
  action: (
    sessionId: number,
    action: "start" | "pause" | "resume" | "complete" | "abandon" | "reopen",
    expectedSequence: number,
    reason?: string,
  ) =>
    post<{ session: DraftSession }>(`/draft-sessions/${sessionId}/actions/${action}`, {
      expected_sequence: expectedSequence,
      idempotency_key: crypto.randomUUID(),
      reason,
    }),
  recordPick: (
    sessionId: number,
    input: {
      expectedSequence: number;
      playerId: number;
      recommendationSnapshotId?: number;
    },
  ) =>
    post<{ session: DraftSession }>(`/draft-sessions/${sessionId}/events`, {
      type: "pick_recorded",
      expected_sequence: input.expectedSequence,
      idempotency_key: crypto.randomUUID(),
      player_id: input.playerId,
      recommendation_snapshot_id: input.recommendationSnapshotId,
    }),
  advanceOpponent: (sessionId: number, expectedSequence: number) =>
    post<{ session: DraftSession; board: DraftBoard; event: DraftPick }>(
      `/draft-sessions/${sessionId}/opponent-picks/next`,
      {
        expected_sequence: expectedSequence,
        idempotency_key: `mock-auto-${sessionId}-${expectedSequence}`,
      },
    ),
  undoPick: (sessionId: number, expectedSequence: number, targetEventId: number) =>
    post<{ session: DraftSession }>(`/draft-sessions/${sessionId}/events`, {
      type: "pick_reversed",
      expected_sequence: expectedSequence,
      idempotency_key: crypto.randomUUID(),
      target_event_id: targetEventId,
      reason: "Owner corrected the latest manual entry",
    }),
  replacePick: (
    sessionId: number,
    input: { expectedSequence: number; targetEventId: number; playerId: number },
  ) =>
    post<{ session: DraftSession }>(`/draft-sessions/${sessionId}/events`, {
      type: "pick_replaced",
      expected_sequence: input.expectedSequence,
      idempotency_key: crypto.randomUUID(),
      target_event_id: input.targetEventId,
      player_id: input.playerId,
      reason: "Owner replaced an incorrect manual entry",
    }),
  saveQueue: (
    sessionId: number,
    revision: number,
    items: Array<{ player_id: number; queue_rank: number }>,
  ) =>
    put<{ revision: number }>(`/draft-sessions/${sessionId}/preferences`, {
      expected_revision: revision,
      items,
    }),
  sourceMode: (
    sessionId: number,
    expectedSequence: number,
    mode: "manual" | "yahoo_scrape_shadow" | "yahoo_oauth_shadow" | "yahoo_scrape_authoritative" | "yahoo_oauth_authoritative",
    ownerConfirmed = false,
  ) => post<DraftSession>(`/draft-sessions/${sessionId}/source-mode`, {
    expected_sequence: expectedSequence,
    mode,
    owner_confirmed: ownerConfirmed,
  }),
  syncYahoo: (sessionId: number, expectedSequence: number) =>
    post<{ counts?: { applied: number; confirmed: number; proposed: number }; status?: string }>(
      `/draft-sessions/${sessionId}/sync/yahoo`,
      { expected_sequence: expectedSequence },
    ),
  resolveConflict: (
    sessionId: number,
    conflictId: number,
    expectedSequence: number,
    action: "keep_canonical" | "accept_incoming" | "ignore_incoming",
  ) => post(`/draft-sessions/${sessionId}/conflicts/${conflictId}/resolve`, {
    expected_sequence: expectedSequence,
    action,
  }),
  simulate: (sessionId: number, expectedSequence: number, playerIds: number[]) =>
    post<DraftSimulation>(`/draft-sessions/${sessionId}/simulations`, {
      expected_sequence: expectedSequence,
      player_ids: playerIds,
      playouts: 200,
    }),
  computation: (sessionId: number, runId: number) =>
    api<DraftSimulation>(`/draft-sessions/${sessionId}/computations/${runId}`),
  exposure: (sessionId: number) =>
    api<DraftExposure>(`/draft-sessions/${sessionId}/exposure`),
};
