import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { api, post } from "../../api";
import type { League, Provider } from "../../types";
import { PlayerDetailsButton, PlayerMentions } from "../leagues/PlayerDetails";
import { projectionPeriod, slotLabel, sourceTime } from "../leagues/league-display";
import { availability, reportForWeek, safeSourceUrl, sourceLabel } from "./forecast-display";
import type { AnalysisSummary, Forecast, SavedAnalysis, WeeklyReport } from "./types";
import "./league-analysis.css";

const ACTIVE = ["queued", "refreshing", "forecasting", "analyzing"];
const STATUS: Record<string, string> = { queued: "Queued", refreshing: "Refreshing league and NFL sources…", forecasting: "Calculating weekly forecasts…", analyzing: "Analyst is reviewing the decisions…", completed: "Complete", partial: "Complete with gaps", failed: "Failed" };
const points = (value: number | null | undefined) => value == null ? "—" : value.toFixed(1);
const signed = (value: number) => `${value > 0 ? "+" : ""}${value.toFixed(1)}`;
type WorkspaceProps = { league: League; selectedTeam: string; selectedWeek: number; onWeekChange: (week: number) => void };

export function LeagueAnalysisPanel(props: WorkspaceProps) {
  return <section className="weekly-analysis" aria-labelledby="weekly-analysis-title">
    <header className="league-section-heading"><h2 id="weekly-analysis-title">Weekly league analysis</h2></header>
    <LeagueWorkspace key={`${props.league.id}-${props.selectedTeam}`} {...props} />
  </section>;
}

