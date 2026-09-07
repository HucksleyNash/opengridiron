import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import type { Player } from "../../types";
import { DetailTabs } from "./DetailTabs";
import { PlayerDetailsButton } from "./PlayerDetails";
import { normalizePosition, points, projectionPeriod, projectionSummary, sourceTime } from "./league-display";

const VIEWS = [{ id: "roster", label: "Selected roster" }, { id: "leaders", label: "League leaders" }] as const;

function ProjectionTable({ players, leaders = false }: { players: Player[]; leaders?: boolean }) {
  return <table className="league-projections-table" role="table">
    <caption className="sr-only">{leaders ? "Top ten league-wide stored projections" : "Selected roster projections and sources"}. Select a player for full projection details.</caption>
    <thead role="rowgroup"><tr role="row">
      <th role="columnheader" scope="col">Player / {leaders ? "fantasy team" : "status"}</th>
      <th role="columnheader" scope="col" className="numeric">Projected pts / period</th>
      <th role="columnheader" scope="col" className="numeric">Floor</th>
      <th role="columnheader" scope="col" className="numeric">Ceiling</th>
      <th role="columnheader" scope="col">Source / updated</th>
    </tr></thead>
    <tbody role="rowgroup">{players.map((player) => <tr key={player.id} role="row">
      <td role="cell"><span className="league-player">
        <PlayerDetailsButton player={player} initialTab="projections" label={`View ${player.name} projections`} />
        <span className="league-player-meta">{normalizePosition(player.position)} · {player.pro_team || "NFL team unknown"}<span className={player.status?.toLowerCase() !== "active" ? "league-injury" : ""}>{player.status || "Status unknown"}</span></span>
        {leaders && <span className="league-availability">{player.rostered_by || "Available"}</span>}
      </span></td>
      <td role="cell" className="numeric league-projection-value"><span className="league-mobile-label">Projected pts</span><strong>{points(player.projected_points)}</strong><span className="league-projection-period">{projectionPeriod(player.projection)}</span></td>
      <td role="cell" className="numeric"><span className="league-mobile-label">Floor</span>{points(player.floor)}</td>
      <td role="cell" className="numeric"><span className="league-mobile-label">Ceiling</span>{points(player.ceiling)}</td>
      <td role="cell" className="league-projection-source"><span className="league-mobile-label">Source / updated</span><span>{player.projection?.source || "Source not recorded"}</span><time className="league-projection-period" dateTime={player.projection?.source_updated_at || undefined}>{player.projection?.source_updated_at ? sourceTime(player.projection.source_updated_at) : "Update time not supplied"}</time></td>
    </tr>)}</tbody>
  </table>;
}

export function PlayerProjections({ leagueId, roster, team }: { leagueId: number; roster: Player[]; team: string }) {
  const [view, setView] = useState<"roster" | "leaders">("roster");
  const leaders = useQuery({ queryKey: ["projection-leaders", leagueId], queryFn: ({ signal }) => api<Player[]>(`/leagues/${leagueId}/projection-leaders`, { signal }), enabled: view === "leaders" });
  return <section className="league-section" id="player-projections" aria-labelledby="player-projections-heading">
    <div className="league-section-heading"><div><h2 id="player-projections-heading">Player projections</h2><p className="league-caption">Compare estimates and sources. Select a player for scoring, timestamps, and full details.</p></div></div>
    <DetailTabs id="projection-view" label="Projection scope" tabs={VIEWS} selected={view} onSelect={setView} />
    <div role="tabpanel" id="projection-view-panel-roster" aria-labelledby="projection-view-tab-roster" hidden={view !== "roster"} tabIndex={0}>
      <div className="league-projection-context"><strong>{team || "Selected roster"}</strong><span>{roster.length} {roster.length === 1 ? "player" : "players"}</span></div>
      {roster.length ? <><p className="league-help">{projectionSummary(roster)}</p><ProjectionTable players={roster} /></> : <p>No roster projections to compare. <a className="league-text-link" href="#league-data-tools">Import player projections</a> to get started.</p>}
    </div>
    <div role="tabpanel" id="projection-view-panel-leaders" aria-labelledby="projection-view-tab-leaders" hidden={view !== "leaders"} tabIndex={0}>
      <div className="league-projection-context"><strong>League projection leaders</strong><span>Top 10 · all teams</span></div>
      <p className="league-help">Highest stored projected points across all players in this league, including other teams and available players. Independent of the selected fantasy team and lineup objective. Check each player’s period before comparing values.</p>
      {leaders.isLoading ? <p role="status" className="league-loading">Loading projection leaders…</p> : leaders.error ? <div role="alert" className="league-error"><strong>Could not load projection leaders</strong><p>{leaders.error.message}</p><button type="button" onClick={() => void leaders.refetch()}>Try again</button></div> : leaders.data?.length ? <ProjectionTable players={leaders.data} leaders /> : <p>No projections imported.</p>}
    </div>
  </section>;
}
