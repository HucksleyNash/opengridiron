import { useState } from "react";
import type { Player } from "../../types";
import { PlayerDetailsButton } from "./PlayerDetails";
import { RosterGameTime } from "./RosterGameTime";
import type { Forecast } from "../league-analysis/types";
import { LINEUP_MODES, normalizePosition, points, rosterGroup, slotLabel, weeklyPoints, type LineupMode, type WeeklyLineup } from "./league-display";

export function RosterTable({ roster, slots, weekly, gameForecasts, mode, loading }: {
  roster: Player[]; slots: string[]; weekly?: WeeklyLineup; gameForecasts?: Forecast[]; mode: LineupMode; loading: boolean;
}) {
  const [changesOnly, setChangesOnly] = useState(false);
  const assignments = new Map(weekly?.assignments.map((assignment) => [assignment.player_id, assignment]));
  const forecasts = new Map(weekly?.forecasts.map((forecast) => [forecast.player_id, forecast]));
  const games = new Map((gameForecasts ?? weekly?.forecasts)?.map((forecast) => [forecast.player_id, forecast]));
  const rows = roster.map((player) => {
    const group = rosterGroup(player, slots);
    const assignment = assignments.get(player.id);
    const forecast = forecasts.get(player.id);
    const action = !weekly ? "" : assignment && group !== "Starters" ? "Start" : !assignment && group === "Starters" ? "Bench" : "";
    const current = slotLabel(player.current_slot);
    const recommended = !weekly ? null : assignment ? slotLabel(assignment.slot) : action === "Bench" ? "Bench" : current;
    const moved = recommended !== null && current !== recommended;
    return { player, group, assignment, forecast, action, current, recommended, moved };
  });
  const changes = rows.filter((row) => row.moved);
  const visible = changesOnly && weekly ? changes : rows;
  return <section className="league-section league-unified-roster" id="current-roster" aria-labelledby="current-roster-heading">
    <div className="league-section-heading"><h2 id="current-roster-heading">Team roster <span className="league-count">{roster.length}</span></h2>
      <button type="button" className="ghost" aria-pressed={changesOnly && Boolean(weekly)} disabled={!weekly} onClick={() => setChangesOnly(!changesOnly)}>{changesOnly && weekly ? "Show full roster" : "Changes only"}</button>
    </div>
    {!roster.length ? <p>No roster imported for this team. Open League tools to sync or import players.</p> : !visible.length ? <p role="status">No slot changes recommended. Your current lineup is unchanged.</p> : <table className="league-unified-table" role="table">
      <caption className="sr-only">Current and recommended slots for each player. Open Gridiron {weekly ? `Week ${weekly.week}` : "weekly"} {LINEUP_MODES[mode].label.toLowerCase()} estimates. Source projections are separate.</caption>
      <thead role="rowgroup"><tr role="row"><th role="columnheader" scope="col">Player</th><th role="columnheader" scope="col">Current → Recommended</th><th role="columnheader" scope="col" className="numeric">OG · {weekly ? `Wk ${weekly.week}` : "Weekly"}<br />{LINEUP_MODES[mode].label} pts</th><th role="columnheader" scope="col">Attention</th></tr></thead>
      <tbody role="rowgroup">{visible.map(({ player, group, assignment, forecast, action, current, recommended, moved }, index) => <tr role="row" key={player.id} className={moved ? "league-roster-change" : undefined}>
        <td role="cell" className="league-roster-identity">
          {(index === 0 || visible[index - 1].group !== group) && <span className="league-roster-group">{group}</span>}
          <PlayerDetailsButton player={player} description={`${normalizePosition(player.position)} · ${player.pro_team || "NFL team unknown"}`}><strong>{player.name}</strong><span className="league-player-meta">{normalizePosition(player.position)} · {player.pro_team || "NFL team unknown"}</span></PlayerDetailsButton>
          <RosterGameTime forecast={games.get(player.id)} loading={loading} />
        </td>
        <td role="cell" className="league-roster-slots"><span className="league-mobile-label">Current → Recommended</span>{current}{moved ? <> → <strong>{recommended}</strong></> : <span className="league-slot-note">{loading ? "Calculating…" : weekly ? "Keep" : "Recommendation unavailable"}</span>}</td>
        <td role="cell" className="numeric league-roster-points"><span className="league-mobile-label">{weekly ? `Wk ${weekly.week}` : "Weekly"} · {LINEUP_MODES[mode].label}</span>{loading ? "Loading…" : points(weeklyPoints(forecast, mode))}</td>
        <td role="cell" className="league-roster-attention">
          {action && <strong className="league-positive">{action}</strong>}
          {forecast?.locked && <span>Game locked</span>}
          {(!player.status || player.status.toLowerCase() !== "active") && <span className="league-injury">{player.status || "Status unknown"}{forecast?.conditional ? " · conditional on playing" : ""}</span>}
          {assignment?.reason && <span>{assignment.reason}</span>}
          {!assignment?.reason && forecast?.reason && <span>{forecast.reason}</span>}
          {!!forecast?.warnings?.length && <details><summary>Evidence & warnings</summary>{forecast.warnings.map((warning, warningIndex) => <p key={warningIndex}>{warning}</p>)}</details>}
          {!action && !forecast?.locked && player.status?.toLowerCase() === "active" && !assignment?.reason && !forecast?.reason && !forecast?.warnings?.length && <span className="league-no-attention" aria-label="No additional flags">—</span>}
        </td>
      </tr>)}</tbody>
    </table>}
  </section>;
}
