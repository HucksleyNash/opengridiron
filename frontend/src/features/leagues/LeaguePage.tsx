import { useEffect, useMemo, useState, type ReactNode } from "react";
import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, post } from "../../api";
import type { League, Player } from "../../types";
import { formatSourceLabel } from "../../ui-display-state";
import "./league.css";
import PerformanceDiagnostics from "./PerformanceDiagnostics";
import { PlayerDetailsButton } from "./PlayerDetails";
import { PlayerProjections } from "./PlayerProjections";
import { DetailTabs } from "./DetailTabs";
import { MyTeamSetting } from "./MyTeamSetting";
import { LeagueAnalysisPanel } from "../league-analysis/LeagueAnalysisPanel";
import {
  LINEUP_MODES, lineupChanges, normalizePosition, orderedRoster,
  points, projectionPeriod, rosterGroup, signedPoints, slotLabel, slotOrder,
  type LineupMode, type WeeklyLineup,
} from "./league-display";

type DraftSession = { kind: string; status: string; completed_at?: string; teams: { name: string; is_owner: boolean }[] };
type SyncResult = { players: number; draft_picks?: number; partial?: number; errors?: string[] };
type WaiverRecommendation = { player_id: number; player: Player; rank: number; expected_value: number; confidence: number; rationale: string[] };
type WaiverPage = { items: WaiverRecommendation[]; total: number; available: number; next_offset: number | null; facets: { teams: string[]; statuses: string[]; positions: string[] } };
const EMPTY_FORM = { name: "", pro_team: "", position: "RB", ownership: "FA", rostered_by: "", current_slot: "", projected_points: 0, floor: 0, ceiling: 0, ros_value: "" };
const FORM_LABELS: Record<keyof typeof EMPTY_FORM, string> = {
  name: "Player name (required)", pro_team: "NFL team (required)", position: "Position (required)", ownership: "Availability code",
  rostered_by: "Fantasy team", current_slot: "Roster slot", projected_points: "Projected points", floor: "Lower estimate",
  ceiling: "Upper estimate", ros_value: "Rest-of-season value",
};
const EMPTY_FILTERS = { search: "", role: "", team: "", availability: "", status: "" };
const PAGE_SIZE = 10;
const LEAGUE_TABS = [{ id: "overview", label: "Overview" }, { id: "forecast", label: "Forecast" }] as const;

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}

function Loading({ children }: { children: ReactNode }) {
  return <p className="league-loading" role="status">{children}</p>;
}

function Failure({ title, error, retry }: { title: string; error: unknown; retry?: () => void }) {
  return <div className="league-error" role="alert"><strong>{title}</strong>
    <p>{error instanceof Error ? error.message : "The request could not be completed. Please try again."}</p>
    {retry && <button type="button" onClick={retry}>Try again</button>}
  </div>;
}

function PlayerIdentity({ player, children }: { player: Player; children?: ReactNode }) {
  const status = player.status || "Status unknown";
  const nonActive = status.toLowerCase() !== "active";
  return <span className="league-player"><PlayerDetailsButton player={player} />
    <span className="league-player-meta">{normalizePosition(player.position)} · {player.pro_team || "NFL team unknown"}
      <span className={nonActive ? "league-injury" : ""}>{status}</span>
    </span>{children}
  </span>;
}

function RosterTable({ roster, label, weekly, loading }: { roster: Player[]; label: string; weekly?: WeeklyLineup; loading: boolean }) {
  return <table className="league-roster-table"><caption className="sr-only">{label}. {weekly ? `Week ${weekly.week}` : "Weekly"} projections, independent of the lineup objective.</caption>
    <thead><tr><th scope="col">Slot</th><th scope="col">Player / status</th><th scope="col" className="numeric">{weekly ? `Week ${weekly.week}` : "Weekly"} pts</th></tr></thead>
    <tbody>{roster.map((player) => {
      const forecast = weekly?.forecasts.find((item) => item.player_id === player.id);
      return <tr key={player.id}><td className="league-slot">{slotLabel(player.current_slot)}</td>
        <td><PlayerIdentity player={player} /></td><td className="numeric" title={forecast?.reason || undefined}>{loading ? "Loading…" : points(forecast?.points)}</td></tr>;
    })}</tbody>
  </table>;
}

