import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { api } from "../../api";
import { points, sourceTime } from "./league-display";

type PointValue = {
  points: number | null;
  state: string;
  reason: string | null;
  source: string;
  captured_at: string | null;
  warnings: string[];
};
type PointWeek = {
  week: number;
  opponent: string | null;
  kickoff: string | null;
  home: boolean | null;
  state: string;
  opengridiron: PointValue;
  yahoo: PointValue;
  actual: PointValue;
};
type PlayerPointsData = {
  player_id: number;
  league_id: number;
  league_name: string;
  season: number;
  current_week: number;
  scoring: Record<string, number>;
  generated_at: string;
  sources: Array<{ name: string; status: string; received_at?: string | null; detail?: string }>;
  weeks: PointWeek[];
};

const STATE_LABELS: Record<string, string> = {
  not_saved: "Not saved", not_published: "Not published", unavailable: "Unavailable",
  pending: "Pending", bye: "Bye", upcoming: "Upcoming", final: "Final", unknown: "Unknown",
};

function PointCell({ value, week }: { value: PointValue; week: number }) {
  const available = value.points !== null;
  const warnings = value.warnings || [];
  return <td className={`player-points-value ${available ? "" : "is-unavailable"}`}>
    <span title={value.reason || undefined}>{available ? points(value.points) : STATE_LABELS[value.state] || "Unavailable"}</span>
    {warnings.length > 0 && <button type="button" className="player-points-warning" aria-label={`${value.source} week ${week}: review scoring or source warning`} onClick={() => {
      const evidence = document.getElementById(`player-points-evidence-${week}`);
      const disclosure = evidence?.closest("details");
      if (disclosure) disclosure.open = true;
      evidence?.focus();
      evidence?.scrollIntoView({ block: "start" });
    }}>Review</button>}
  </td>;
}

export function PlayerPoints({ playerId }: { playerId: number }) {
  const queryClient = useQueryClient();
  const queryKey = ["player-points", playerId];
  const query = useQuery({
    queryKey, queryFn: ({ signal }) => api<PlayerPointsData>(`/players/${playerId}/points`, { signal }),
    staleTime: 5 * 60_000, retry: false,
  });
  const refresh = useMutation({
    mutationFn: () => api<PlayerPointsData>(`/players/${playerId}/points?refresh=true`),
    onSuccess: (data) => queryClient.setQueryData(queryKey, data),
  });
  const data = query.data;
  const busy = query.isFetching || refresh.isPending;
  const error = refresh.error || query.error;
  const unavailableSources = data?.sources.filter((source) => ["unavailable", "stale", "error"].includes(source.status)) || [];

  return <section className="player-points" aria-labelledby="player-points-title">
    <div className="player-points-heading"><div><h3 id="player-points-title">Weekly points</h3>
      {data && <p className="player-report-meta">{data.league_name} · {data.season} season · League scoring</p>}
    </div><button type="button" className="ghost" disabled={busy} onClick={() => refresh.mutate()}><RefreshCw size={15} className={busy ? "spin" : undefined} />{busy ? "Loading points…" : "Refresh points"}</button></div>
    {!data && busy && <p role="status">Loading weekly projections and actual points…</p>}
    {error && <div role="alert" className="league-error"><strong>Could not load weekly points</strong><p>{error.message}</p><button type="button" disabled={busy} onClick={() => { refresh.reset(); void query.refetch(); }}>Try again</button></div>}
    {data && <>
      <p>Compare both projections with points scored each week.</p>
      {busy && <p role="status" className="league-caption">Updating points; saved values remain visible.</p>}
      {unavailableSources.length > 0 && <p role="status" className="league-warning">Some sources are unavailable or out of date: {unavailableSources.map((source) => source.name).join(", ")}. Available saved points are shown.</p>}
      {data.weeks.every((week) => week.state === "unknown") && <p role="status">No verified weekly schedule is available for this player.</p>}
      {!Object.keys(data.scoring).length && <p className="league-warning">Configure this league’s scoring to calculate actual points and independent forecasts.</p>}
      <div className="player-points-table-wrap">
        <table className="player-points-table" aria-label="Weekly projected and actual fantasy points">
          <thead><tr><th scope="col">Week /<br />opponent</th><th scope="col"><span>Open Gridiron</span><small>projected</small></th><th scope="col">Yahoo<small>projected</small></th><th scope="col">Actual<small>points</small></th></tr></thead>
          <tbody>{data.weeks.map((week) => <tr key={week.week} className={week.week === data.current_week ? "is-current" : undefined}>
            <th scope="row"><span>{week.week}</span><small>{week.state === "bye" ? "Bye" : week.opponent ? `${week.home ? "vs" : "@"} ${week.opponent}` : "Unknown"}</small>{week.state === "final" && <small>Final</small>}{week.week === data.current_week && <span className="sr-only">Current week</span>}</th>
            <PointCell value={week.opengridiron} week={week.week} /><PointCell value={week.yahoo} week={week.week} /><PointCell value={week.actual} week={week.week} />
          </tr>)}</tbody>
        </table>
      </div>
      <p className="league-caption">Past projections use saved pregame values. Future projections can change. Actual points reflect the latest available stats; they are not live scores. Yahoo coverage may not include every player or week.</p>
      <details className="player-points-sources"><summary>Scoring & sources</summary>
        <p className="league-caption">Checked {sourceTime(data.generated_at)}. Refresh points checks cached NFL inputs and the current Yahoo week, subject to source refresh intervals.</p>
        <dl className="player-points-scoring">{Object.entries(data.scoring).map(([stat, weight]) => <div key={stat}><dt>{stat.replaceAll("_", " ")}</dt><dd>{weight}</dd></div>)}</dl>
        {data.sources.map((source, index) => <p className="league-caption" key={`${source.name}-${index}`}><strong>{source.name}</strong> · {source.status} · {sourceTime(source.received_at)}{source.detail && <> · {source.detail}</>}</p>)}
      </details>
      <details className="player-points-sources"><summary>Weekly point details</summary>
        {data.weeks.map((week) => <div className="player-points-evidence" id={`player-points-evidence-${week.week}`} key={week.week} tabIndex={-1}>
          <h4>Week {week.week}{week.opponent ? ` · ${week.home ? "vs" : "@"} ${week.opponent}` : ""}</h4>
          {week.kickoff && <p className="league-caption">Kickoff {sourceTime(week.kickoff)}</p>}
          {[week.opengridiron, week.yahoo, week.actual].map((value) => <p className="league-caption" key={value.source}><strong>{value.source}</strong> · {value.points === null ? STATE_LABELS[value.state] || "Unavailable" : `${points(value.points)} pts`}{value.captured_at && <> · Saved {sourceTime(value.captured_at)}</>}{value.reason && <> · {value.reason}</>}{value.warnings.map((warning) => <span className="player-points-evidence-warning" key={warning}>{warning}</span>)}</p>)}
        </div>)}
      </details>
    </>}
  </section>;
}
