import { FormEvent, ReactNode, Suspense, lazy, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  ChevronRight,
  ClipboardList,
  Copy,
  DraftingCompass,
  ExternalLink,
  Home,
  HeartPulse,
  KeyRound,
  Menu,
  Newspaper,
  RefreshCw,
  Settings,
  ShieldCheck,
  Trash2,
  Trophy,
  Users,
} from "lucide-react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useSearchParams } from "react-router-dom";
import { PlayerDetailsButton, PlayerDetailsProvider, PlayerMentions } from "./features/leagues/PlayerDetails";
import { api, ApiError, post, remove } from "./api";
import { ProviderEditor, ProviderTasks } from "./features/analysis/ProviderEditor";
import { providerModelControl, providerModelPlaceholder, supportsProviderModelDiscovery } from "./provider-model-state";
import type { Game, League, Pool, Provider } from "./types";
import { filterNewsItems, formatSourceLabel, type NewsFeedItem, uniqueAlertsByTitle } from "./ui-display-state";

const AnalysisWorkspace = lazy(() => import("./features/analysis/AnalysisWorkspace"));
const InjuryReportPage = lazy(() => import("./features/injuries/InjuryReportPage"));
const LeaguePage = lazy(() => import("./features/leagues/LeaguePage"));
const DraftRoute = lazy(() => import("./features/draft/DraftRoute").then((module) => ({ default: module.DraftRoute })));
const PoolsOverviewPage = lazy(() => import("./features/pools/PoolsOverviewPage").then((module) => ({ default: module.PoolsOverviewPage })));
const PoolWeekPage = lazy(() => import("./features/pools/PoolWeekPage").then((module) => ({ default: module.PoolWeekPage })));

type Onboarding = { configured: boolean; auth_required: boolean; environment: string; timezone: string; capabilities: { draft_suite: boolean } };
type Alert = { id: number; title: string; message: string; severity: string; url?: string; read: boolean; created_at: string };
type DashboardSnapshot = { id: number; source: string; retrieved_at: string; status: string };
type DashboardNewsStatus = { id: number; name: string; enabled: boolean; official: boolean; last_fetched_at?: string };
type Dashboard = { leagues: League[]; active_draft?: { id: number; league_id: number; kind: "live" | "mock"; status: string } | null; pools: Pool[]; alerts: Alert[]; snapshots: DashboardSnapshot[]; news_sources?: DashboardNewsStatus[]; analysis_runs: { id: number; task: string; model: string; status: string; created_at: string }[] };
type MarketRecommendation = { player_id?: number; rank: number; subject: string; expected_value: number; confidence: number; data_as_of: string };
type DashboardNewsSource = { id: number; name: string; enabled: boolean };
type DashboardRefreshOutcome = { status: "success" | "partial" | "error"; message: string; details: string[] };
type CodexAuthStatus = { status: "idle" | "starting" | "pending" | "authenticated" | "error"; authenticated: boolean; verification_url?: string; user_code?: string; message: string; expires_in?: number };
type YahooScraperStatus = { configured: boolean; has_cookie: boolean; league_urls: string[] };
type ProviderDraft = { name: string; provider_type: string; model: string; base_url: string; api_key: string; task_defaults: string[] };
type LeagueDraftSession = { id: number; kind: "live" | "mock"; status: string; team_count: number; round_count: number; owner_team_slot: number; completed_at?: string; archived_at?: string | null; teams: Array<{ name: string; is_owner: boolean }> };

const PROVIDER_DEFAULTS: Record<string, Pick<ProviderDraft, "name" | "model" | "base_url">> = {
  openai: { name: "OpenAI", model: "", base_url: "" },
  anthropic: { name: "Anthropic", model: "", base_url: "" },
  openai_compatible: { name: "Local model", model: "", base_url: "" },
  codex: { name: "Codex CLI", model: "", base_url: "" },
};

function apiTimestamp(value: string): Date {
  return new Date(/(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`);
}

function relativeAge(value: string): string {
  const seconds = Math.max(0, Math.floor((Date.now() - apiTimestamp(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

function clockTime(value: string): string {
  return new Intl.DateTimeFormat("en-US", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "America/Chicago" }).format(apiTimestamp(value));
}

function dashboardWeek(games: Game[]): { week?: number; nextGame?: Game; games: Game[] } {
  const sorted = games.slice().sort((left, right) => apiTimestamp(left.kickoff).getTime() - apiTimestamp(right.kickoff).getTime());
  const nextGame = sorted.find((game) => apiTimestamp(game.kickoff).getTime() > Date.now());
  const anchor = nextGame || sorted.at(-1);
  if (!anchor) return { games: [] };
  return { week: anchor.week, nextGame, games: sorted.filter((game) => game.week === anchor.week).slice(0, 3) };
}

function gameLeader(game: Game): string {
  const homeLeads = game.home_win_probability >= 0.5;
  const probability = homeLeads ? game.home_win_probability : 1 - game.home_win_probability;
  return `${homeLeads ? game.home_team : game.away_team} ${Math.round(probability * 100)}%`;
}

function marketSubject(subject: string): { name: string; position: string; team: string } {
  const parsed = subject.match(/^(.*) \(([^,]+), ([^)]+)\)$/);
  return parsed ? { name: parsed[1], position: parsed[2], team: parsed[3] } : { name: subject, position: "—", team: "—" };
}

function readableList(items: string[]): string {
  if (items.length < 2) return items[0] || "";
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(", ")}, and ${items.at(-1)}`;
}

async function refreshCommandCenterSources(): Promise<DashboardRefreshOutcome> {
  const completed: string[] = [];
  const failures: string[] = [];
  const details: string[] = [];
  const [yahoo, sourceRegistry, dataRegistry] = await Promise.allSettled([
    post("/integrations/yahoo/scraper/sync"),
    api<DashboardNewsSource[]>("/news/sources"),
    api<FootballDataSource[]>("/data-sources"),
  ]);

  if (yahoo.status === "fulfilled") {
    completed.push("Yahoo roster/market", "NFLverse draft models");
  } else {
    failures.push("Yahoo roster/market and NFLverse draft models");
    details.push(yahoo.reason instanceof Error ? yahoo.reason.message : "Yahoo and NFLverse refresh failed.");
  }

  if (sourceRegistry.status === "fulfilled") {
    const enabledSources = sourceRegistry.value.filter((source) => source.enabled !== false);
    const newsResults = await Promise.allSettled(enabledSources.map((source) => post(`/news/sources/${source.id}/fetch`)));
    const refreshedNews = newsResults.filter((result) => result.status === "fulfilled").length;
    if (refreshedNews) completed.push(`${refreshedNews} news source${refreshedNews === 1 ? "" : "s"}`);
    newsResults.forEach((result, index) => {
      if (result.status === "rejected") {
        failures.push(enabledSources[index].name);
        details.push(result.reason instanceof Error ? `${enabledSources[index].name}: ${result.reason.message}` : `${enabledSources[index].name} failed.`);
      }
    });
  } else {
    failures.push("news sources");
    details.push(sourceRegistry.reason instanceof Error ? sourceRegistry.reason.message : "News source registry failed.");
  }

  if (dataRegistry.status === "fulfilled") {
    const results = await Promise.allSettled(dataRegistry.value.map((source) => post<FootballDataSource>(`/data-sources/${source.key}/fetch`)));
    results.forEach((result, index) => {
      const name = dataRegistry.value[index].name;
      if (result.status === "fulfilled" && result.value.status === "available") completed.push(name);
      else {
        failures.push(name);
        details.push(result.status === "fulfilled" ? `${name}: ${result.value.last_error || result.value.status}` : `${name}: refresh failed.`);
      }
    });
  } else {
    failures.push("player and draft data");
    details.push("Player and draft source registry failed.");
  }

  const status = failures.length === 0 ? "success" : completed.length ? "partial" : "error";
  const completedText = completed.length ? `Refreshed ${readableList(completed)}.` : "No sources refreshed.";
  return {
    status,
    message: failures.length ? `${completedText} Failed: ${readableList(failures)}.` : completedText,
    details,
  };
}

function useDashboardData() {
  return useQuery({ queryKey: ["dashboard"], queryFn: () => api<Dashboard>("/dashboard"), refetchInterval: 60_000 });
}

function useDashboardGames(data?: Dashboard) {
  const season = data?.pools[0]?.season || data?.leagues[0]?.season || new Date().getFullYear();
  return useQuery({ queryKey: ["dashboard-games", season], queryFn: () => api<Game[]>(`/games?season=${season}`), enabled: Boolean(data), refetchInterval: 5 * 60_000 });
}

function applicationServerKey(value: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - value.length % 4) % 4);
  const raw = atob((value + padding).replaceAll("-", "+").replaceAll("_", "/"));
  return Uint8Array.from(raw, (character) => character.charCodeAt(0));
}

