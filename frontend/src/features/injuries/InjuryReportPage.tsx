import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowUpRight, RefreshCw, Search, X } from "lucide-react";
import { api, post } from "../../api";
import type { Provider } from "../../types";
import type { Evidence, Finding, InjuryBoard, InjuryCheck, InjuryDetail, InjuryRow, Source, TargetGame } from "./types";
import "./injury-report.css";

function time(value?: string | null): string {
  if (!value) return "Time not supplied";
  const date = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? "Time not supplied" : date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function SourceLink({ url, children }: { url: string; children: ReactNode }) {
  try {
    if (!["https:", "http:"].includes(new URL(url).protocol)) return <span>{children}</span>;
  } catch { return <span>{children}</span>; }
  return <a href={url} target="_blank" rel="noopener noreferrer">{children}<ArrowUpRight size={13} aria-hidden="true" /></a>;
}

function GameLabel({ game }: { game: TargetGame | null }) {
  return game ? <span>Week {game.week} · {game.away_team} @ {game.home_team} · {time(game.kickoff)}</span> : <span>Next game unavailable in saved schedule</span>;
}

function SourceStatus({ sources }: { sources: Source[] }) {
  return <ul className="injury-source-status" aria-label="Source freshness">{sources.map((source) => <li key={source.name}>
    <span className={`injury-source-dot ${source.status}`} aria-hidden="true" />
    <SourceLink url={source.url}>{source.name}</SourceLink>
    <span>{source.status === "ok" ? "Retrieved" : source.status === "stale" ? "Stale · retrieved" : "Unavailable"}{source.fetched_at ? ` ${time(source.fetched_at)}` : ""}</span>
    {source.status !== "ok" && source.message && <p>{source.message}</p>}
  </li>)}</ul>;
}

const OUTLOOKS: Record<string, string> = { likely_to_play: "Likely to play", game_time_decision: "Game-time decision", unlikely_to_play: "Unlikely to play", ruled_out: "Ruled out", unknown: "Availability unknown" };

function CitedFinding({ title, finding, evidence }: { title: string; finding: Finding; evidence: Evidence[] }) {
  const citations = finding.evidence_ids.map((id) => evidence.find((item) => item.id === id)).filter((item): item is Evidence => Boolean(item));
  return <section className="injury-finding"><h4>{title}</h4><p>{finding.text}</p>
    {citations.length > 0 && <ul>{citations.map((source) => <li key={source.id}><SourceLink url={source.url}>{source.title}</SourceLink><small>{source.published_at ? `Published ${time(source.published_at)}` : `Retrieved ${time(source.retrieved_at)}`}</small></li>)}</ul>}
  </section>;
}

function CheckResult({ check }: { check: InjuryCheck }) {
  const output = check.output;
  return <div className="injury-check-result">
    <p className="injury-meta">{time(check.created_at)} · {check.model}</p>
    <p className="injury-meta"><GameLabel game={check.target_game} /></p>
    {check.stale && <p className="injury-warning" role="status">Saved assessment may be out of date. Run a new check before making a lineup decision.</p>}
    {["queued", "running"].includes(check.status) && <p role="status">Checking injury reports and recent coverage… You can leave this view; the result will be saved.</p>}
    {check.status === "failed" && <p role="alert">{check.error || "The injury check failed. Run a new check to retry."}</p>}
    {output && <>
      <div className="injury-outlook"><strong>{OUTLOOKS[output.outlook] || "Availability unknown"}</strong><span>{output.confidence} confidence · AI assessment</span></div>
      <p>{output.summary}</p>
      <CitedFinding title="Chance to play" finding={output.availability} evidence={check.evidence} />
      <CitedFinding title="Expected workload" finding={output.workload} evidence={check.evidence} />
      <CitedFinding title="Fantasy advice" finding={output.fantasy_advice} evidence={check.evidence} />
      <section className="injury-finding"><h4>Next update to watch</h4><p>{output.next_update}</p></section>
      {!!output.missing_information.length && <details className="injury-caveats" open={output.outlook === "unknown"}><summary>Evidence gaps & limitations ({output.missing_information.length})</summary><ul>{output.missing_information.map((item, index) => <li key={index}>{item}</li>)}</ul></details>}
    </>}
  </div>;
}

function PlayerCheck({ playerKey }: { playerKey: string }) {
  const client = useQueryClient();
  const [providerId, setProviderId] = useState("");
  const [selectedCheck, setSelectedCheck] = useState<number | null>(null);
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api<Provider[]>("/providers") });
  const enabled = (providers.data || []).filter((provider) => provider.enabled);
  const chosen = enabled.find((provider) => String(provider.id) === providerId) || enabled.find((provider) => provider.task_defaults.includes("recommendation")) || enabled[0];
  const history = useQuery({
    queryKey: ["injury-checks", playerKey], queryFn: () => api<InjuryCheck[]>(`/injuries/${playerKey}/checks`),
    refetchInterval: (query) => query.state.data?.some((run) => ["queued", "running"].includes(run.status)) ? 2000 : false,
  });
  const start = useMutation({
    mutationFn: () => post<InjuryCheck>(`/injuries/${playerKey}/checks`, { provider_id: chosen?.id }),
    onSuccess: (run) => {
      setSelectedCheck(run.id);
      client.setQueryData<InjuryCheck[]>(["injury-checks", playerKey], (previous) => [run, ...(previous || []).filter((item) => item.id !== run.id)]);
      void client.invalidateQueries({ queryKey: ["injury-checks", playerKey] });
    },
  });
  const active = history.data?.some((run) => ["queued", "running"].includes(run.status));
  const result = history.data?.find((run) => run.id === selectedCheck) || history.data?.[0];
  return <section className="injury-ai" aria-labelledby="injury-ai-title">
    <h3 id="injury-ai-title">AI availability check</h3>
    <p>Pull current reports and recent coverage for a source-backed outlook on playing, workload, and your lineup.</p>
    <div className="injury-ai-actions">
      <label>Analysis provider<select value={chosen?.id || ""} onChange={(event) => setProviderId(event.target.value)} disabled={!enabled.length || Boolean(active)}><option value="" disabled>Select provider</option>{enabled.map((provider) => <option key={provider.id} value={provider.id}>{provider.name} · {provider.model}</option>)}</select></label>
      <button type="button" className="primary" onClick={() => start.mutate()} disabled={!chosen || start.isPending || Boolean(active) || history.isPending || Boolean(history.error)}>{start.isPending || active ? "Checking…" : "Run AI check"}</button>
    </div>
    {!providers.isPending && !providers.error && !enabled.length && <p><Link to="/settings">Configure an AI provider</Link> to run a check. Reports and sources are available below.</p>}
    {providers.error && <p role="alert">Could not load providers. <button type="button" className="ghost" onClick={() => void providers.refetch()}>Retry providers</button></p>}
    {start.error && <p role="alert">{start.error.message}</p>}
    {history.error && <p role="alert">Could not load saved checks. <button type="button" className="ghost" onClick={() => void history.refetch()}>Retry saved checks</button></p>}
    {(history.data?.length || 0) > 1 && <label className="injury-history">Saved checks<select value={result?.id || ""} onChange={(event) => setSelectedCheck(Number(event.target.value))}>{history.data!.map((run) => <option key={run.id} value={run.id}>{time(run.created_at)} · {run.status}</option>)}</select></label>}
    {result && <CheckResult check={result} />}
  </section>;
}

