type DraftLeague = { id: number };
type AnalysisDraftSession = {
  status: string;
  archived_at?: string | null;
};

const ANALYSIS_SESSION_PRIORITY = ["LIVE", "PAUSED", "READY", "SETUP", "COMPLETE"];

export function resolveDraftLeague<T extends DraftLeague>(
  routeLeagueId: string | undefined,
  leagues: readonly T[],
): T | undefined {
  if (routeLeagueId === undefined) return leagues[0];
  const requestedId = Number(routeLeagueId);
  if (!Number.isInteger(requestedId) || requestedId <= 0) return undefined;
  return leagues.find((league) => league.id === requestedId);
}

export function preferredAnalysisDraftSession<T extends AnalysisDraftSession>(
  sessions: readonly T[],
): T | undefined {
  const available = sessions.filter(
    (session) => !session.archived_at && session.status !== "ABANDONED",
  );
  for (const status of ANALYSIS_SESSION_PRIORITY) {
    const match = available.find((session) => session.status === status);
    if (match) return match;
  }
  return undefined;
}
