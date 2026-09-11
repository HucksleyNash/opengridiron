import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import type { Player } from "../../types";
import { PlayerDetailsButton } from "./PlayerDetails";
import { RosterGameTime } from "./RosterGameTime";
import type { Forecast } from "../league-analysis/types";
import { normalizePosition, points, projectionPeriod, sharedProjectionContext, slotLabel, sourceTime } from "./league-display";

export function SourceProjectionTable({ players, leaders = false, gameForecasts, scheduleLoading = false }: { players: Player[]; leaders?: boolean; gameForecasts?: Forecast[]; scheduleLoading?: boolean }) {
  const shared = sharedProjectionContext(players);
  const games = new Map(gameForecasts?.map((forecast) => [forecast.player_id, forecast]));
  return <>
    <p className="league-source-context">{shared.source || "Multiple sources"} · {shared.period || "Mixed projection periods"}<br />
      <span>{shared.updated === null ? "Source update times vary by player" : shared.updated ? `Source updated ${sourceTime(shared.updated)}` : "Source update time not supplied"}. Source values retain their imported scoring; select a player for full details.</span>
    </p>
    <table className="league-source-table" role="table">
      <caption className="sr-only">{leaders ? "League leaders" : "Selected roster"}: imported source projections, separate from Open Gridiron weekly estimates. Range means source floor to ceiling.</caption>
      <thead role="rowgroup"><tr role="row"><th role="columnheader" scope="col">Player</th><th role="columnheader" scope="col" className="numeric">Source pts</th><th role="columnheader" scope="col" className="numeric">Source range</th><th role="columnheader" scope="col">Context</th></tr></thead>
      <tbody role="rowgroup">{players.map((player) => <tr key={player.id} role="row">
        <td role="cell"><PlayerDetailsButton player={player} initialTab="projections" label={`View ${player.name} projections`} description={`${normalizePosition(player.position)} · ${player.pro_team || "NFL team unknown"} · ${leaders ? player.rostered_by || "Available" : slotLabel(player.current_slot)}`}><strong>{player.name}</strong><span className="league-player-meta">{normalizePosition(player.position)} · {player.pro_team || "NFL team unknown"} · {leaders ? player.rostered_by || "Available" : slotLabel(player.current_slot)}</span></PlayerDetailsButton>
          {!leaders && <RosterGameTime forecast={games.get(player.id)} loading={scheduleLoading} />}
        </td>
        <td role="cell" className="numeric league-source-points"><span className="league-mobile-label">Source pts</span>{points(player.projected_points)}</td>
        <td role="cell" className="numeric league-source-range"><span className="league-mobile-label">Source floor → ceiling</span>{points(player.floor)} → {points(player.ceiling)}</td>
        <td role="cell" className="league-source-exceptions">
          {(!player.status || player.status.toLowerCase() !== "active") && <span className="league-injury">{player.status || "Status unknown"}</span>}
          {!shared.source && <span>{player.projection?.source || "Source not recorded"}</span>}
          {!shared.period && <span>{projectionPeriod(player.projection)}</span>}
          {shared.updated === null && <span>Updated {sourceTime(player.projection?.source_updated_at)}</span>}
        </td>
      </tr>)}</tbody>
    </table>
  </>;
}

export function LeagueProjectionLeaders({ leagueId, active }: { leagueId: number; active: boolean }) {
  const [open, setOpen] = useState(false);
  const leaders = useQuery({ queryKey: ["projection-leaders", leagueId], queryFn: ({ signal }) => api<Player[]>(`/leagues/${leagueId}/projection-leaders`, { signal }), enabled: active && open });
  return <details className="league-disclosure league-section" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>League projection leaders · all teams</summary>
    <p className="league-help">Top 10 stored source totals, including rostered and available players. This ranking does not establish weekly lineup improvement. Compare matching periods and scoring.</p>
    {open && (leaders.isLoading ? <p role="status">Loading projection leaders…</p> : leaders.error ? <div role="alert"><p>Could not load projection leaders.</p><button type="button" onClick={() => void leaders.refetch()}>Try again</button></div> : leaders.data?.length ? <SourceProjectionTable players={leaders.data} leaders /> : <p>No projections imported.</p>)}
  </details>;
}