function InjuryPlayer({ row, onClose }: { row: InjuryRow; onClose: () => void }) {
  const client = useQueryClient();
  const heading = useRef<HTMLHeadingElement>(null);
  const detail = useQuery({ queryKey: ["injury-detail", row.key], queryFn: () => api<InjuryDetail>(`/injuries/${row.key}`), staleTime: 60_000, refetchOnMount: "always" });
  const refresh = useMutation({ mutationFn: () => api<InjuryDetail>(`/injuries/${row.key}?refresh=true`), onSuccess: (data) => { client.setQueryData(["injury-detail", row.key], data); void client.invalidateQueries({ queryKey: ["injuries"] }); } });
  useEffect(() => { heading.current?.focus({ preventScroll: true }); if (window.matchMedia("(max-width: 1100px)").matches) heading.current?.scrollIntoView({ block: "start" }); }, []);
  const data = detail.data;
  const player = data || row;
  return <section className="injury-player" aria-label={`${row.name} injury details`}>
    <header><div><h2 ref={heading} tabIndex={-1}>{row.name}</h2><p className="injury-meta">{row.position} · {row.team}</p></div><button type="button" className="ghost" aria-label="Close injury details" onClick={onClose}><X size={19} /></button></header>
    <p className="injury-meta"><GameLabel game={player.next_game} /></p>
    <PlayerCheck playerKey={row.key} />
    <section className="injury-reports"><div className="injury-section-heading"><h3>Reported details</h3><button className="ghost" type="button" disabled={detail.isFetching || refresh.isPending} onClick={() => refresh.mutate()}><RefreshCw size={14} />{refresh.isPending ? "Refreshing…" : "Refresh sources"}</button></div>
      {detail.isPending && <p role="status">Loading injury reports and recent articles…</p>}
      {(detail.error || refresh.error) && <p role="alert">{(detail.error || refresh.error)?.message} <button type="button" className="ghost" onClick={() => void detail.refetch()}>Retry sources</button></p>}
      {player.official_reports.map((report, index) => <article key={index} className="injury-report-entry"><h4>Official NFL report</h4><p className="injury-meta">{report.report_period}</p><dl><div><dt>Injury</dt><dd>{report.injury}</dd></div><div><dt>Practice</dt><dd>{report.practice_status}</dd></div><div><dt>Game status</dt><dd>{report.game_status}</dd></div></dl><p className="injury-meta"><SourceLink url={report.url}>NFL report</SourceLink> · Retrieved {time(report.retrieved_at)}</p></article>)}
      {!player.official_reports.length && <p>No matching official entry is available. An absent entry does not confirm health or playing availability.</p>}
      {player.supplemental.map((item, index) => <article key={index} className="injury-report-entry"><h4>Sleeper status</h4><dl><div><dt>Injury</dt><dd>{item.injury || "Not supplied"}</dd></div><div><dt>Practice</dt><dd>{item.practice || "Not supplied"}</dd></div><div><dt>Status</dt><dd>{item.status || "Not supplied"}</dd></div></dl><p className="injury-meta"><SourceLink url={item.url}>Sleeper</SourceLink> · {item.source_status === "stale" ? "Stale · " : ""}Retrieved {time(item.retrieved_at)}</p><p className="injury-note">Individual field update times are not supplied.</p></article>)}
      {!!player.memberships.length && <details className="injury-rosters"><summary>Imported league records ({player.memberships.length})</summary><p>Tags reflect your last roster import and may differ from newer reports.</p><ul>{player.memberships.map((membership) => <li key={membership.player_id}><Link to={`/leagues/${membership.league_id}`}>{membership.league_name}</Link><span>{membership.is_mine ? "My player · " : ""}{membership.fantasy_team || "Free agent"} · {membership.status}{membership.slot ? ` · ${membership.slot}` : ""}</span></li>)}</ul></details>}
    </section>
    {data && <section className="injury-news"><h3>Recent coverage</h3><p className="injury-note">Matching reports from the last 30 days. A headline may discuss teammates; publication dates matter.</p>{data.articles.length ? <ol>{data.articles.map((article) => <li key={article.url}><p className="injury-meta">{article.source} · {article.published_at ? `Published ${time(article.published_at)}` : "Publication date not supplied"}</p><SourceLink url={article.url}>{article.title}</SourceLink>{article.excerpt && <p>{article.excerpt}</p>}<small>{!article.excerpt && "Headline only · "}Retrieved {time(article.retrieved_at)}</small></li>)}</ol> : <p>No matching recent articles were returned by the available sources.</p>}<SourceStatus sources={data.sources} /></section>}
  </section>;
}