async function enablePush(publicKey: string): Promise<void> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) throw new Error("Push is not supported by this browser.");
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("Notification permission was not granted.");
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: applicationServerKey(publicKey),
  });
  const serialized = subscription.toJSON();
  await post("/notifications/subscribe", {
    endpoint: subscription.endpoint,
    keys: { auth: serialized.keys?.auth || "", p256dh: serialized.keys?.p256dh || "" },
  });
}

const nav = [
  ["/", "Command center", Home],
  ["/leagues", "Leagues", Users],
  ["/draft", "Draft room", DraftingCompass],
  ["/pools", "Pools", Trophy],
  ["/news", "News wire", Newspaper],
  ["/injuries", "Injury report", HeartPulse],
  ["/analysis", "Analyst desk", Bot],
  ["/settings", "Settings", Settings],
] as const;

function Loading({ label = "Loading" }: { label?: string }) {
  return <div className="loading"><span />{label}</div>;
}

function Empty({ title, body }: { title: string; body: string }) {
  return <div className="empty"><ClipboardList size={28} /><h3>{title}</h3><p>{body}</p></div>;
}

function ErrorPanel({ error }: { error: unknown }) {
  return <div className="error-panel">{error instanceof Error ? error.message : "Something went wrong."}</div>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}

function DashboardTicker() {
  const dashboard = useDashboardData();
  const schedule = useDashboardGames(dashboard.data);
  const weekState = dashboardWeek(schedule.data || []);
  const firstGame = weekState.games[0];
  const secondGame = weekState.games[1];
  const injuryAlerts = uniqueAlertsByTitle(dashboard.data?.alerts || []).filter((alert) => !alert.read && /injur|practice|questionable|doubtful|\bout\b/i.test(`${alert.title} ${alert.message}`)).length;
  const latestSync = dashboard.data?.snapshots[0]?.retrieved_at;
  return <div className="dashboard-tape" aria-label="NFL week and data status">
    <span>NFL week <strong>{weekState.week ? String(weekState.week).padStart(2, "0") : "—"}</strong></span>
    {firstGame ? <span>{firstGame.away_team} @ {firstGame.home_team} <strong>{gameLeader(firstGame)}</strong></span> : <span>Schedule <strong>Loading</strong></span>}
    {secondGame && <span>{secondGame.away_team} @ {secondGame.home_team} <strong>{secondGame.total ? `O/U ${secondGame.total.toFixed(1)}` : gameLeader(secondGame)}</strong></span>}
    <span className={injuryAlerts ? "warning" : ""}>Injury wire <strong>{injuryAlerts} new</strong></span>
    <span>Sync <strong>{latestSync ? `${clockTime(latestSync)} CT` : "Pending"}</strong></span>
  </div>;
}

function AppShell({ children, draftSuiteEnabled }: { children: ReactNode; draftSuiteEnabled: boolean }) {
  const location = useLocation();
  const isDashboard = location.pathname === "/";
  const availableNav = nav.filter(([to]) => draftSuiteEnabled || to !== "/draft");
  const primaryNav = availableNav.slice(0, 4);
  const secondaryNav = availableNav.slice(4);
  const secondaryActive = secondaryNav.some(([to]) => location.pathname === to || location.pathname.startsWith(`${to}/`));
  const workstationDate = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric" }).format(new Date());
  return <div className={`shell ${isDashboard ? "dashboard-shell" : ""}`}>
    {isDashboard && <DashboardTicker />}
    <aside>
      <Link to="/" className="brand"><span className="brand-mark">OG</span><span>Open Gridiron<small>Decision intelligence</small></span></Link>
      <nav className="desktop-nav" aria-label="Primary navigation">{availableNav.map(([to, label, Icon]) => <NavLink key={to} to={to} end={to === "/"}><Icon size={18} />{label}</NavLink>)}</nav>
      <nav className="mobile-nav" aria-label="Primary navigation">
        {primaryNav.map(([to, label, Icon]) => <NavLink key={to} to={to} end={to === "/"}><Icon size={19} /><span>{label === "Command center" ? "Home" : label === "Draft room" ? "Draft" : label}</span></NavLink>)}
        <details className={secondaryActive ? "more-active" : ""}>
          <summary aria-label="More sections"><Menu size={19} /><span>More</span></summary>
          <div className="mobile-nav-more-menu">
            {secondaryNav.map(([to, label, Icon]) => <NavLink key={to} to={to} onClick={(event) => event.currentTarget.closest("details")?.removeAttribute("open")}><Icon size={18} /><span>{label}</span></NavLink>)}
          </div>
        </details>
      </nav>
      <div className="sidebar-note"><ShieldCheck size={18} /><span>Read-only posture<small>Owner control retained</small></span></div>
    </aside>
    <main>
      {!isDashboard && <div className="command-tape" aria-label="Workstation status"><span>Open Gridiron <strong>Decision intelligence</strong></span><span>Mode <strong>Private workstation</strong></span><span>Authority <strong>Read-only</strong></span><time>{workstationDate}</time></div>}
      <div className={`main-workspace ${isDashboard ? "command-center-workspace" : ""}`}>{children}</div>
    </main>
  </div>;
}

