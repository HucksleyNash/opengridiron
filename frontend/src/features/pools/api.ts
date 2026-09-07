import { api, post, put } from "../../api";
import type { Pool, PoolEntry, PoolOverview, PoolWeek, WeeklyCard, WeeklyPickDraft } from "../../types";

export const poolKeys = {
  overview: ["pool-overview"] as const,
  entries: (poolId: number) => ["pool-entries", poolId] as const,
  week: (poolId: number, entryId: number, season: number, week: number) =>
    ["pool-week", poolId, entryId, season, week] as const,
};

export const getPoolOverview = () => api<PoolOverview>("/pools/overview");

export const getPoolWeek = (poolId: number, entryId: number, week: number) =>
  api<PoolWeek>(`/pools/${poolId}/weeks/${week}?entry_id=${entryId}`);

export const getPoolEntries = (poolId: number) =>
  api<PoolEntry[]>(`/pools/${poolId}/entries`);

export const importSchedule = (season: number, trigger: "missing" | "retry") =>
  post<Record<string, number | string>>(
    `/sync/nflverse/schedule?season=${season}&trigger=${trigger}`,
  );

export const createPoolEntry = (poolId: number, name: string) =>
  post<PoolEntry>(`/pools/${poolId}/entries`, { name });

export const createPool = (payload: Omit<Pool, "id" | "entry_count">) =>
  post<Pool>("/pools", payload);

export const saveWeeklyCard = (
  entryId: number,
  week: number,
  version: number,
  picks: WeeklyPickDraft[],
) =>
  put<{ card: WeeklyCard }>(`/entries/${entryId}/weeks/${week}/picks`, {
    version,
    picks: picks.map((pick) => ({
      game_id: pick.game_id,
      team: pick.team,
      confidence: pick.confidence,
      ...(pick.slot ? { slot: pick.slot } : {}),
    })),
  });