export default function InjuryReportPage() {
  const [params, setParams] = useSearchParams();
  const [limit, setLimit] = useState(50);
  const client = useQueryClient();
  const board = useQuery({ queryKey: ["injuries"], queryFn: () => api<InjuryBoard>("/injuries"), staleTime: 60_000, refetchOnMount: "always", refetchInterval: 300_000 });
  const refresh = useMutation({ mutationFn: () => api<InjuryBoard>("/injuries?refresh=true"), onSuccess: (data) => { client.setQueryData(["injuries"], data); void client.invalidateQueries({ queryKey: ["injury-detail"] }); } });
  const search = params.get("search") || "", league = params.get("league") || "", position = params.get("position") || "", team = params.get("team") || "", status = params.get("status") || "", mine = params.get("mine") === "true";
  const selected = params.get("player") || "";
  function change(key: string, value: string) {
    // Browser history updates before React Router commits its transition. Read
    // that latest URL so rapid edits cannot restore an older render's filters.
    const next = new URLSearchParams(window.location.search);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next, { replace: true });
    setLimit(50);
  }
  const rows = board.data?.items || [];
  const filtered = useMemo(() => rows.filter((row) => {
    const memberships = row.memberships.filter((membership) => !league || String(membership.league_id) === league);
    return (!league || memberships.length > 0) && (!mine || memberships.some((membership) => membership.is_mine))
      && (!position || row.position === position) && (!team || row.team === team) && (!status || row.game_status === status)
      && search.toLowerCase().split(/\s+/).every((word) => `${row.name} ${row.team} ${row.position} ${row.injury}`.toLowerCase().includes(word));
  }), [rows, league, mine, position, team, status, search]);
  const activeRow = rows.find((row) => row.key === selected);
  const unsetLeagues = board.data?.leagues.filter((item) => !item.my_team_name && (!league || String(item.id) === league)) || [];
  const hasFilters = Boolean(search || league || position || team || status || mine);
  function reset() {
    const next = new URLSearchParams(window.location.search);
    for (const key of [...next.keys()]) if (key !== "player") next.delete(key);
    setParams(next, { replace: true }); setLimit(50);
  }
  function closeDetails() { change("player", ""); window.requestAnimationFrame(() => document.getElementById(`injury-${selected}`)?.focus({ preventScroll: true })); }
  return <div className="injury-workspace">
    <header className="injury-page-heading"><div><h1>Injury report</h1><p>Track availability across the NFL and your rosters.</p></div><button type="button" className="ghost" disabled={board.isFetching || refresh.isPending} onClick={() => refresh.mutate()}><RefreshCw size={16} />{refresh.isPending ? "Refreshing…" : "Refresh report"}</button></header>
    {board.data && <><p className="injury-counts">{board.data.season} season <span>{rows.length} reported players</span><span>{board.data.my_players} on your rosters</span></p><SourceStatus sources={board.data.sources} /></>}
    {(board.error || refresh.error) && <p role="alert" className="injury-error">Could not refresh the injury report. {(board.error || refresh.error)?.message} <button className="ghost" onClick={() => void board.refetch()}>Try again</button></p>}
    <form className="injury-filters" aria-label="Filter injuries" onSubmit={(event) => event.preventDefault()}>
      <label className="injury-search">Search players or injuries<div><Search size={16} aria-hidden="true" /><input type="search" value={search} placeholder="Player, team, or injury" onChange={(event) => change("search", event.target.value)} /></div></label>
      <label>League<select value={league} onChange={(event) => change("league", event.target.value)}><option value="">All leagues</option>{board.data?.leagues.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label>Position<select value={position} onChange={(event) => change("position", event.target.value)}><option value="">All positions</option>{[...new Set(rows.map((row) => row.position).filter(Boolean))].sort().map((item) => <option key={item}>{item}</option>)}</select></label>
      <label>NFL team<select value={team} onChange={(event) => change("team", event.target.value)}><option value="">All NFL teams</option>{[...new Set(rows.map((row) => row.team))].sort().map((item) => <option key={item}>{item}</option>)}</select></label>
      <label>Game status<select value={status} onChange={(event) => change("status", event.target.value)}><option value="">All statuses</option>{[...new Set(rows.map((row) => row.game_status))].sort().map((item) => <option key={item}>{item}</option>)}</select></label>
      <label className="injury-mine"><input type="checkbox" checked={mine} onChange={(event) => change("mine", event.target.checked ? "true" : "")} />My players</label>
      {hasFilters && <button type="button" className="ghost" onClick={reset}>Clear filters</button>}
    </form>
    {mine && unsetLeagues.length > 0 && <p className="injury-warning">Set your team to include its players: {unsetLeagues.map((item, index) => <span key={item.id}>{index > 0 && ", "}<Link to={`/leagues/${item.id}`}>{item.name}</Link></span>)}.</p>}
    {mine && !board.isPending && board.data?.leagues.length === 0 && <p><Link to="/leagues">Add or import a league</Link> for this season to filter your players.</p>}
    <div className={`injury-content ${activeRow ? "has-selection" : ""}`}>
      <section className="injury-list" aria-label="Injury list">
        <div className="injury-list-heading"><h2>Reported players</h2><span aria-live="polite">{board.isPending ? "Loading…" : `${filtered.length} ${filtered.length === 1 ? "player" : "players"}`}</span></div>
        {board.isPending ? <p role="status">Loading official reports and player status…</p> : <>
          <div className="injury-table-wrap"><table><thead><tr><th scope="col">Player</th><th scope="col">Injury</th><th scope="col" className="injury-practice-cell">Practice</th><th scope="col">Game status</th></tr></thead><tbody>{filtered.slice(0, limit).map((row) => <tr key={row.key} className={selected === row.key ? "is-selected" : ""}>
            <td><button id={`injury-${row.key}`} type="button" className="injury-player-link" aria-expanded={selected === row.key} onClick={() => change("player", row.key)}>{row.name}</button><small>{row.position} · {row.team}{row.is_mine && <span className="injury-owned"> · My player</span>}</small></td>
            <td>{row.injury}</td><td className="injury-practice-cell">{row.practice_status}</td><td><span className={`injury-designation ${/out|doubtful|ir|reserve/i.test(row.game_status) ? "urgent" : row.game_status === "Questionable" ? "questionable" : ""}`}>{row.game_status}</span><small>{row.status_source}</small></td>
          </tr>)}</tbody></table></div>
          {!filtered.length && <div className="injury-empty"><h3>{hasFilters ? "No players match these filters" : "No injury entries available"}</h3><p>{hasFilters ? "Try another player, league, or status." : "Sources may not have published reports yet. An empty list does not establish player availability."}</p><button type="button" className="ghost" onClick={hasFilters ? reset : () => refresh.mutate()}>{hasFilters ? "Clear filters" : "Refresh report"}</button></div>}
          {filtered.length > limit && <button className="ghost injury-more" onClick={() => setLimit((value) => value + 50)}>Show more players ({filtered.length - limit} remaining)</button>}
          <p className="injury-note">Game status is attributed to the source shown. Open a player for report periods, roster tags, recent coverage, and an AI check.</p>
        </>}
      </section>
      {activeRow ? <InjuryPlayer key={activeRow.key} row={activeRow} onClose={closeDetails} /> : selected && board.data ? <p role="status">This player is no longer in the report. <button className="ghost" onClick={() => change("player", "")}>Clear selection</button></p> : null}
    </div>
  </div>;
}