function OnboardingPage({ status }: { status: Onboarding }) {
  const queryClient = useQueryClient();
  const [username, setUsername] = useState("owner");
  const [password, setPassword] = useState("");
  const mutation = useMutation({ mutationFn: () => post("/onboarding/setup", { username, password }), onSuccess: () => queryClient.invalidateQueries() });
  return <div className="auth-screen"><section className="auth-card">
    <span className="eyebrow">Welcome to Open Gridiron</span>
    <h1>Your season, under control.</h1>
    <p>Create the one owner account for this {status.environment} instance. Yahoo can be connected later; manual setup works immediately.</p>
    <form onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}>
      <Field label="Owner username"><input value={username} onChange={(event) => setUsername(event.target.value)} required minLength={3} /></Field>
      <Field label="Password (12+ characters)"><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required minLength={12} /></Field>
      {mutation.error && <ErrorPanel error={mutation.error} />}
      <button className="primary" disabled={mutation.isPending}>{mutation.isPending ? "Setting up…" : "Create owner"}</button>
    </form>
  </section></div>;
}

function LoginPage() {
  const [username, setUsername] = useState("owner");
  const [password, setPassword] = useState("");
  const queryClient = useQueryClient();
  const mutation = useMutation({ mutationFn: () => post("/auth/login", { username, password }), onSuccess: () => queryClient.invalidateQueries() });
  return <div className="auth-screen"><section className="auth-card"><span className="eyebrow">Private instance</span><h1>Welcome back.</h1><p>Sign in to view your leagues, pool entries, and analysis.</p>
    <form onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}>
      <Field label="Username"><input value={username} onChange={(event) => setUsername(event.target.value)} /></Field>
      <Field label="Password"><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></Field>
      {mutation.error && <ErrorPanel error={mutation.error} />}
      <button className="primary">Sign in</button>
    </form>
  </section></div>;
}

function PageHeader({ eyebrow, title, actions }: { eyebrow: string; title: string; actions?: ReactNode }) {
  return <header className="page-header"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1></div>{actions}</header>;
}

function PriorityWireAlert({ alert }: { alert: Alert }) {
  const content = <>
    <time dateTime={alert.created_at}><span>{clockTime(alert.created_at)}</span><small>{relativeAge(alert.created_at)} ago</small></time>
    <div><strong><PlayerMentions text={alert.title} href={alert.url} /></strong><p><PlayerMentions text={alert.message} /></p></div>
    <span className="command-alert-tag">{alert.url ? <a href={alert.url} target="_blank" rel="noopener noreferrer" aria-label={`Open article: ${alert.title}`}>{alert.severity}<ExternalLink size={12} aria-hidden="true" /></a> : alert.severity}</span>
  </>;
  const className = `command-alert ${alert.severity}`;

  return <article className={className}>{content}</article>;
}

function DashboardPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useDashboardData();
  const schedule = useDashboardGames(data);
  const primaryLeague = data?.leagues[0];
  const draftRoomPath = data?.active_draft
    ? `/draft/${data.active_draft.league_id}?session=${data.active_draft.id}`
    : primaryLeague ? `/draft/${primaryLeague.id}` : "/draft";
  const market = useQuery({ queryKey: ["dashboard-market", primaryLeague?.id], queryFn: () => api<MarketRecommendation[]>(`/leagues/${primaryLeague!.id}/waivers`), enabled: Boolean(primaryLeague), refetchInterval: 5 * 60_000 });
  const refreshSources = useMutation({
    mutationFn: refreshCommandCenterSources,
    onSettled: () => Promise.all([
      queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      queryClient.invalidateQueries({ queryKey: ["dashboard-games"] }),
      queryClient.invalidateQueries({ queryKey: ["dashboard-market"] }),
      queryClient.invalidateQueries({ queryKey: ["leagues"] }),
      queryClient.invalidateQueries({ queryKey: ["league"] }),
      queryClient.invalidateQueries({ queryKey: ["players"] }),
      queryClient.invalidateQueries({ queryKey: ["news"] }),
      queryClient.invalidateQueries({ queryKey: ["sources"] }),
      queryClient.invalidateQueries({ queryKey: ["football-data-sources"] }),
    ]),
  });
  if (isLoading) return <Loading label="Building your command center" />;
  if (error || !data) return <ErrorPanel error={error} />;
  const unread = data.alerts.filter((alert) => !alert.read).length;
  const dashboardAlerts = uniqueAlertsByTitle(data.alerts).slice(0, 3);
  const weekState = dashboardWeek(schedule.data || []);
  const poolEntries = data.pools.reduce((total, pool) => total + pool.entry_count, 0);
  const sourceByName = new Map<string, DashboardSnapshot>();
  data.snapshots.forEach((snapshot) => { if (!sourceByName.has(snapshot.source)) sourceByName.set(snapshot.source, snapshot); });
  const sourceRows = [...sourceByName.values()].slice(0, 3).map((snapshot) => ({
    key: `snapshot-${snapshot.source}`,
    label: formatSourceLabel(snapshot.source),
    detail: snapshot.source.includes("yahoo") ? "Roster / market" : snapshot.source.includes("draft") ? "Draft model" : "Data feed",
    status: snapshot.status,
    retrievedAt: snapshot.retrieved_at,
  }));
  const latestNewsFetch = (data.news_sources || []).find((source) => source.last_fetched_at);
  if (sourceRows.length < 3 && latestNewsFetch?.last_fetched_at) sourceRows.push({ key: "official-wire", label: latestNewsFetch.name, detail: "Official wire", status: "fresh", retrievedAt: latestNewsFetch.last_fetched_at });
  else if (sourceRows.length < 3 && dashboardAlerts[0]) sourceRows.push({ key: "official-wire", label: "NFL News", detail: "Official wire", status: "fresh", retrievedAt: dashboardAlerts[0].created_at });
  sourceRows.sort((left, right) => {
    const priority = (label: string) => label.includes("Yahoo") ? 0 : label.includes("NFLverse") ? 1 : 2;
    return priority(left.label) - priority(right.label);
  });
  const sourcesOnline = sourceRows.filter((source) => source.status !== "failed" && source.status !== "error").length;
  const nextKickoffHours = weekState.nextGame ? Math.max(0, Math.ceil((apiTimestamp(weekState.nextGame.kickoff).getTime() - Date.now()) / 3_600_000)) : undefined;
  const marketRows = market.data?.slice(0, 4) || [];
  const currentDay = new Intl.DateTimeFormat("en-US", { weekday: "long", timeZone: "America/Chicago" }).format(new Date());
  const currentTime = new Intl.DateTimeFormat("en-US", { hour: "2-digit", minute: "2-digit", timeZone: "America/Chicago" }).format(new Date());

  return <div className="command-center">
    <header className="command-center-header">
      <div><h1>Command center</h1><p>{currentDay} · Week {weekState.week || "—"} · {currentTime} CT</p></div>
      <div className="command-center-action-stack">
        <div className="command-center-actions"><button type="button" className="button ghost" aria-label="Refresh sources" disabled={refreshSources.isPending} onClick={() => refreshSources.mutate()} title="Refresh Yahoo, nflverse, news, Sleeper player status, and draft ADP"><RefreshCw className={refreshSources.isPending ? "spin" : undefined} size={15} /> {refreshSources.isPending ? "Refreshing…" : "Refresh sources"}</button><Link className="button primary" to="/analysis"><Bot size={16} /> Ask analyst</Link></div>
        {(refreshSources.isPending || refreshSources.data) && <span className={`command-refresh-status ${refreshSources.data?.status || "pending"}`} role="status" aria-live="polite" title={refreshSources.data?.details.join("\n")}>{refreshSources.isPending ? "Syncing Yahoo, NFLverse, and news sources…" : refreshSources.data?.message}</span>}
      </div>
    </header>

    <section className="command-center-metrics" aria-label="Current decision metrics">
      <div><small>Active leagues</small><strong>{String(data.leagues.length).padStart(2, "0")}</strong></div>
      <div><small>Pool entries</small><strong>{String(poolEntries).padStart(2, "0")}</strong></div>
      <div className="attention"><small>Decision alerts</small><strong>{String(unread).padStart(2, "0")}</strong></div>
      <div><small>Sources online</small><strong>{String(sourcesOnline).padStart(2, "0")}<i>/ {String(sourceRows.length).padStart(2, "0")}</i></strong></div>
      <div><small>Next kickoff</small><strong>{nextKickoffHours === undefined ? "—" : nextKickoffHours}<i>{nextKickoffHours === 1 ? "hour" : "hours"}</i></strong></div>
    </section>


    <div className="command-center-grid">
      <div className="command-center-wire">
        <section className="command-section">
          <header className="command-section-head"><div><span className="eyebrow">Priority wire</span><h2>What changed</h2></div><Link to="/news">Open all alerts</Link></header>
          {dashboardAlerts.length ? dashboardAlerts.map((alert) => <PriorityWireAlert key={alert.id} alert={alert} />) : <Empty title="No urgent alerts" body="Sources will appear here after the first news sync." />}
        </section>

        <section className="command-section command-market">
          <header className="command-section-head"><div><span className="eyebrow">Player market</span><h2>Top available value</h2></div><Link to={draftRoomPath}>Open draft room</Link></header>
          <div className="command-market-head"><span>RK</span><span>Player</span><span>POS</span><span>Value</span><span>Confidence</span></div>
          {marketRows.length ? marketRows.map((item) => { const player = marketSubject(item.subject); return <div className="command-market-row" key={`${item.rank}-${item.subject}`}><span>{String(item.rank).padStart(2, "0")}</span><span><PlayerDetailsButton player={{ id: item.player_id, name: player.name, league_id: primaryLeague?.id, position: player.position === "—" ? undefined : player.position, pro_team: player.team === "—" ? undefined : player.team }} /><small>{player.team}</small></span><span>{player.position}</span><span>{item.expected_value.toFixed(1)}</span><span className="delta">{Math.round(item.confidence * 100)}%</span></div>; }) : <div className="command-market-empty">{market.isLoading ? "Ranking available players…" : "No available-player market yet."}</div>}
        </section>
      </div>

      <aside className="command-center-intel">
        <section className="command-side-block">
          <header className="command-section-head"><div><span className="eyebrow">Data health</span><h2>Source status</h2></div></header>
          {sourceRows.length ? sourceRows.map((source) => <div className="command-source-row" key={source.key}><span className={`status ${source.status}`} /><span><strong>{source.label}</strong><small>{source.detail}</small></span><time dateTime={source.retrievedAt}>{relativeAge(source.retrievedAt)}</time></div>) : <div className="command-market-empty">No synchronized sources yet.</div>}
        </section>
        <section className="command-side-block">
          <header className="command-section-head"><div><span className="eyebrow">Game pulse</span><h2>Week {weekState.week || "—"} board</h2></div></header>
          {weekState.games.length ? weekState.games.map((game) => <div className="command-game" key={game.id}><div className="command-matchup"><strong>{game.away_team}</strong><time dateTime={game.kickoff}>{new Intl.DateTimeFormat("en-US", { weekday: "short", hour: "numeric", minute: "2-digit", timeZone: "America/Chicago" }).format(apiTimestamp(game.kickoff))}</time><strong>{game.home_team}</strong></div><div className="command-market-line"><span>{gameLeader(game)}</span><span>{game.spread_home === undefined ? "Line —" : `${game.home_team} ${game.spread_home > 0 ? "+" : ""}${game.spread_home.toFixed(1)}`}</span><span>{game.total === undefined ? "O/U —" : `O/U ${game.total.toFixed(1)}`}</span></div></div>) : <div className="command-market-empty">{schedule.isLoading ? "Loading the current NFL slate…" : "No games loaded for this week."}</div>}
        </section>
      </aside>
    </div>
  </div>;
}