function LeagueWorkspace({ league, selectedTeam, selectedWeek, onWeekChange }: WorkspaceProps) {
  const queryClient = useQueryClient();
  const [providerId, setProviderId] = useState<number>();
  const [selectedId, setSelectedId] = useState<number>();
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api<Provider[]>("/providers") });
  const historyKey = ["weekly-analyses", league.id, selectedTeam];
  const history = useQuery({
    queryKey: historyKey,
    queryFn: () => api<AnalysisSummary[]>(`/leagues/${league.id}/analyses?${new URLSearchParams({ team_name: selectedTeam })}`),
    enabled: Boolean(selectedTeam),
    refetchInterval: (query) => query.state.data?.some((run) => ACTIVE.includes(run.status)) ? 2000 : 15000,
  });
  const runs = history.data || [];
  const active = runs.find((run) => ACTIVE.includes(run.status));
  const selected = reportForWeek(runs, selectedWeek, selectedId);
  const detail = useQuery({ queryKey: ["weekly-analysis", league.id, selected?.id, selected?.status], queryFn: () => api<SavedAnalysis>(`/leagues/${league.id}/analyses/${selected!.id}`), enabled: Boolean(selected && (selected.has_report || !ACTIVE.includes(selected.status))), staleTime: 10000 });
  const start = useMutation({
    mutationFn: () => post<AnalysisSummary>(`/leagues/${league.id}/analyses`, { team_name: selectedTeam, week: selectedWeek, provider_id: providerId }),
    onSuccess: (run) => {
      setSelectedId(undefined);
      queryClient.setQueryData<AnalysisSummary[]>(historyKey, (previous) => [run, ...(previous || []).filter((item) => item.id !== run.id)]);
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
  const error = start.error || history.error || detail.error;
  const enabledProviders = providers.data?.filter((item) => item.enabled) || [];
  const saved = detail.data?.team_name === selectedTeam && detail.data.week === selectedWeek ? detail.data : undefined;
  const hasSavedReport = Boolean(selected?.has_report);
  return <>
    {saved?.report && <p><Link to={`/analysis?${new URLSearchParams({ league_id: String(league.id), league_report_id: String(saved.id), team_name: selectedTeam, week: String(selectedWeek) })}`}>Ask a follow-up about this report</Link></p>}
    {runs.length > 0 && <label className="weekly-history-label">Saved reports<select aria-label="Saved reports" value={selected?.id ?? ""} onChange={(event) => {
      const run = runs.find((item) => item.id === Number(event.target.value));
      if (run) { setSelectedId(run.id); onWeekChange(run.week); }
    }}>
      {!selected && <option value="">No saved report for Week {selectedWeek}</option>}
      {runs.map((run) => <option key={run.id} value={run.id}>Week {run.week} · {sourceTime(run.created_at)} · {STATUS[run.status]}</option>)}
    </select></label>}
    <details className="weekly-run-settings" key={`${selectedWeek}-${hasSavedReport}-${Boolean(active)}`} open={!history.isLoading && !hasSavedReport && !active}>
      <summary>{hasSavedReport ? "Run a fresh analysis" : "Analysis settings"}</summary>
      <p>Refresh league and NFL sources, then save a new report for <strong>{selectedTeam || "the selected team"} · Week {selectedWeek}</strong>. Saved reports remain available. This does not submit roster moves.</p>
      <form className="weekly-controls" onSubmit={(event) => { event.preventDefault(); start.mutate(); }}>
        <label>AI analyst<select aria-label="AI analyst" value={providerId ?? ""} onChange={(event) => setProviderId(event.target.value ? Number(event.target.value) : undefined)}><option value="">Configured default</option>{enabledProviders.map((provider) => <option key={provider.id} value={provider.id}>{provider.name} · {provider.model}</option>)}</select></label>
        <button className="button primary" type="submit" disabled={!selectedTeam || Boolean(active) || start.isPending}><Bot size={16} />{active || start.isPending ? "Analysis running…" : "Run league analysis"}</button>
      </form>
      {providers.isSuccess && !enabledProviders.length && <p className="weekly-notice">Statistical forecasts are available. <Link to="/settings">Configure an AI provider</Link> to include an analyst briefing.</p>}
      {providers.error && <p role="alert">Analyst settings could not load. <button className="button ghost" onClick={() => void providers.refetch()}>Retry analyst settings</button></p>}
    </details>
    {!selectedTeam && <p>Select a fantasy team above. If none are available, <Link to={`/leagues/${league.id}`}>import or review this league’s roster.</Link></p>}
    {error && <p className="weekly-notice error" role="alert">{error.message} <button className="button ghost" type="button" onClick={() => { void history.refetch(); if (selected) void detail.refetch(); start.reset(); }}>Retry loading</button></p>}
    {active && <p className="weekly-progress" role="status"><RefreshCw className="spin" size={16} />Week {active.week}: {STATUS[active.status]} You can leave this page; the report is saved when finished.</p>}
    {runs[0]?.status === "failed" && <p role="alert" className="weekly-notice error">Latest run failed (Week {runs[0].week}). {runs[0].error} {saved?.report && "Showing a previous saved report."}</p>}
    {(history.isLoading || (detail.isFetching && !saved)) && <p role="status">Loading saved report…</p>}
    {saved?.report ? <Report key={saved.id} saved={saved} /> : !active && !history.isLoading && !detail.isFetching && !error && selectedTeam ? <p className="weekly-empty">No saved report for {selectedTeam} · Week {selectedWeek}. Run an analysis or choose another week from Saved reports.</p> : null}
  </>;
}

export function Report({ saved }: { saved: SavedAnalysis }) {
  const report = saved.report!;
  const lineup = report.lineup;
  const output = report.analysis?.output;
  const mentionPlayers = report.forecasts.map((player) => ({ id: player.player_id, name: player.name, league_id: saved.league_id }));
  const mentions = (text: string) => <PlayerMentions text={text} players={mentionPlayers} leagueId={saved.league_id} />;
  const moves = lineup.waivers.map((move) => {
    const player = report.forecasts.find((item) => item.player_id === move.add_id);
    return { move, player, evidence: availability(player, move.conditional) };
  });
  const supported = moves.filter((item) => !item.evidence.needsVerification);
  const conditional = moves.filter((item) => item.evidence.needsVerification);
  const needsReview = Boolean(saved.stale_reasons.length || lineup.partial_total || conditional.length || lineup.assignments.some((item) => item.conditional));
  return <div className="weekly-report">
    <div className="weekly-report-heading"><h3>{report.team_name} · Week {report.week}</h3><span>{STATUS[saved.status]} · {sourceTime(report.generated_at)}</span></div>
    <section className="weekly-decision-brief" aria-labelledby="weekly-decision-heading">
      <h4 id="weekly-decision-heading">{lineup.error ? "Resolve roster gaps before choosing a lineup" : lineup.gain == null ? "Not enough evidence for a lineup decision" : lineup.gain > 0 ? `${signed(lineup.gain)} pts of modeled lineup improvement` : "No supported lineup upgrade found"}</h4>
      <p>{needsReview ? "Review availability and coverage before acting. " : "Review the estimates before acting. "}{supported.length ? `${supported.length} modeled waiver alternative${supported.length === 1 ? "" : "s"}. ` : ""}{conditional.length ? `${conditional.length} waiver alternative${conditional.length === 1 ? " needs" : "s need"} verification. ` : ""}Roster changes remain your decision.</p>
    </section>
    <nav className="league-section-nav weekly-section-nav" aria-label="Forecast sections"><a href="#weekly-lineup">Lineup</a><a href="#weekly-waivers">Waivers</a><a href="#weekly-comparison">Compare players</a><a href="#weekly-briefing">Analyst briefing</a><a href="#weekly-method">Sources & method</a></nav>
    {saved.stale_reasons.length > 0 && <div className="weekly-notice" role="status"><strong>Recheck before acting.</strong> {saved.stale_reasons.join(" ")}</div>}
    {report.coverage.modeled === 0 && <p className="weekly-notice">No independent forecasts could be calculated. Review data coverage below; missing forecasts are not zero.</p>}
    <div className="weekly-stat-tape">
      <div><small>Modeled players · including available</small><strong>{report.coverage.modeled} / {report.coverage.players}</strong></div>
      <div><small>{lineup.partial_total ? "Lineup gain · modeled portion" : "Lineup improvement"}</small><strong>{lineup.gain == null ? "—" : `${signed(lineup.gain)} pts`}</strong></div>
      <div><small>{lineup.partial_total ? "Modeled portion of lineup" : "Projected lineup"}</small><strong>{lineup.recommended_points == null ? "—" : `${points(lineup.recommended_points)} pts`}</strong></div>
    </div>
    <p className="weekly-caption">Experimental weekly baseline · historical ranges</p>
    <div className="weekly-directions">
      <section id="weekly-lineup" aria-labelledby="weekly-lineup-heading"><h4 id="weekly-lineup-heading">Lineup direction</h4>
        {lineup.error ? <p className="weekly-notice">{lineup.error}</p> : <>
          {lineup.partial_total && <p className="weekly-notice">Some slots have no forecast. Totals cover modeled players only; unmodeled starters stay in place.</p>}
          {lineup.assignments.map((item, index) => {
            const player = report.forecasts.find((forecast) => forecast.player_id === item.player_id);
            const evidence = availability(player, item.conditional);
            return <div className="weekly-lineup-row" key={`${index}-${item.player_id}`}><span>{slotLabel(item.slot)}</span><div><PlayerDetailsButton player={{ id: item.player_id, name: item.name }} /><p className="weekly-instruction">{item.action}{item.conditional ? ` · Conditional on playing (${evidence.status})` : ""}{item.reason ? ` · ${item.reason}` : ""}</p></div><span>{points(item.points)}</span></div>;
          })}
          {!!lineup.bench?.length && <p>Move to bench: {lineup.bench.map((name, index) => <span key={`${index}-${name}`}>{index > 0 && ", "}<PlayerDetailsButton player={{ id: report.forecasts.find((player) => player.name === name)?.player_id, name }} /></span>)}.</p>}
          {!!lineup.unfilled_slots?.length && <p className="weekly-notice">Unfilled: {lineup.unfilled_slots.map(slotLabel).join(", ")}</p>}
        </>}
      </section>
      <section id="weekly-waivers" aria-labelledby="weekly-waiver-heading"><h4 id="weekly-waiver-heading">Waiver alternatives</h4>
        {moves.length ? <><p>Each is a separate alternative to the optimized lineup, not a combined set of moves. Status and estimates are from this saved report. Verify availability and long-term value before dropping anyone.</p>
          {[{ label: "Modeled alternatives", items: supported }, { label: "Verify before considering", items: conditional }].filter((group) => group.items.length).map((group) => <div className="weekly-waiver-group" key={group.label}><h5>{group.label}</h5>{group.items.map(({ move, player, evidence }) => <article className="weekly-waiver-row" key={move.add_id} aria-label={`Evaluate ${move.add}`}>
            <strong>Evaluate <PlayerDetailsButton player={{ id: move.add_id, name: move.add }} /></strong><span>{signed(move.gain)} pts<small>{evidence.needsVerification ? "Conditional estimate" : "Modeled gain"}</small></span>
            <p className={evidence.needsVerification ? "weekly-availability" : ""}><strong>{evidence.status}</strong> · {evidence.confidence}{player ? ` · ${player.sample_games} games` : ""}</p>
            <p>Drop candidate: {move.drop_id ? <PlayerDetailsButton player={{ id: move.drop_id, name: move.drop }} /> : move.drop}</p><p>{mentions(move.reason)}</p>
            {!player && <p className="weekly-availability">Availability evidence is missing from this saved report. Verify status before considering this move.</p>}
            {player?.reason && <p>{mentions(player.reason)}</p>}
            {!!player?.warnings.length && <ul className="weekly-waiver-warnings">{player.warnings.map((warning) => <li key={warning}>{mentions(warning)}</li>)}</ul>}
            {player && evidence.needsVerification && !player.warnings.length && <p className="weekly-availability">Confirm playing status and role before using this estimate.</p>}
          </article>)}</div>)}</> : <p>{lineup.error ? "Resolve the roster issue to evaluate add/drop moves." : "No supported one-week add/drop upgrade found. Players without forecasts were not evaluated."}</p>}
      </section>
    </div>
    <ForecastTable report={report} />
    {report.league_comparison && <details className="weekly-method"><summary>League lineup comparison</summary><p>{report.league_comparison.note}</p><div className="table-wrap"><table><thead><tr><th>Team</th><th>Rank</th><th>Projected lineup</th><th>Coverage</th></tr></thead><tbody>{report.league_comparison.teams.map((team) => <tr key={team.team}><td>{team.team}{team.reason && <small>{team.reason}</small>}</td><td>{team.rank ?? "Unranked"}</td><td>{points(team.optimized_points)}{!team.complete ? " · partial" : ""}</td><td>{team.projected_starters} / {team.starting_slots}{team.conditional_players.length ? ` · verify ${team.conditional_players.join(", ")}` : ""}</td></tr>)}</tbody></table></div></details>}
    <details className="weekly-briefing" id="weekly-briefing"><summary>Analyst briefing · recommendations and risks</summary>{output ? <><p>{mentions(output.summary)}</p>{!!output.recommendations.length && <ul>{output.recommendations.map((value, index) => <li key={index}>{mentions(value)}</li>)}</ul>}{!!output.risks.length && <p><strong>Watch:</strong> {mentions(output.risks.join(" "))}</p>}{!!output.missing_information.length && <p><strong>Missing evidence:</strong> {mentions(output.missing_information.join(" "))}</p>}<div className="weekly-links">{output.citations.filter(safeSourceUrl).map((url, index) => <a key={index} href={url} target="_blank" rel="noreferrer">{sourceLabel(url, report.sources)}<span className="sr-only"> (opens in a new tab)</span></a>)}</div><small>{report.analysis?.provider} · {report.analysis?.model}</small></> : <p className="weekly-notice">{report.analysis?.error || "The analyst briefing is still running. Forecasts are saved."}</p>}</details>
    <details className="weekly-changes"><summary>Since the previous run</summary><ul>{report.changes.map((change, index) => <li key={index}>{mentions(change)}</li>)}</ul></details>
    <details className="weekly-method" id="weekly-method"><summary>Sources, coverage, and forecast method</summary><p>{report.method}</p><small>Model version: {report.model_version}</small><ul>{report.limitations.map((item) => <li key={item}>{item}</li>)}</ul><h4>League coverage</h4><div className="weekly-coverage">{report.league_coverage.map((team) => <p key={team.team}><strong>{team.team}</strong><span>{team.modeled} / {team.players} modeled</span></p>)}</div><h4>Sources used for this run</h4>{report.sources.map((source, index) => <div className="weekly-source" key={index}><strong>{source.url && safeSourceUrl(source.url) ? <a href={source.url} target="_blank" rel="noreferrer">{source.name}<span className="sr-only"> (opens in a new tab)</span></a> : source.name}</strong><span>{source.status} · {sourceTime(source.received_at)}</span>{source.detail && <p>{source.detail}</p>}</div>)}<h4>Forecast tracking</h4><p>{report.evaluation.scored_forecasts ? `${report.evaluation.scored_forecasts} forecasts scored · mean absolute error ${points(report.evaluation.mae)} points.` : "No completed-game outcomes scored yet. Saved forecasts will be checked on subsequent runs."}</p>{report.evaluation.comparison_count > 0 && <p>Matching source comparisons: {report.evaluation.comparison_count} · Open Gridiron error {points(report.evaluation.paired_model_mae)} · imported source error {points(report.evaluation.source_mae)} points.</p>}<p>{report.evaluation.note}</p></details>
  </div>;
}

function ForecastTable({ report }: { report: WeeklyReport }) {
  const [scope, setScope] = useState("roster");
  const [search, setSearch] = useState("");
  const [limit, setLimit] = useState(20);
  const rows = report.forecasts.filter((p) => (scope === "all" || (scope === "roster" ? p.rostered_by === report.team_name : !p.rostered_by)) && `${p.name} ${p.team} ${p.position}`.toLowerCase().includes(search.toLowerCase())).sort((a, b) => (b.points ?? -Infinity) - (a.points ?? -Infinity));
  return <section className="weekly-forecasts" id="weekly-comparison" aria-labelledby="weekly-comparison-heading"><h4 id="weekly-comparison-heading">Weekly forecast comparison</h4><div className="weekly-forecast-controls"><label>Players<select aria-label="Players" value={scope} onChange={(event) => { setScope(event.target.value); setLimit(20); }}><option value="roster">Selected roster</option><option value="available">Available players</option><option value="all">All league players</option></select></label><label>Search players<input aria-label="Search players" type="search" value={search} onChange={(event) => { setSearch(event.target.value); setLimit(20); }} placeholder="Name, position, or NFL team" /></label></div>
    <p>Source totals retain their original period. A difference is shown only for matching weekly forecasts and scoring.</p>
    <div className="weekly-table-wrap"><table role="table"><caption className="sr-only">Independent weekly forecasts and imported source projections</caption><thead role="rowgroup"><tr role="row"><th scope="col" role="columnheader">Player</th><th scope="col" role="columnheader">Open Gridiron · Week {report.week}</th><th scope="col" role="columnheader">Imported source & comparison</th><th scope="col" role="columnheader">Evidence</th></tr></thead><tbody role="rowgroup">{rows.slice(0, limit).map((p) => <ForecastRow key={p.player_id} player={p} />)}</tbody></table></div>
    {!rows.length && <p role="status">No players match these filters.</p>}
    {rows.length > limit && <button className="button ghost" onClick={() => setLimit(limit + 30)}>Show more players ({limit} of {rows.length})</button>}
  </section>;
}

function ForecastRow({ player: p }: { player: Forecast }) {
  const evidence = availability(p);
  const comparable = p.source_projection.comparable && p.difference != null;
  return <tr role="row">
    <td className="weekly-player" role="cell"><PlayerDetailsButton player={{ id: p.player_id, name: p.name, position: p.position, pro_team: p.team }} /><small>{p.position} · {p.team}{p.opponent ? ` vs ${p.opponent}` : ""}{p.locked ? " · locked" : ""}</small></td>
    <td className="weekly-estimate" role="cell"><span className="weekly-mobile-label" aria-hidden="true">Weekly estimate</span><strong>{points(p.points)}{p.points != null ? " pts" : ""}</strong><small>{p.points == null ? "Unavailable" : `${points(p.floor)}–${points(p.ceiling)} historical range`}</small></td>
    <td className="weekly-comparability" role="cell"><strong>{comparable ? `${signed(p.difference!)} pts difference` : "Not comparable"}</strong><small>{comparable ? "Matching weekly period and scoring" : `${projectionPeriod(p.source_projection)} · different or unverified period / scoring`}</small><details><summary>Imported source projection</summary><p>{points(p.source_projection.points)} pts · {projectionPeriod(p.source_projection)}<br />{p.source_projection.source || "Source unknown"}</p></details></td>
    <td className="weekly-evidence" role="cell"><p className="weekly-instruction"><strong>{evidence.confidence}</strong>{p.conditional ? " · Conditional on playing" : ""}</p><small>{evidence.status} · {p.sample_games} games</small><details><summary>{p.confidence === "unavailable" ? "Missing evidence" : "Evidence & warnings"}</summary>{p.reason && <p><PlayerMentions text={p.reason} /></p>}{p.recent_usage != null && <p>Recent workload: {p.recent_usage} attempts / carries / targets per game; baseline {p.baseline_usage}.</p>}{p.warnings.map((warning) => <p key={warning}><PlayerMentions text={warning} /></p>)}</details></td>
  </tr>;
}