function WaiverWatchlist({ leagueId, rosterSlots, showReplay, teamName }: { leagueId: number; rosterSlots: string[]; showReplay: boolean; teamName: string }) {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState({ ...EMPTY_FILTERS });
  const [search, setSearch] = useState("");
  const resetPages = (next: typeof EMPTY_FILTERS) => queryClient.setQueryData<InfiniteData<WaiverPage>>(["waivers-page", leagueId, { ...next, team_name: teamName }], (previous) => previous ? { ...previous, pages: previous.pages.slice(0, 1), pageParams: [0] } : previous);
  useEffect(() => { if (filters.search === search) return; const timer = window.setTimeout(() => { resetPages({ ...filters }); setSearch(filters.search); }, 250); return () => window.clearTimeout(timer); }, [filters.search, search]);
  const effectiveFilters = { ...filters, search, team_name: teamName };
  const queryKey = ["waivers-page", leagueId, effectiveFilters];
  const query = useInfiniteQuery({ queryKey, initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => api<WaiverPage>(`/leagues/${leagueId}/waivers/page?${new URLSearchParams({ ...effectiveFilters, offset: String(pageParam), limit: String(PAGE_SIZE) })}`, { signal }),
    getNextPageParam: (lastPage) => lastPage.next_offset ?? undefined,
    placeholderData: keepPreviousData, staleTime: 30_000,
  });
  const first = query.data?.pages[0];
  const visible = query.data?.pages.flatMap((page) => page.items) || [];
  const total = first?.total || 0;
  const available = first?.available || 0;
  const busy = query.isLoading || query.isPlaceholderData || search !== filters.search;
  const updateFilter = (key: keyof typeof filters, value: string) => { const next = { ...filters, [key]: value }; resetPages({ ...next, search: key === "search" ? search : next.search }); setFilters(next); };
  const clearFilters = () => { resetPages({ ...EMPTY_FILTERS }); setFilters({ ...EMPTY_FILTERS }); setSearch(""); };
  const roles = [...new Set(["QB", "RB", "WR", "TE", "K", "DEF", ...(first?.facets.positions || []).map(normalizePosition), ...rosterSlots.map(normalizePosition).filter((slot) => !["BN", "BENCH", "IR", "IR+", "NA"].includes(slot))])];
  const teams = first?.facets.teams || [];
  const statuses = first?.facets.statuses || [];
  const hasFilters = Object.values(filters).some(Boolean);

  return <section className="league-section" aria-labelledby="waiver-watchlist-heading">
    <div className="league-section-heading"><h2 id="waiver-watchlist-heading">Waiver watchlist</h2>{showReplay && <Link to={`/draft/${leagueId}`}>Open draft replay</Link>}</div>
    <p className="league-help">Available players for {teamName || "this league"}. Matching weekly inputs support starting-lineup gains; other periods are a review list. Filter by position to compare similar players.</p>
    <details className="league-disclosure league-method"><summary>How rankings and estimates work</summary>
      <p>Matching weekly projections and scoring support a modeled starting-lineup gain for the selected team. Otherwise the list displays stored projection values for review; mixed periods cannot establish an add recommendation.</p>
      <p>FAAB bids are withheld until budget and comparable winning-bid evidence are available. Unavailable players are excluded. Prediction confidence is not calibrated.</p>
      <p>Check source age, scoring, period, game locks and long-term drop cost before acting. Missing rest-of-season values stay missing.</p>
    </details>
    <div className="league-waiver-filters">
      <Field label="Search players"><input type="search" placeholder="Name, NFL team, or position" value={filters.search} onChange={(event) => updateFilter("search", event.target.value)} /></Field>
      <Field label="Position / role"><select value={filters.role} onChange={(event) => updateFilter("role", event.target.value)}><option value="">All roles</option>{roles.map((role) => <option key={role} value={role}>{role === "DEF" ? "DEF / D/ST" : role}</option>)}</select></Field>
      <Field label="NFL team"><select value={filters.team} onChange={(event) => updateFilter("team", event.target.value)}><option value="">All teams</option>{teams.map((team) => <option key={team}>{team}</option>)}</select></Field>
      <Field label="Availability"><select value={filters.availability} onChange={(event) => updateFilter("availability", event.target.value)}><option value="">All available</option><option value="free-agent">Free agents</option><option value="waivers">On waivers</option></select></Field>
      <Field label="Player status"><select value={filters.status} onChange={(event) => updateFilter("status", event.target.value)}><option value="">All statuses</option>{statuses.map((status) => <option key={status}>{status}</option>)}</select></Field>
    </div>
    <div className="league-results"><span role="status">{busy ? "Loading available-player rankings…" : query.error && !first ? "Rankings unavailable" : `Showing ${visible.length} of ${total} matching players · ${available} available`}</span>{hasFilters && <button type="button" className="ghost" onClick={clearFilters}>Clear filters</button>}</div>
    {query.error && <><Failure title="Could not load waiver rankings" error={query.error} /><button type="button" onClick={() => void (query.isFetchNextPageError ? query.fetchNextPage() : query.refetch())}>Retry watchlist</button></>}
    {busy ? <p className="league-help">Loading waiver watchlist</p> : !first ? null : !available ? <p>No available players. Sync the league or import free agents using the data tools below.</p> : !total ? <p>No players match your filters.</p> : <>
      <div role="region" aria-label="Waiver player results">
        <table className="league-waiver-table" role="table"><caption className="sr-only">Overall league-wide rank, player availability, rank score, and ranking details. Overall ranks remain unchanged when filtered.</caption>
          <thead role="rowgroup"><tr role="row"><th role="columnheader" scope="col">Overall rank</th><th role="columnheader" scope="col">Player / availability</th><th role="columnheader" scope="col" className="numeric">Rank score</th><th role="columnheader" scope="col">Evidence & estimates</th></tr></thead>
          <tbody role="rowgroup">{visible.map((item) => <tr key={item.player_id} role="row">
            <td role="cell" className="league-rank"><span className="league-mobile-label">Overall</span>{item.rank}</td>
            <td role="cell"><PlayerIdentity player={item.player}><span className="league-availability">{["W", "WAIVERS"].includes(item.player.ownership.toUpperCase()) ? "On waivers" : "Free agent"}</span></PlayerIdentity></td>
            <td role="cell" className="numeric"><span className="league-mobile-label">Score</span>{points(item.expected_value)}</td>
            <td role="cell" className="league-waiver-details">
              <span className="league-waiver-estimate">{points(item.player.projected_points)} projected pts</span>
              <span className="league-projection-period">{projectionPeriod(item.player.projection)}</span>
              <PlayerDetailsButton player={item.player} initialTab="projections" ranking={item} label={`Ranking details for ${item.player.name}`}>Ranking details</PlayerDetailsButton>
            </td>
          </tr>)}</tbody>
        </table>
      </div>
      <div className="league-results">{query.hasNextPage && <button type="button" className="ghost" disabled={query.isFetching} onClick={() => void query.fetchNextPage()}>{query.isFetchingNextPage ? "Loading players…" : "Show more players"}</button>}
        {visible.length > PAGE_SIZE && <button type="button" className="ghost" disabled={query.isFetching} onClick={() => queryClient.setQueryData(queryKey, { ...query.data, pages: query.data?.pages.slice(0, 1), pageParams: [0] })}>Show first 10</button>}</div>
    </>}
  </section>;
}