function LeaguesPage() {
  const queryClient = useQueryClient();
  const { data = [], isLoading } = useQuery({ queryKey: ["leagues"], queryFn: () => api<League[]>("/leagues") });
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [season, setSeason] = useState(new Date().getFullYear());
  const mutation = useMutation({ mutationFn: () => post<League>("/leagues", { name, season, scoring: {}, roster_slots: ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN", "BN", "BN", "BN", "BN"] }), onSuccess: () => { setShowForm(false); setName(""); queryClient.invalidateQueries({ queryKey: ["leagues"] }); } });
  return <><PageHeader eyebrow="Fantasy football" title="Leagues" actions={<button className="primary" onClick={() => setShowForm(!showForm)}>Add manual league</button>} />
    {showForm && <form className="inline-form panel" onSubmit={(e) => { e.preventDefault(); mutation.mutate(); }}><Field label="League name"><input value={name} onChange={(e) => setName(e.target.value)} required /></Field><Field label="Season"><input type="number" value={season} onChange={(e) => setSeason(Number(e.target.value))} /></Field><button className="primary">Save league</button></form>}
    {isLoading ? <Loading /> : data.length ? <div className="list-grid">{data.map((league) => <Link to={`/leagues/${league.id}`} className="league-card" key={league.id}><div className="league-icon">{league.name.slice(0, 2).toUpperCase()}</div><div><small>{formatSourceLabel(league.source)} · {league.season}</small><h2>{league.name}</h2><p>{league.player_count} players · {league.roster_slots.filter((slot) => !["BN", "IR"].includes(slot)).length} starters</p></div><ChevronRight /></Link>)}</div> : <Empty title="No leagues yet" body="Create one manually now, then connect Yahoo after API approval." />}
  </>;
}


type SourceRefresh = { status: "idle" | "pending" | "success" | "error"; message?: string };
type FootballDataSource = { key: string; name: string; url: string; status: string; row_count: number; received_at: string | null; source_date?: string; usage: string; last_error?: string; refresh_status?: string };

function NewsPage() {
  const queryClient = useQueryClient();
  const { data: sources = [] } = useQuery({ queryKey: ["sources"], queryFn: () => api<any[]>("/news/sources") });
  const { data: dataSources = [] } = useQuery({ queryKey: ["football-data-sources"], queryFn: () => api<FootballDataSource[]>("/data-sources") });
  const { data: items = [] } = useQuery({ queryKey: ["news"], queryFn: () => api<NewsFeedItem[]>("/news/items") });
  const [refreshes, setRefreshes] = useState<Record<string, SourceRefresh>>({});
  const [newsQuery, setNewsQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [visibleCount, setVisibleCount] = useState(20);
  const categories = useMemo(() => [...new Set(items.map((item) => item.category))].sort(), [items]);
  const filteredItems = useMemo(() => filterNewsItems(items, newsQuery, category), [category, items, newsQuery]);
  const visibleItems = filteredItems.slice(0, visibleCount);

  async function refreshSource(id: number): Promise<void> {
    setRefreshes((current) => ({ ...current, [id]: { status: "pending" } }));
    try {
      const result = await post<{ created?: number; status?: string }>(`/news/sources/${id}/fetch`);
      setRefreshes((current) => ({
        ...current,
        [id]: { status: "success", message: `Fetched ${result.created ?? 0} new items.` },
      }));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["news"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ]);
    } catch (error) {
      setRefreshes((current) => ({
        ...current,
        [id]: {
          status: "error",
          message: error instanceof Error ? error.message : "Source refresh failed.",
        },
      }));
    }
  }

  async function refreshDataSource(key: string): Promise<void> {
    const id = `data-${key}`;
    setRefreshes((current) => ({ ...current, [id]: { status: "pending" } }));
    try {
      const result = await post<FootballDataSource>(`/data-sources/${key}/fetch`);
      const available = result.status === "available";
      setRefreshes((current) => ({ ...current, [id]: {
        status: available ? "success" : "error",
        message: result.refresh_status === "running" ? "A refresh is already running. Fetch again shortly."
          : available ? `${result.row_count} records available. Refreshes at most daily.`
          : result.last_error || `${result.status}. Check the source date before using it.`,
      } }));
      await queryClient.invalidateQueries({ queryKey: ["football-data-sources"] });
    } catch (error) {
      setRefreshes((current) => ({ ...current, [id]: { status: "error", message: error instanceof Error ? error.message : "Source refresh failed." } }));
    }
  }

  const refreshing = Object.values(refreshes).some((state) => state.status === "pending");
  return <>
    <PageHeader eyebrow="Football sources" title="News wire" actions={<button className="button ghost" disabled={refreshing} onClick={() => void Promise.all([...sources.filter((source) => source.enabled).map((source) => refreshSource(source.id)), ...dataSources.map((source) => refreshDataSource(source.key))])}>{refreshing ? "Refreshing…" : "Refresh all"}</button>} />
    <div className="dashboard-grid">
      <section className="panel span-2 news-panel">
        <div className="news-toolbar" role="search">
          <label><span>Search stories</span><input type="search" value={newsQuery} placeholder="Player, team, or headline" onChange={(event) => { setNewsQuery(event.target.value); setVisibleCount(20); }} /></label>
          <label><span>Category</span><select value={category} onChange={(event) => { setCategory(event.target.value); setVisibleCount(20); }}><option value="all">All categories</option>{categories.map((value) => <option value={value} key={value}>{value}</option>)}</select></label>
        </div>
        <div className="news-results" aria-live="polite"><strong>{filteredItems.length}</strong> {filteredItems.length === 1 ? "story" : "stories"}{filteredItems.length > visibleItems.length ? ` · showing ${visibleItems.length}` : ""}</div>
        <div className="news-feed">{visibleItems.length ? visibleItems.map((item) => <article key={item.id}><span className={`tag ${item.severity}`}>{item.category}</span><div><h2><PlayerMentions text={item.title} href={item.canonical_url} /></h2><p><PlayerMentions text={item.excerpt} /></p><a href={item.canonical_url} target="_blank" rel="noopener noreferrer">Read article<ExternalLink size={12} aria-hidden="true" /></a><small>{item.published_at ? new Date(item.published_at).toLocaleString() : "Recently retrieved"}</small></div></article>) : <Empty title={items.length ? "No matching stories" : "No stories yet"} body={items.length ? "Try a broader search or another category." : "Refresh an official source to populate the attributed feed."} />}</div>
        {filteredItems.length > visibleItems.length && <button className="button ghost news-more" onClick={() => setVisibleCount((count) => count + 20)}>Show 20 more</button>}
      </section>
      <section className="panel source-registry"><div className="panel-title"><h2>Source registry</h2></div>{sources.map((source) => { const state = refreshes[source.id] || { status: "idle" }; return <div className="source-row" key={source.id}><span className={`status ${state.status === "error" ? "error" : "fresh"}`} /><div><strong>{source.name}</strong><small>{formatSourceLabel(source.source_type)} · {source.official ? "official" : "publisher"}</small>{state.message && <small className={`source-message ${state.status}`}>{state.message}</small>}</div><button disabled={state.status === "pending" || !source.enabled} onClick={() => void refreshSource(source.id)}>{state.status === "pending" ? "Fetching…" : "Fetch"}</button></div>; })}
        <div className="panel-title"><h2>Player and draft data</h2></div>
        {dataSources.map((source) => { const state = refreshes[`data-${source.key}`] || { status: "idle" }; return <div className="source-row" key={source.key}><span className={`status ${state.status === "error" || source.status !== "available" ? "error" : "fresh"}`} /><div><strong><a href={source.url} target="_blank" rel="noopener noreferrer">{source.name}</a></strong><small>{source.status} · {source.row_count} records</small><small>{source.usage}</small><small>{source.received_at ? `Retrieved ${new Date(source.received_at).toLocaleString()}` : "Not fetched yet"}{source.source_date ? ` · Sample through ${source.source_date}` : ""}</small>{state.message && <small className={`source-message ${state.status}`}>{state.message}</small>}</div><button disabled={state.status === "pending"} onClick={() => void refreshDataSource(source.key)}>{state.status === "pending" ? "Fetching…" : "Fetch"}</button></div>; })}
        <p className="muted">Draft analysis fetches the supported scoring format and team count for its room. ADP describes draft demand; it is not a points projection. Sleeper status supplements official game-day reports.</p>
      </section>
    </div>
  </>;
}

function SettingsPage() {
  const queryClient = useQueryClient();
  const { data: status } = useQuery({ queryKey: ["onboarding"], queryFn: () => api<Onboarding>("/onboarding/status") });
  const { data: providers = [] } = useQuery({ queryKey: ["providers"], queryFn: () => api<Provider[]>("/providers") });
  const [provider, setProvider] = useState<ProviderDraft>({ name: "OpenAI", provider_type: "openai", model: "", base_url: "", api_key: "", task_defaults: ["chat", "recommendation"] });
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [manualModelEntry, setManualModelEntry] = useState(false);
  const [copiedCode, setCopiedCode] = useState(false);
  const [confirmingProviderId, setConfirmingProviderId] = useState<number | null>(null);
  const [providerHealth, setProviderHealth] = useState<Record<number, { pending?: boolean; ok?: boolean; error?: string }>>({});
  async function checkProvider(id: number): Promise<void> {
    setProviderHealth((current) => ({ ...current, [id]: { pending: true } }));
    try {
      const result = await post<{ ok: boolean; error?: string }>(`/providers/${id}/health`);
      setProviderHealth((current) => ({ ...current, [id]: result }));
    } catch (error) {
      setProviderHealth((current) => ({
        ...current,
        [id]: { ok: false, error: error instanceof Error ? error.message : "Health check failed." },
      }));
    }
  }
  const modelDiscovery = useMutation({
    mutationFn: () => post<{ models: string[] }>("/providers/models", { provider_type: provider.provider_type, api_key: provider.api_key || null }),
    onSuccess: (result) => {
      setModelOptions(result.models);
      setManualModelEntry(false);
      setProvider((current) => ({ ...current, model: result.models.includes(current.model) ? current.model : result.models[0] }));
    },
  });
  const codexAuth = useQuery({
    queryKey: ["codex-auth"],
    queryFn: () => api<CodexAuthStatus>("/providers/codex/auth"),
    enabled: provider.provider_type === "codex" && !provider.base_url,
    refetchInterval: (query) => ["starting", "pending"].includes(query.state.data?.status || "") ? 1500 : false,
  });
  const startCodexAuth = useMutation({
    mutationFn: () => post<CodexAuthStatus>("/providers/codex/auth"),
    onSuccess: (result) => queryClient.setQueryData(["codex-auth"], result),
  });
  const addProvider = useMutation({
    mutationFn: () => post("/providers", { ...provider, base_url: provider.base_url || null, api_key: provider.api_key || null, enabled: true }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["providers"] });
      setProvider((current) => ({ ...current, api_key: "" }));
    },
  });
  const removeProvider = useMutation({
    mutationFn: (providerId: number) => remove(`/providers/${providerId}`),
    onSuccess: (_result, providerId) => {
      setConfirmingProviderId(null);
      setProviderHealth((current) => {
        const next = { ...current };
        delete next[providerId];
        return next;
      });
      queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
  });
  const [yahoo, setYahoo] = useState({ client_id: "", client_secret: "", redirect_uri: `${window.location.origin}/api/v1/integrations/yahoo/callback` });
  const saveYahoo = useMutation({ mutationFn: () => post("/integrations/yahoo/settings", yahoo) });
  const connectYahoo = useMutation({ mutationFn: () => api<{ authorization_url: string }>("/integrations/yahoo/start"), onSuccess: (result) => { window.location.assign(result.authorization_url); } });
  const syncYahoo = useMutation({ mutationFn: () => post<any>("/integrations/yahoo/sync"), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["leagues"] }); queryClient.invalidateQueries({ queryKey: ["dashboard"] }); } });
  const yahooScraperStatus = useQuery({ queryKey: ["yahoo-scraper-status"], queryFn: () => api<YahooScraperStatus>("/integrations/yahoo/scraper/status") });
  const [yahooScraper, setYahooScraper] = useState({ leagueUrls: "", cookie: "" });
  const saveYahooScraper = useMutation({
    mutationFn: () => post<YahooScraperStatus>("/integrations/yahoo/scraper/settings", {
      league_urls: yahooScraper.leagueUrls.split("\n").map((value) => value.trim()).filter(Boolean),
      cookie: yahooScraper.cookie.trim() || null,
    }),
    onSuccess: (result) => {
      queryClient.setQueryData(["yahoo-scraper-status"], result);
      setYahooScraper((current) => ({ ...current, cookie: "" }));
    },
  });
  const syncYahooScraper = useMutation({
    mutationFn: () => post<any>("/integrations/yahoo/scraper/sync"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leagues"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
  const backup = useMutation({ mutationFn: () => post<any>("/backups") });
  const { data: pushConfig } = useQuery({ queryKey: ["push-config"], queryFn: () => api<{ enabled: boolean; vapid_public_key?: string }>("/notifications/config") });
  const push = useMutation({ mutationFn: async () => { if (!pushConfig?.vapid_public_key) throw new Error("Configure VAPID keys on the server first."); await enablePush(pushConfig.vapid_public_key); return post("/notifications/test"); } });
  const currentCodexAuth = codexAuth.data || startCodexAuth.data;
  const supportsModelDiscovery = supportsProviderModelDiscovery(provider.provider_type, provider.base_url);
  const modelControl = providerModelControl(provider.provider_type, manualModelEntry, provider.base_url);
  const modelPlaceholder = providerModelPlaceholder({
    loading: modelDiscovery.isPending,
    hasCredential: provider.provider_type === "codex"
      ? Boolean(currentCodexAuth?.authenticated || provider.api_key.trim())
      : Boolean(provider.api_key.trim()),
    optionCount: modelOptions.length,
    providerType: provider.provider_type,
  });
  const loadEnteredKeyModels = (): void => {
    if (supportsModelDiscovery && provider.api_key.trim() && !modelDiscovery.isPending && modelOptions.length === 0) {
      modelDiscovery.mutate();
    }
  };
  useEffect(() => {
    if (
      provider.provider_type === "codex"
      && !provider.base_url
      && currentCodexAuth?.authenticated
      && !manualModelEntry
      && modelOptions.length === 0
      && !modelDiscovery.isPending
      && !modelDiscovery.isError
    ) {
      modelDiscovery.mutate();
    }
  }, [
    currentCodexAuth?.authenticated,
    manualModelEntry,
    modelDiscovery,
    modelOptions.length,
    provider.base_url,
    provider.provider_type,
  ]);
  useEffect(() => {
    if (yahooScraperStatus.data?.league_urls.length && !yahooScraper.leagueUrls) {
      setYahooScraper((current) => ({ ...current, leagueUrls: yahooScraperStatus.data?.league_urls.join("\n") || "" }));
    }
  }, [yahooScraper.leagueUrls, yahooScraperStatus.data?.league_urls]);
  return <>
    <PageHeader eyebrow={`${status?.environment || "local"} deployment`} title="Settings" />
    <div className="settings-grid">
      <section className="panel provider-panel">
        <div className="panel-title"><div><span className="eyebrow">Models & credentials</span><h2>AI providers</h2></div></div>
        {providers.map((p) => {
          const health = providerHealth[p.id];
          const credential = p.provider_type === "codex" ? "runner login" : p.has_api_key ? "key stored" : "no key";
          const confirmingRemoval = confirmingProviderId === p.id;
          const removing = removeProvider.isPending && removeProvider.variables === p.id;
          return <div className="provider-row" key={p.id}>
            <span className={`status ${health?.ok ? "fresh" : health?.ok === false ? "error" : ""}`} />
            <div><strong>{p.name}</strong><small>{p.provider_type} · {p.model} · {credential}</small><small>{p.enabled ? "Enabled" : "Disabled"} · Defaults: {p.task_defaults.join(", ") || "none"}</small>{health && !health.pending && <small className={`source-message ${health.ok ? "success" : "error"}`}>{health.ok ? "Connected · model available" : health.error || "Provider unavailable"}</small>}<ProviderEditor provider={p} /></div>
            <div className="provider-actions">
              {confirmingRemoval ? <>
                <button type="button" className="ghost" disabled={removing} onClick={() => { setConfirmingProviderId(null); removeProvider.reset(); }}>Cancel</button>
                <button type="button" className="danger confirm" disabled={removing} onClick={() => removeProvider.mutate(p.id)}><Trash2 size={14} />{removing ? "Removing…" : `Confirm remove ${p.name}`}</button>
              </> : <>
                <button type="button" disabled={health?.pending || removeProvider.isPending} onClick={() => void checkProvider(p.id)}>{health?.pending ? "Checking…" : "Check"}</button>
                <button type="button" className="danger" disabled={removeProvider.isPending} onClick={() => { setConfirmingProviderId(p.id); removeProvider.reset(); }}><Trash2 size={14} />Remove</button>
              </>}
            </div>
          </div>;
        })}
        {removeProvider.error && <ErrorPanel error={removeProvider.error} />}
        <form className="provider-form" onSubmit={(event) => { event.preventDefault(); addProvider.mutate(); }}>
          <h3>Add provider</h3>
          <ProviderTasks value={provider.task_defaults} onChange={(task_defaults) => setProvider({ ...provider, task_defaults })} />
          <Field label="Provider">
            <select value={provider.provider_type} onChange={(event) => {
              const providerType = event.target.value;
              setProvider((current) => ({ ...current, provider_type: providerType, api_key: "", ...PROVIDER_DEFAULTS[providerType] }));
              setModelOptions([]);
              setManualModelEntry(false);
              modelDiscovery.reset();
            }}>
              <option value="openai">OpenAI Responses</option>
              <option value="anthropic">Anthropic Messages</option>
              <option value="openai_compatible">Ollama / compatible</option>
              <option value="codex">Codex CLI sidecar</option>
            </select>
          </Field>
          <Field label="Display name"><input value={provider.name} onChange={(event) => setProvider({ ...provider, name: event.target.value })} required /></Field>
          {provider.provider_type === "codex" && <Field label="Local provider (optional)"><select value={provider.base_url} onChange={(event) => {
            setProvider({ ...provider, base_url: event.target.value, model: "" });
            setModelOptions([]);
            setManualModelEntry(false);
            modelDiscovery.reset();
          }}><option value="">OpenAI / ChatGPT</option><option value="ollama">Ollama</option><option value="lmstudio">LM Studio</option></select></Field>}
          {provider.provider_type === "openai_compatible" && <Field label="Base URL"><input value={provider.base_url} onChange={(event) => setProvider({ ...provider, base_url: event.target.value })} placeholder="http://host.docker.internal:11434/v1" required /></Field>}
          <Field label={provider.provider_type === "codex" ? "API key (optional alternative)" : "API key"}><input type="password" value={provider.api_key} onChange={(event) => {
            setProvider((current) => ({ ...current, api_key: event.target.value }));
            if (supportsModelDiscovery) {
              setModelOptions([]);
              modelDiscovery.reset();
            }
          }} onBlur={loadEnteredKeyModels} disabled={supportsModelDiscovery && modelDiscovery.isPending} autoComplete="off" /></Field>
          {supportsModelDiscovery && <div className="model-discovery">
            <button type="button" className="ghost" disabled={modelDiscovery.isPending} onClick={() => modelDiscovery.mutate()}><RefreshCw size={15} />{modelDiscovery.isPending ? "Loading models…" : "Load available models"}</button>
            <small>{provider.provider_type === "codex" ? "Load models from the connected Codex CLI account or the optional key above." : "Models load after you enter a key; when blank, this checks the server key."}</small>
          </div>}
          <Field label="Model">
            {modelControl === "select" ? <select value={modelOptions.includes(provider.model) ? provider.model : ""} onChange={(event) => setProvider({ ...provider, model: event.target.value })} disabled={modelDiscovery.isPending} required><option value="" disabled>{modelPlaceholder}</option>{modelOptions.map((model) => <option key={model} value={model}>{model}</option>)}</select> : <input value={provider.model} onChange={(event) => setProvider({ ...provider, model: event.target.value })} placeholder="Enter a model ID" required />}
          </Field>
          {supportsModelDiscovery && <button type="button" className="text-button" onClick={() => {
            if (manualModelEntry) setProvider((draft) => ({ ...draft, model: modelOptions.includes(draft.model) ? draft.model : modelOptions[0] || "" }));
            setManualModelEntry(!manualModelEntry);
          }}>{manualModelEntry ? modelOptions.length > 0 ? `Choose from ${modelOptions.length} available models` : "Use provider model list" : "Enter a model ID manually"}</button>}
          {modelDiscovery.error && <ErrorPanel error={modelDiscovery.error} />}
          {provider.provider_type === "codex" && !provider.base_url && <div className={`codex-auth-card ${currentCodexAuth?.authenticated ? "connected" : ""}`}>
            <div className="codex-auth-heading"><KeyRound size={19} /><div><strong>{currentCodexAuth?.authenticated ? "Codex CLI connected" : "Connect Codex CLI"}</strong><small>{currentCodexAuth?.message || (codexAuth.isLoading ? "Checking the private runner…" : "Sign in with ChatGPT using a one-time device code.")}</small></div></div>
            {!currentCodexAuth?.authenticated && !["starting", "pending"].includes(currentCodexAuth?.status || "") && <button type="button" className="primary" disabled={startCodexAuth.isPending} onClick={() => startCodexAuth.mutate()}>{startCodexAuth.isPending ? "Starting login…" : "Sign in with ChatGPT"}</button>}
            {currentCodexAuth?.user_code && <div className="device-code"><span>One-time code</span><strong>{currentCodexAuth.user_code}</strong><button type="button" aria-label="Copy one-time code" onClick={() => { void navigator.clipboard.writeText(currentCodexAuth.user_code || ""); setCopiedCode(true); }}><Copy size={15} />{copiedCode ? "Copied" : "Copy"}</button></div>}
            {currentCodexAuth?.verification_url && <a className="button ghost" href={currentCodexAuth.verification_url} target="_blank" rel="noreferrer">Open ChatGPT sign-in <ExternalLink size={15} /></a>}
            {startCodexAuth.error && <ErrorPanel error={startCodexAuth.error} />}
            {codexAuth.error && !startCodexAuth.error && <ErrorPanel error={codexAuth.error} />}
          </div>}
          <button className="primary" disabled={addProvider.isPending}>{addProvider.isPending ? "Adding…" : "Add provider"}</button>
          {addProvider.error && <ErrorPanel error={addProvider.error} />}
          {addProvider.isSuccess && <p className="success">Provider added.</p>}
        </form>
      </section>
      <section className="panel"><div className="panel-title"><div><span className="eyebrow">Works without an API key</span><h2>Yahoo authenticated scraper</h2></div><span className={`status ${yahooScraperStatus.data?.configured ? "fresh" : ""}`} /></div><p className="muted">Use an existing Yahoo session to import league settings, rosters, ownership, and drafts. The cookie stays encrypted and never returns to the browser.</p><form onSubmit={(event) => { event.preventDefault(); saveYahooScraper.mutate(); }}><Field label="League URLs (one per line)"><textarea rows={3} value={yahooScraper.leagueUrls} onChange={(event) => setYahooScraper({ ...yahooScraper, leagueUrls: event.target.value })} placeholder="https://football.fantasysports.yahoo.com/f1/123456" required /></Field><Field label={`Yahoo Cookie header${yahooScraperStatus.data?.has_cookie ? " (leave blank to keep saved session)" : ""}`}><textarea rows={4} value={yahooScraper.cookie} onChange={(event) => setYahooScraper({ ...yahooScraper, cookie: event.target.value })} placeholder="A1=…; A3=…; GUC=…" autoComplete="off" /></Field><p className="muted">In Yahoo, copy the page request’s <strong>Cookie</strong> header from browser developer tools. Treat it as a password and replace it after Yahoo signs out.</p><div className="action-row"><button className="primary" disabled={saveYahooScraper.isPending}>{saveYahooScraper.isPending ? "Saving…" : "Save encrypted session"}</button><button type="button" disabled={!yahooScraperStatus.data?.configured || syncYahooScraper.isPending} onClick={() => syncYahooScraper.mutate()}><RefreshCw size={15} />{syncYahooScraper.isPending ? "Scraping and modeling…" : "Scrape now"}</button></div>{saveYahooScraper.isSuccess && <p className="success">Yahoo scraper session saved.</p>}{syncYahooScraper.data && <p className="success">Synchronized {syncYahooScraper.data.players} players and modeled {syncYahooScraper.data.ranges_modeled || 0} NFLverse ranges ({syncYahooScraper.data.nflverse_matched || 0} exact player matches).{syncYahooScraper.data.partial ? " The player pool reached the safety page limit." : ""}</p>}{(saveYahooScraper.error || syncYahooScraper.error || yahooScraperStatus.error) && <ErrorPanel error={saveYahooScraper.error || syncYahooScraper.error || yahooScraperStatus.error} />}</form></section>
      <section className="panel"><div className="panel-title"><h2>Yahoo API access (optional)</h2></div><p className="muted">Keep this path for later, after Yahoo approves a client. No Yahoo writes are performed.</p><form onSubmit={(e) => { e.preventDefault(); saveYahoo.mutate(); }}><Field label="Client ID"><input value={yahoo.client_id} onChange={(e) => setYahoo({ ...yahoo, client_id: e.target.value })} required /></Field><Field label="Client secret"><input type="password" value={yahoo.client_secret} onChange={(e) => setYahoo({ ...yahoo, client_secret: e.target.value })} required /></Field><Field label="Redirect URI"><input value={yahoo.redirect_uri} onChange={(e) => setYahoo({ ...yahoo, redirect_uri: e.target.value })} required /></Field><div className="action-row"><button className="primary" disabled={saveYahoo.isPending}>Store settings</button><button type="button" onClick={() => connectYahoo.mutate()}>Authorize Yahoo</button><button type="button" onClick={() => syncYahoo.mutate()}>Sync API now</button><a className="button ghost" target="_blank" rel="noreferrer" href="https://sports.yahoo.com/developer/access/">Access application</a></div>{saveYahoo.isSuccess && !connectYahoo.error && !syncYahoo.error && <p className="success">Yahoo API settings encrypted.</p>}{syncYahoo.data && <p className="success">Synchronized {syncYahoo.data.players} player records.</p>}{(saveYahoo.error || connectYahoo.error || syncYahoo.error) && <ErrorPanel error={saveYahoo.error || connectYahoo.error || syncYahoo.error} />}</form></section>
      <section className="panel"><div className="panel-title"><h2>Backups</h2></div><p className="muted">Create a consistent SQLite snapshot now. Scheduled backups retain the latest seven copies.</p><button className="primary" onClick={() => backup.mutate()}>Create backup</button>{backup.data && <p className="success">Created {backup.data.filename}</p>}</section>
      <section className="panel"><div className="panel-title"><h2>Browser alerts</h2><span className={`status ${pushConfig?.enabled ? "fresh" : ""}`} /></div><p className="muted">Receive new or materially changed injury and transaction alerts while this server is online.</p><button className="primary" disabled={!pushConfig?.enabled || push.isPending} onClick={() => push.mutate()}>{push.isPending ? "Enabling…" : "Enable on this device"}</button>{push.error && <ErrorPanel error={push.error} />}{push.isSuccess && <p className="success">Subscribed and test sent.</p>}</section>
    </div>
  </>;
}

function ProtectedApp({ draftSuiteEnabled }: { draftSuiteEnabled: boolean }) {
  const health = useQuery({ queryKey: ["health"], queryFn: () => api("/system/health"), retry: false });
  if (health.isLoading) return <Loading label="Opening your private instance" />;
  if (health.error instanceof ApiError && health.error.status === 401) return <LoginPage />;
  return <PlayerDetailsProvider><AppShell draftSuiteEnabled={draftSuiteEnabled}><Suspense fallback={<Loading label="Loading workspace" />}><Routes><Route path="/" element={<DashboardPage />} /><Route path="/leagues" element={<LeaguesPage />} /><Route path="/leagues/:leagueId" element={<LeaguePage draftSuiteEnabled={draftSuiteEnabled} />} />{draftSuiteEnabled ? <><Route path="/draft" element={<DraftRoute />} /><Route path="/draft/:leagueId" element={<DraftRoute />} /></> : <Route path="/draft/*" element={<Navigate to="/" replace />} />}<Route path="/pools" element={<PoolsOverviewPage />} /><Route path="/pools/:poolId/weeks/:week" element={<PoolWeekPage />} /><Route path="/news" element={<NewsPage />} /><Route path="/injuries" element={<InjuryReportPage />} /><Route path="/analysis" element={<AnalysisWorkspace />} /><Route path="/settings" element={<SettingsPage />} /></Routes></Suspense></AppShell></PlayerDetailsProvider>;
}

export default function App() {
  const { data, isLoading, error } = useQuery({ queryKey: ["onboarding"], queryFn: () => api<Onboarding>("/onboarding/status"), retry: false });
  if (isLoading) return <Loading label="Starting Open Gridiron" />;
  if (error || !data) return <ErrorPanel error={error} />;
  if (!data.configured) return <OnboardingPage status={data} />;
  return <ProtectedApp draftSuiteEnabled={data.capabilities.draft_suite} />;
}
