import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import type { PoolWeek } from "../../types";

type Standing = { entry_id: number; name: string; status: string; eliminated_week?: number; wins: number; losses: number; pushes: number; points: number; ungraded: number; pending: number };
type Plan = { entry_id: number; name: string; status: string; missing_weeks?: number[]; picks: { week: number; team: string; probability: number | null; kind: string; saved: boolean }[] };

export function PoolIntelligence({ data }: { data: PoolWeek }) {
  const results = useQuery({ queryKey: ["pool-standings", data.pool.id, data.card.version], queryFn: () => api<{ entries: Standing[]; grading: string }>(`/pools/${data.pool.id}/standings`), refetchInterval: 60000 });
  const strategy = useQuery({ queryKey: ["pool-strategy", data.pool.id, data.week.number, data.card.version], queryFn: () => api<{ entries: Plan[]; limitations: string }>(`/pools/${data.pool.id}/strategy?start_week=${data.week.number}`), enabled: data.pool.pool_type === "survivor" && data.week.number <= 18, staleTime: 60000 });
  return <>
    <section className="panel" aria-label="Pool results"><h2>Results and standings</h2>
      {results.error && <p role="alert">{results.error.message}</p>}
      <div className="table-wrap"><table><thead><tr><th>Entry</th><th>Status</th><th>W / L / Push</th><th>Points</th><th>Pending / ungraded</th></tr></thead><tbody>{(results.data?.entries || []).map((entry) => <tr key={entry.entry_id}><td>{entry.name}</td><td>{entry.status}{entry.eliminated_week ? ` · Week ${entry.eliminated_week}` : ""}</td><td>{entry.wins} / {entry.losses} / {entry.pushes}</td><td>{entry.points}</td><td>{entry.pending} / {entry.ungraded}</td></tr>)}</tbody></table></div><p>{results.data?.grading}</p>
      {data.games.some((game) => game.completed) && <p>{data.games.filter((game) => game.completed).map((game) => `${game.away_team} ${game.away_score} – ${game.home_team} ${game.home_score}`).join(" · ")}</p>}
      {data.card.picks.some((pick) => pick.result) && <p>Saved card: {data.card.picks.map((pick) => `${pick.team}: ${pick.result || "pending"}`).join(" · ")}</p>}
    </section>
    {data.pool.pool_type === "survivor" && <details className="panel"><summary>Season plan and entry diversification</summary>
      {strategy.error && <p role="alert">{strategy.error.message}</p>}
      <p>{strategy.data?.limitations}</p>
      {(strategy.data?.entries || []).map((entry) => <div key={entry.entry_id}><h3>{entry.name} · {entry.status}</h3>{Boolean(entry.missing_weeks?.length) && <p>Incomplete weeks: {entry.missing_weeks!.join(", ")}</p>}<div className="table-wrap"><table><thead><tr><th>Week</th><th>Team</th><th>Survival</th><th>Source</th><th>Card</th></tr></thead><tbody>{entry.picks.map((pick) => <tr key={`${pick.week}-${pick.team}`}><td>{pick.week}</td><td>{pick.team}</td><td>{pick.probability === null ? "Unavailable" : `${Math.round(pick.probability * 100)}%`}</td><td>{pick.kind}</td><td>{pick.saved ? "Saved" : "Suggestion"}</td></tr>)}</tbody></table></div></div>)}
    </details>}
  </>;
}