function LeagueWorkspace({ id, draftSuiteEnabled }: { id: number; draftSuiteEnabled: boolean }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") === "forecast" ? "forecast" : "overview";
  const selectTab = (value: "overview" | "forecast") => setSearchParams((previous) => {
    const next = new URLSearchParams(previous);
    if (value === "overview") next.delete("tab");
    else next.set("tab", value);
    return next;
  });
  const queryClient = useQueryClient();
  const leagueQuery = useQuery({ queryKey: ["league", id], queryFn: () => api<League>(`/leagues/${id}`) });
  const league = leagueQuery.data;
  const playersQuery = useQuery({ queryKey: ["roster", id], queryFn: ({ signal }) => api<Player[]>(`/leagues/${id}/roster`, { signal }), enabled: Boolean(league) });
  const players = playersQuery.data || [];
  const draftQuery = useQuery({ queryKey: ["draft-sessions-v2", id], queryFn: () => api<DraftSession[]>(`/leagues/${id}/draft-sessions`), enabled: draftSuiteEnabled });
  const completedDraft = draftQuery.data?.filter((session) => session.kind === "live" && session.status === "COMPLETE").sort((a, b) => (b.completed_at || "").localeCompare(a.completed_at || ""))[0];
  const owner = completedDraft?.teams.find((team) => team.is_owner)?.name;
  const analysisContext = useQuery({ queryKey: ["weekly-analysis-context", id], queryFn: () => api<{ teams: string[]; suggested_week: number; schedule_available: boolean }>(`/leagues/${id}/analysis-context`), enabled: Boolean(league) });
  const teams = useMemo(() => [...new Set([...players.map((player) => player.rostered_by).filter((name): name is string => Boolean(name)), ...(analysisContext.data?.teams || [])])].sort(), [players, analysisContext.data]);
  const setContext = (key: "team" | "week", value: string) => setSearchParams((previous) => {
    const next = new URLSearchParams(previous);
    if (value) next.set(key, value);
    else next.delete(key);
    return next;
  });
  const teamChoice = searchParams.get("team") || "";
  const setTeamChoice = (value: string) => setContext("team", value);
  const defaultTeam = league?.my_team_name ? (teams.includes(league.my_team_name) ? league.my_team_name : "") : owner && teams.includes(owner) ? owner : teams[0] || "";
  const selectedTeam = teamChoice ? (teams.includes(teamChoice) ? teamChoice : "") : defaultTeam;
  const [mode, setMode] = useState<LineupMode>("balanced");
  const requestedWeek = searchParams.get("week") || "";
  const weekChoice = /^(?:[1-9]|1[0-8])$/.test(requestedWeek) ? requestedWeek : "";
  const setWeekChoice = (value: string) => setContext("week", value);
  const roster = useMemo(() => orderedRoster(players.filter((player) => player.rostered_by === selectedTeam), league?.roster_slots || []), [players, selectedTeam, league?.roster_slots]);
  const lineupQuery = useQuery({
    queryKey: ["weekly-lineup", id, mode, selectedTeam, weekChoice],
    queryFn: ({ signal }) => api<WeeklyLineup>(`/leagues/${id}/weekly-lineup?${new URLSearchParams({ mode, team_name: selectedTeam, ...(weekChoice ? { week: weekChoice } : {}) })}`, { signal }),
    enabled: Boolean(selectedTeam) && !playersQuery.error, staleTime: 60_000,
  });
  const weekly = lineupQuery.data;
  const lineup = useMemo(() => weekly ? { ...weekly, assignments: weekly.assignments.flatMap((item) => {
    const player = roster.find((entry) => entry.id === item.player_id);
    return player ? [{ ...item, player }] : [];
  }) } : undefined, [weekly, roster]);
  const lineupError = lineupQuery.error || (weekly?.error ? new Error(weekly.error) : null);
  const weekLabel = weekly ? `${weekly.season} · Week ${weekly.week}` : weekChoice ? `Week ${weekChoice}` : "Current NFL week";
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [projectionForm, setProjectionForm] = useState({ source: "", period: "unknown", season: "", week: "", source_updated_at: "", scoring_basis: "unknown" });
  const [importFile, setImportFile] = useState<File | null>(null);
  const [syncCompletedAt, setSyncCompletedAt] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  useEffect(() => {
    if (lineup) setAnnouncement(`${LINEUP_MODES[mode].label} Week ${lineup.week} lineup for ${selectedTeam}: ${points(lineup.projected_total)} points, ${signedPoints(lineup.projected_gain)} compared with current starters.`);
  }, [lineup, mode, selectedTeam]);
  const refresh = async () => {
    await Promise.all([["players", id], ["roster", id], ["projection-leaders", id], ["league", id], ["leagues"], ["dashboard"], ["lineup", id], ["weekly-lineup", id], ["waivers", id], ["waivers-page", id], ["draft-board-v2"]].map((queryKey) => queryClient.invalidateQueries({ queryKey })));
  };
  const add = useMutation({ mutationFn: () => post(`/leagues/${id}/players`, { ...form, ros_value: form.ros_value.trim() === "" ? null : Number(form.ros_value), rostered_by: form.rostered_by || null, current_slot: form.current_slot || null, status: "Active", risk: 0.5, evidence: [],
    projection: { ...projectionForm, source: projectionForm.source || "Manual entry", season: projectionForm.period === "unknown" ? null : Number(projectionForm.season || league?.season), week: ["week", "rest_of_season"].includes(projectionForm.period) && projectionForm.week ? Number(projectionForm.week) : null, source_updated_at: projectionForm.source_updated_at ? new Date(projectionForm.source_updated_at).toISOString() : null, scoring: projectionForm.scoring_basis === "league_rules" ? league?.scoring : null },
  }), onSuccess: async () => { setForm({ ...EMPTY_FORM }); await refresh(); } });
  const importPlayers = useMutation({ mutationFn: async () => {
    if (!importFile) throw new Error("Choose a CSV or JSON file.");
    const body = new FormData(); body.append("file", importFile);
    return api<{ created: number; updated: number }>(`/leagues/${id}/players/import`, { method: "POST", body });
  }, onSuccess: refresh });
  const importDraft = useMutation({ mutationFn: () => post<SyncResult>(`/leagues/${id}/sync/draft-roster`), onSuccess: refresh });
  const sync = useMutation({ mutationFn: () => post<SyncResult>(`/leagues/${id}/sync/yahoo-scraper`), onSuccess: async () => {
    setSyncCompletedAt(new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" }).format(new Date())); await refresh();
  } });

  if (leagueQuery.isLoading) return <div className="league-page league-body-loading"><Loading>Loading league…</Loading></div>;
  if (!league) return <Failure title="Could not load this league" error={leagueQuery.error} retry={() => void leagueQuery.refetch()} />;
  const rosteredCount = players.filter((player) => player.rostered_by).length;
  const changes = lineup && !lineupError ? lineupChanges(roster, lineup, league.roster_slots) : null;
  const starters = roster.filter((player) => rosterGroup(player, league.roster_slots) === "Starters");
  const reserves = ["Bench", "Reserve", "Unassigned"] as const;
  const syncPending = sync.isPending || importDraft.isPending || add.isPending || importPlayers.isPending;
  const scoring = Object.entries(league.scoring);
  const datedPlayers = roster.filter((player) => player.projection?.source_updated_at).length;

  return <div className="league-page">
    <header className="page-header league-header"><div><p className="league-source">{formatSourceLabel(league.source)} · {league.season}</p><h1>{league.name}</h1></div>
      {draftSuiteEnabled && <Link className="button ghost" to={`/draft/${id}`}>Open draft room</Link>}
    </header>
    {leagueQuery.error && <Failure title="League refresh failed; showing previously loaded settings" error={leagueQuery.error} retry={() => void leagueQuery.refetch()} />}
    <details className="league-team-setting" open={!league.my_team_name || (!playersQuery.isLoading && !teams.includes(league.my_team_name))}>
      <summary>My team: {league.my_team_name || "Not set"}<span>Change</span></summary>
      <MyTeamSetting league={league} rosterTeams={teams} loading={playersQuery.isLoading} error={Boolean(playersQuery.error)} onSaved={() => setTeamChoice("")} />
    </details>
    <DetailTabs id="league-view" label="League views" tabs={LEAGUE_TABS} selected={tab} onSelect={selectTab} />
    <div className="league-working-context" role="group" aria-label="Team and week for both league views">
      <Field label="Fantasy team"><select value={selectedTeam} onChange={(event) => setTeamChoice(event.target.value)} disabled={!teams.length}>
        {!selectedTeam && <option value="">{playersQuery.isLoading ? "Loading teams…" : playersQuery.error ? "Teams unavailable" : teamChoice ? "Selected team unavailable" : league.my_team_name ? "No roster for my team" : "No roster imported"}</option>}{teams.map((team) => <option key={team}>{team}</option>)}
      </select></Field>
      <Field label="Projection week"><select value={weekChoice} onChange={(event) => setWeekChoice(event.target.value)}><option value="">{analysisContext.data ? `Current: ${analysisContext.data.suggested_week}` : "Current"}</option>{Array.from({ length: 18 }, (_, index) => <option key={index + 1} value={index + 1}>Week {index + 1}</option>)}</select></Field>
      <span className="league-context-hint">Shared across Overview and Forecast. Does not change My team.</span>
    </div>
    {analysisContext.error && <Failure title="Could not refresh the current NFL week" error={analysisContext.error} retry={() => void analysisContext.refetch()} />}
    <div className="league-view-panel" role="tabpanel" id="league-view-panel-overview" aria-labelledby="league-view-tab-overview" hidden={tab !== "overview"} tabIndex={0}>
    <div className="league-context">
      <div className="league-context-copy"><strong>{playersQuery.isLoading ? "Loading roster data…" : playersQuery.error ? "Roster data unavailable" : `${rosteredCount} rostered players across ${teams.length} teams`}</strong>
        <span>{datedPlayers ? `Source update times recorded for ${datedPlayers}/${roster.length} selected-team players` : "Source update times not supplied"}{syncCompletedAt ? ` · Sync completed at ${syncCompletedAt} this visit` : ""}</span></div>
      {league.yahoo_key && <button type="button" className="ghost" disabled={syncPending} onClick={() => sync.mutate()}><RefreshCw size={16} className={sync.isPending ? "spin" : ""} />{sync.isPending ? "Syncing Yahoo…" : "Sync Yahoo roster"}</button>}
    </div>
    <div className="league-action-feedback" aria-live="polite">
      {sync.data && <p className={sync.data.partial || sync.data.errors?.length ? "league-warning" : "success"}>Sync returned {sync.data.players} players and {sync.data.draft_picks || 0} draft picks.{sync.data.partial ? " Player coverage is partial." : ""}{sync.data.errors?.length ? ` ${sync.data.errors.join(" · ")}` : ""}</p>}
      {importDraft.data && <p className="success">Imported {importDraft.data.players} draft picks into the roster.</p>}
      {add.isSuccess && <p className="success">Player added.</p>}
      {importPlayers.data && <p className="success">Import complete: {importPlayers.data.created} created, {importPlayers.data.updated} updated.</p>}
      {add.error && <p className="league-warning">Player was not added. Open data tools to review the error and retry.</p>}
      {importPlayers.error && <p className="league-warning">Player import failed. Open data tools to review the error and retry.</p>}
    </div>
    {(sync.error || importDraft.error) && <Failure title="Roster sync failed" error={sync.error || importDraft.error} retry={() => sync.error ? sync.mutate() : importDraft.mutate()} />}
    <p className="league-data-note">{weekLabel} · Open Gridiron weekly projections</p>
    <nav className="league-section-nav" aria-label="League sections"><a href="#lineup-review">Lineup</a><a href="#current-roster">Roster</a><a href="#player-projections">Projections</a><a href="#waiver-watchlist-heading">Waivers</a><a href="#league-data-tools">Data tools</a></nav>
    {playersQuery.isLoading ? <div className="league-body-loading"><Loading>Loading roster and available players…</Loading></div> : playersQuery.error ? <Failure title="Could not load roster data" error={playersQuery.error} retry={() => void playersQuery.refetch()} /> : <>
      <section className="league-section" id="lineup-review" aria-labelledby="lineup-review-heading">
        <div className="league-section-heading"><h2 id="lineup-review-heading">Lineup review</h2>
          <fieldset className="league-mode"><legend>Lineup objective</legend><div>{(Object.keys(LINEUP_MODES) as LineupMode[]).map((value) => <button type="button" key={value} aria-pressed={mode === value} onClick={() => setMode(value)}>{LINEUP_MODES[value].label}</button>)}</div></fieldset>
        </div>
        <span className="sr-only" role="status">{announcement}</span>
        {!selectedTeam ? <p>No roster imported. <a className="league-text-link" href="#league-data-tools">Import a roster or connect Yahoo</a> to compare starters.</p> : lineupQuery.isLoading ? <div className="league-lineup-loading"><Loading>Calculating {LINEUP_MODES[mode].label.toLowerCase()} lineup…</Loading></div> : lineupError ? <Failure title="Could not calculate the lineup" error={lineupError} retry={() => void lineupQuery.refetch()} /> : lineup && <>
          <dl className="league-comparison" aria-label={`${LINEUP_MODES[mode].label} lineup comparison`}>
            <div><dt>Current · {LINEUP_MODES[mode].label}{lineup.partial_total ? " · modeled portion" : ""}</dt><dd>{points(lineup.current_total)} <small>pts</small></dd></div>
            <div><dt>Recommended · {LINEUP_MODES[mode].label}{lineup.partial_total ? " · modeled portion" : ""}</dt><dd>{points(lineup.projected_total)} <small>pts</small></dd></div>
            <div><dt>Potential change</dt><dd className={(lineup.projected_gain ?? 0) < 0 ? "league-warning" : "league-positive"}>{signedPoints(lineup.projected_gain)} <small>pts</small></dd></div>
          </dl>
          <details className="league-disclosure league-objective-help"><summary>About the {LINEUP_MODES[mode].label.toLowerCase()} objective</summary>
            <p>{LINEUP_MODES[mode].description} Both lineup totals use this weekly objective. Current roster rows show the central weekly projection. Waiver rankings and imported source projections keep their original values.</p>
            <p>Weekly estimates use recent NFL game production scored for your league. This experimental baseline does not adjust for matchup, weather, or injury news.</p>
          </details>
          {lineup.partial_total && <p className="league-warning" role="status">Some players lack weekly forecasts. Totals include only modeled players; unavailable estimates are not zero. Starters without a forecast stay in place.</p>}
          {lineup.unfilled_slots.length > 0 && <p className="league-warning" role="alert">Unfilled starter slots: {lineup.unfilled_slots.map(slotLabel).join(", ")}. The recommended total is incomplete.</p>}
          <div className="league-changes">
            <h3>Suggested changes</h3>
            {changes && (changes.start.length || changes.bench.length) ? <ul>
              {changes.start.map(({ player, slot }) => <li key={`start-${player.id}`}><PlayerDetailsButton player={player}><strong>Start {player.name}</strong></PlayerDetailsButton><span>{slotLabel(player.current_slot)} to {slotLabel(slot)} · {player.status}</span></li>)}
              {changes.bench.map((player) => <li key={`bench-${player.id}`}><PlayerDetailsButton player={player}><strong>Bench {player.name}</strong></PlayerDetailsButton><span>Currently {slotLabel(player.current_slot)} · {player.status}</span></li>)}
            </ul> : <p>{lineup.unfilled_slots.length ? "No start/bench changes among assigned players." : "Your current starters already match the recommended player group."}</p>}
            <p className="league-caption">Games already started stay locked. Other injury statuses are conditional on playing. Review availability and eligibility before making changes in Yahoo. Open Gridiron does not submit lineup changes.</p>
          </div>
        </>}
      </section>
      <div className="league-roster-grid">
        <section className="league-section" aria-labelledby="recommended-starters-heading"><h2 id="recommended-starters-heading">Recommended starters</h2>
          <p className="league-caption">{weekLabel} · {LINEUP_MODES[mode].label.toLowerCase()} weekly estimates</p>
          {!selectedTeam ? <p>No team roster available.</p> : lineupQuery.isLoading ? <Loading>Loading recommended starters…</Loading> : lineupError ? <p>Recommendations unavailable. Retry the lineup calculation above.</p> : lineup && <table className="league-roster-table">
            <caption className="sr-only">Recommended starters using {LINEUP_MODES[mode].label.toLowerCase()} weekly estimates</caption><thead><tr><th scope="col">Slot</th><th scope="col">Player / status</th><th scope="col" className="numeric">{weekly ? `Week ${weekly.week}` : "Weekly"} pts</th></tr></thead>
            <tbody>{[...lineup.assignments].sort((a, b) => slotOrder(a.slot) - slotOrder(b.slot)).map(({ slot, player, score }) => <tr key={`${slot}-${player.id}`}><td className="league-slot">{slotLabel(slot)}</td><td><PlayerIdentity player={player} />{changes?.start.some((item) => item.player.id === player.id) && <span className="league-positive league-caption">Suggested start</span>}</td><td className="numeric">{points(score)}</td></tr>)}</tbody>
          </table>}
        </section>
        <section className="league-section" id="current-roster" aria-labelledby="current-roster-heading"><h2 id="current-roster-heading">Current roster</h2><p className="league-caption">Imported slots · {weekLabel.toLowerCase()} projected points</p>
          {starters.length ? <RosterTable roster={starters} label="Current starters" weekly={weekly} loading={lineupQuery.isLoading} /> : <p>No assigned starters in this roster.</p>}
          {reserves.map((group) => { const grouped = roster.filter((player) => rosterGroup(player, league.roster_slots) === group); return grouped.length ? <details className="league-disclosure" key={group}><summary>{group} · {grouped.length} {grouped.length === 1 ? "player" : "players"}</summary><RosterTable roster={grouped} label={`${group} players`} weekly={weekly} loading={lineupQuery.isLoading} /></details> : null; })}
        </section>
      </div>
      <PlayerProjections leagueId={id} roster={roster} team={selectedTeam} />
      <WaiverWatchlist leagueId={id} rosterSlots={league.roster_slots} showReplay={Boolean(completedDraft)} teamName={selectedTeam} />
    </>}
    <details className="league-disclosure league-section" id="league-data-tools"><summary>League settings & data tools</summary>
      <h3>Data sources</h3><p className="league-help">Yahoo sync reads roster moves, ownership, slots, status, and draft results. It may replace stored projections when Yahoo supplies them. It does not submit changes to Yahoo. Completed live drafts also populate the local roster.</p>
      {!league.yahoo_key && <Link className="button ghost" to="/settings">Connect Yahoo scraper</Link>}
      {completedDraft && rosteredCount === 0 && <button type="button" disabled={syncPending} onClick={() => importDraft.mutate()}>{importDraft.isPending ? "Importing draft…" : "Use completed live draft"}</button>}
      {draftQuery.error && <Failure title="Draft import availability could not be checked" error={draftQuery.error} retry={() => void draftQuery.refetch()} />}
      <h3>League scoring & roster slots</h3><p className="league-help">Current league settings are shown below. Projection sources retain the scoring context supplied with each import; stored totals are not automatically recalculated when settings change.</p>
      {scoring.length ? <dl className="league-scoring">{scoring.map(([stat, value]) => <div key={stat}><dt>{stat.replaceAll("_", " ")}</dt><dd>{value}</dd></div>)}</dl> : <p>No explicit scoring settings stored.</p>}
      <p className="league-caption">Roster slots: {league.roster_slots.map(slotLabel).join(" · ")}</p>
      <h3>Add a player manually</h3><p className="league-help">For availability, use FA (free agent) or W (waivers). For rostered players, enter the exact fantasy-team name and a roster slot; BN means bench. Use the same projection period as your existing data.</p>
      <form className="league-player-form" onSubmit={(event) => { event.preventDefault(); add.mutate(); }}>
        {(Object.keys(form) as (keyof typeof form)[]).map((key) => <Field key={key} label={FORM_LABELS[key]}><input value={form[key]} type={typeof form[key] === "number" || key === "ros_value" ? "number" : "text"} step="0.1" onChange={(event) => setForm({ ...form, [key]: typeof form[key] === "number" ? Number(event.target.value) : event.target.value })} required={["name", "pro_team", "position"].includes(key)} /></Field>)}
        <Field label="Projection source"><input value={projectionForm.source} placeholder="Provider or model name" onChange={(event) => setProjectionForm({ ...projectionForm, source: event.target.value })} /></Field>
        <Field label="Projection period"><select value={projectionForm.period} onChange={(event) => setProjectionForm({ ...projectionForm, period: event.target.value })}><option value="unknown">Unknown</option><option value="season">Full season</option><option value="week">One week</option><option value="rest_of_season">Rest of season</option></select></Field>
        {projectionForm.period !== "unknown" && <Field label="Projection season"><input type="number" min="2000" max="2100" value={projectionForm.season || league.season} onChange={(event) => setProjectionForm({ ...projectionForm, season: event.target.value })} /></Field>}
        {["week", "rest_of_season"].includes(projectionForm.period) && <Field label={projectionForm.period === "week" ? "Projection week (required)" : "From week (optional)"}><input type="number" min="1" max="30" required={projectionForm.period === "week"} value={projectionForm.week} onChange={(event) => setProjectionForm({ ...projectionForm, week: event.target.value })} /></Field>}
        <Field label="Source updated (your local time)"><input type="datetime-local" value={projectionForm.source_updated_at} onChange={(event) => setProjectionForm({ ...projectionForm, source_updated_at: event.target.value })} /></Field>
        <Field label="Projection scoring"><select value={projectionForm.scoring_basis} onChange={(event) => setProjectionForm({ ...projectionForm, scoring_basis: event.target.value })}><option value="unknown">Unknown</option><option value="source_points">Provider-scored points</option><option value="league_rules">Uses the league rules above</option></select></Field>
        <button className="primary" disabled={syncPending}>{add.isPending ? "Adding player…" : "Add player"}</button>
      </form>{add.error && <Failure title="Could not add the player" error={add.error} />}
      <p className="league-help">Leave rest-of-season value blank when missing; enter 0 only when the source explicitly reports zero. Leave source update time blank unless the provider supplies it.</p>
      <h3>Import players & projections</h3><p className="league-help" id="league-import-help">Choose CSV or a JSON array of players. Use name, pro_team, position, and projected_points; include source_id for stable matching. Imports update matching records and create new ones. Optional fields include ownership, rostered_by, current_slot, status, floor, ceiling, ros_value, and risk.</p>
      <p className="league-help">CSV provenance columns: projection_source, projection_period (season, week, rest_of_season, or unknown), projection_season, projection_week, projection_source_updated_at (ISO date with timezone), projection_scoring_basis (source_points, league_rules, or unknown), and projection_scoring (JSON rules). JSON imports accept the same metadata under a projection object without the projection_ prefixes. Blank or null ros_value means missing; 0 means supplied zero.</p>
      <div className="league-import-row"><Field label="Player data file"><input type="file" accept=".csv,.json" aria-describedby="league-import-help" onChange={(event) => { setImportFile(event.target.files?.[0] || null); importPlayers.reset(); }} /></Field><button type="button" disabled={!importFile || syncPending} onClick={() => importPlayers.mutate()}>{importPlayers.isPending ? "Importing players…" : "Import CSV / JSON"}</button></div>
      {importPlayers.error && <Failure title="Player import failed. Check the file and try again." error={importPlayers.error} />}
    </details>
    <PerformanceDiagnostics />
    </div>
    <div className="league-view-panel" role="tabpanel" id="league-view-panel-forecast" aria-labelledby="league-view-tab-forecast" hidden={tab !== "forecast"} tabIndex={0}>
      {tab === "forecast" && <LeagueAnalysisPanel league={league} selectedTeam={selectedTeam} selectedWeek={Number(weekChoice) || analysisContext.data?.suggested_week || weekly?.week || 1} onWeekChange={(week) => setWeekChoice(String(week))} />}
    </div>
  </div>;
}

export default function LeaguePage({ draftSuiteEnabled }: { draftSuiteEnabled: boolean }) {
  const id = Number(useParams().leagueId);
  return Number.isInteger(id) && id > 0 ? <LeagueWorkspace key={id} id={id} draftSuiteEnabled={draftSuiteEnabled} /> : <p>Invalid league. <Link to="/leagues">Return to leagues</Link>.</p>;
}
