import type { Forecast } from "../league-analysis/types";

export function RosterGameTime({ forecast, loading = false }: { forecast?: Forecast; loading?: boolean }) {
  const kickoff = forecast?.kickoff;
  // The API supplies UTC instants. Reject ambiguous timestamps without an offset.
  const valid = kickoff && /(?:Z|[+-]\d{2}:\d{2})$/i.test(kickoff) && Number.isFinite(Date.parse(kickoff));
  const label = valid ? new Intl.DateTimeFormat(undefined, {
    weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
  }).format(new Date(kickoff)) : null;
  return <span className="league-roster-game">
    {forecast?.bye ? "Bye week" : <>
      {forecast?.opponent && <span>vs {forecast.opponent} · </span>}
      {label ? <time dateTime={kickoff ?? undefined}>{label}</time> : loading ? "Loading game time…" : "Game time unavailable"}
    </>}
  </span>;
}
