import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, RefreshCw, X } from "lucide-react";
import { useLocation } from "react-router-dom";
import { api } from "../../api";
import type { Player } from "../../types";
import { normalizePosition, slotLabel, sourceTime } from "./league-display";
import { DetailTabs } from "./DetailTabs";
import { ProjectionDetails, type RankingEvidence } from "./ProjectionDetails";
import { createPlayerMatcher, isCompletePlayer, playerMatches, type PlayerReference } from "./player-links";
import "./league.css";
import "./player-details.css";

type Article = { title: string; url: string; excerpt: string; source: string; category: string; published_at: string | null; retrieved_at: string };
type ReportSource = { name: string; url: string; status: "ok" | "stale" | "unavailable"; checked_at: string; fetched_at: string | null; message: string | null };
type InjuryReport = { player_name: string; team: string; injury: string; practice_status: string; game_status: string; report_period: string; url: string; retrieved_at: string };
type Synopsis = { player: Player; synopsis: string; injury_reports: InjuryReport[]; articles: Article[]; sources: ReportSource[]; news_window_days: number };

const PLAYER_TABS = [{ id: "overview", label: "Overview" }, { id: "projections", label: "Projections" }, { id: "news", label: "News & sources" }] as const;
type PlayerTab = typeof PLAYER_TABS[number]["id"];
type PlayerSelection = { player: PlayerReference; initialTab: PlayerTab; ranking?: RankingEvidence };
const PlayerDetailsContext = createContext<(selection: PlayerSelection) => void>(() => {});
const PlayerDirectoryContext = createContext<PlayerReference[]>([]);
const PlayerLeagueContext = createContext<number | undefined>(undefined);
const PlayerMatcherContext = createContext(createPlayerMatcher([]));

export function PlayerDetailsButton({ player, children, initialTab = "overview", ranking, label, description }: {
  player: PlayerReference; children?: ReactNode; initialTab?: PlayerTab; ranking?: RankingEvidence; label?: string; description?: string;
}) {
  const open = useContext(PlayerDetailsContext);
  const leagueId = useContext(PlayerLeagueContext);
  const descriptionId = useId();
  return <button type="button" className="player-details-trigger" aria-haspopup="dialog" aria-label={label || `View ${player.name} synopsis`} aria-describedby={description ? descriptionId : undefined} onClick={(event) => { event.stopPropagation(); open({ player: { ...player, league_id: player.league_id ?? leagueId }, initialTab, ranking }); }}>{children ?? <strong>{player.name}</strong>}{description && <span className="sr-only" id={descriptionId}>{description}</span>}</button>;
}

export function PlayerMentions({ text, players, leagueId, href }: { text: string; players?: PlayerReference[]; leagueId?: number; href?: string }) {
  const matchDirectory = useContext(PlayerMatcherContext);
  const currentLeague = useContext(PlayerLeagueContext);
  const match = useMemo(() => players ? createPlayerMatcher(players) : matchDirectory, [players, matchDirectory]);
  const parts = match(text, leagueId ?? currentLeague);
  return <>{parts.map((part, index) => part.player
    ? <PlayerDetailsButton key={index} player={part.player}>{part.text}</PlayerDetailsButton>
    : href ? <a key={index} href={href} target="_blank" rel="noopener noreferrer">{part.text}</a> : part.text)}</>;
}

export function PlayerDetailsProvider({ children }: { children: ReactNode }) {
  const [selected, setSelected] = useState<PlayerSelection | null>(null);
  const location = useLocation();
  const routeLeague = location.pathname.match(/^\/(?:leagues|draft)\/(\d+)(?:\/|$)/)?.[1];
  const leagueId = routeLeague ? Number(routeLeague) : undefined;
  const directory = useQuery({ queryKey: ["players", "directory"], queryFn: ({ signal }) => api<PlayerReference[]>("/players/directory", { signal }), staleTime: 60_000 });
  const players = Array.isArray(directory.data) ? directory.data : [];
  const matcher = useMemo(() => createPlayerMatcher(players), [directory.data]);
  const close = useCallback(() => setSelected(null), []);
  useEffect(close, [location.pathname, close]);
  return <PlayerDetailsContext.Provider value={setSelected}><PlayerDirectoryContext.Provider value={players}><PlayerMatcherContext.Provider value={matcher}><PlayerLeagueContext.Provider value={leagueId}>{children}
    {selected && <PlayerDetails key={`${selected.player.id}-${selected.player.name}-${selected.player.league_id}`} initialPlayer={selected.player} initialTab={selected.initialTab} ranking={selected.ranking} onClose={close} directoryLoading={directory.isFetching} directoryError={directory.error} retryDirectory={() => void directory.refetch()} />}
  </PlayerLeagueContext.Provider></PlayerMatcherContext.Provider></PlayerDirectoryContext.Provider></PlayerDetailsContext.Provider>;
}

function SourceLink({ url, children }: { url: string; children: ReactNode }) {
  let safe = false;
  try { safe = ["https:", "http:"].includes(new URL(url).protocol); } catch { /* Render text for invalid links. */ }
  return safe ? <a href={url} target="_blank" rel="noopener noreferrer">{children}<ExternalLink size={14} aria-hidden="true" /><span className="sr-only"> (opens in a new tab)</span></a> : <span>{children}</span>;
}

function Articles({ items }: { items: Article[] }) {
  return <ul className="player-article-list">{items.map((item) => <li key={item.url}>
    <div className="player-report-meta"><span>{item.source}</span><span>{item.published_at ? <>Published {sourceTime(item.published_at)}</> : <>Publication date not supplied · Retrieved {sourceTime(item.retrieved_at)}</>}</span></div>
    <h3><SourceLink url={item.url}>{item.title}</SourceLink></h3>
    {item.excerpt && <p>{item.excerpt}</p>}
  </li>)}</ul>;
}

function PlayerDetails({ initialPlayer, initialTab, ranking, onClose, directoryLoading, directoryError, retryDirectory }: { initialPlayer: PlayerReference; initialTab: PlayerTab; ranking?: RankingEvidence; onClose: () => void; directoryLoading: boolean; directoryError: Error | null; retryDirectory: () => void }) {
  const [selectedTab, setSelectedTab] = useState<PlayerTab>(initialTab);
  const body = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const queryClient = useQueryClient();
  const directory = useContext(PlayerDirectoryContext);
  const matches = playerMatches(initialPlayer, directory);
  const [chosen, setChosen] = useState<PlayerReference>();
  const reference = initialPlayer.id != null ? initialPlayer : chosen || (matches.length === 1 ? matches[0] : initialPlayer);
  const playerId = reference.id;
  const stored = useQuery({ queryKey: ["player", playerId], queryFn: ({ signal }) => api<Player>(`/players/${playerId}`, { signal }), enabled: playerId != null && !isCompletePlayer(initialPlayer), retry: false });
  const queryKey = ["player-synopsis", playerId];
  const query = useQuery({ queryKey, queryFn: ({ signal }) => api<Synopsis>(`/players/${playerId}/synopsis`, { signal }), staleTime: 60_000, retry: false, enabled: playerId != null && selectedTab !== "projections" });
  const refresh = useMutation({
    mutationFn: () => api<Synopsis>(`/players/${playerId}/synopsis?refresh=true`),
    onSuccess: (data) => queryClient.setQueryData(queryKey, data),
  });
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    const element = dialog.current;
    element?.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      element?.close();
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    };
  }, [onClose]);
  const data = query.data;
  const fullPlayer = data?.player || stored.data || (isCompletePlayer(initialPlayer) ? initialPlayer : undefined);
  const player = fullPlayer || reference;
  const busy = query.isFetching || refresh.isPending;
  const error = query.error || refresh.error;
  const injuryArticles = data?.articles.filter((article) => article.category === "injury") || [];
  const otherArticles = data?.articles.filter((article) => article.category !== "injury") || [];
  const nflSource = data?.sources.find((source) => source.name === "NFL injury report");
  const newsSource = data?.sources.find((source) => source.name === "Google News");
  const partial = data?.sources.some((source) => source.status !== "ok");

  const reportState = <>
      <div className="player-refresh-row"><span className="player-report-meta">Injuries & player news</span><button type="button" className="ghost" disabled={busy || playerId == null} onClick={() => refresh.mutate()}><RefreshCw size={15} className={busy ? "spin" : undefined} />{busy ? "Checking reports…" : "Refresh reports"}</button></div>
      {busy && <p role="status">{data ? "Refreshing player reports…" : "Loading injury reports and recent articles…"}</p>}
      {error && <div role="alert" className="league-error"><strong>Could not load player reports</strong><p>{error instanceof Error ? error.message : "Please try again."}</p><button type="button" disabled={busy} onClick={() => { refresh.reset(); void query.refetch(); }}>Try again</button></div>}
      {partial && <p role="status" className="league-warning">Some sources are unavailable or out of date. Available reports are shown below.</p>}
  </>;
  const sourceSection = data && <>
        <section className="player-detail-section player-sources" aria-labelledby="player-sources-title"><h3 id="player-sources-title">Sources & freshness</h3>
          <p className="league-caption">Sources are cached for 10 minutes. Manual refresh checks at most once per minute. Article dates are publication dates; retrieval time shows when Open Gridiron checked a source.</p>
          {data.sources.map((source) => <div key={source.name}><SourceLink url={source.url}>{source.name}</SourceLink><span className={source.status !== "ok" ? "league-warning" : "league-positive"}>{source.status === "ok" ? "Available" : source.status === "stale" ? "Out of date" : "Unavailable"}</span>
            <p className="league-caption">Last attempted {sourceTime(source.checked_at)} · Last retrieved {sourceTime(source.fetched_at)}{source.message && <> · {source.message}</>}</p>
          </div>)}
        </section>
  </>;

  return <dialog ref={dialog} className="league-page player-dialog" aria-labelledby="player-details-title" onKeyDown={(event) => {
    // Keep draft shortcuts (especially Escape and /) from changing the underlying room.
    event.stopPropagation();
    if (event.key !== "Tab") return;
    const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button, a[href], [tabindex]'))
      .filter((element) => element.tabIndex >= 0 && !element.matches(':disabled') && element.getClientRects().length > 0);
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first && last) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last && first) {
      event.preventDefault(); first.focus();
    }
  }} onCancel={(event) => { event.preventDefault(); onClose(); }} onClick={(event) => {
    if (event.target !== event.currentTarget) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose();
  }}>
    <header className="player-dialog-header"><div><h2 id="player-details-title">{player.name}</h2><p className="player-report-meta">{normalizePosition(player.position || "")} · {player.pro_team || "Team unknown"} · Player synopsis</p></div>
      <button type="button" className="ghost player-dialog-close" aria-label="Close player synopsis" autoFocus onClick={onClose}><X size={20} /></button>
    </header>
    <DetailTabs id="player-detail" label="Player information" tabs={PLAYER_TABS} selected={selectedTab} onSelect={(value) => { setSelectedTab(value); body.current?.scrollTo({ top: 0 }); }} />
    <div ref={body} className="player-dialog-body">
      {playerId == null && <section aria-label="Player lookup">
        {directoryLoading ? <p role="status">Finding player details…</p> : directoryError ? <p role="alert">Could not look up this player. <button type="button" onClick={retryDirectory}>Retry player lookup</button></p> : matches.length > 1 ? <><p>Choose the league record to view its roster and projections.</p>{matches.map((match) => <div key={match.id}><button type="button" className="ghost" onClick={() => setChosen(match)}>{match.name} · {match.pro_team} · {match.position} · {match.league_name || `League ${match.league_id}`}</button></div>)}</> : <p role="status">No matching player record is available in this league. Sync or import this player to load their details.</p>}
      </section>}
      {stored.isFetching && !fullPlayer && <p role="status">Loading player information…</p>}
      {stored.error && !fullPlayer && <p role="alert">Could not load player information. <button type="button" onClick={() => void stored.refetch()}>Retry player information</button></p>}
      <div role="tabpanel" id="player-detail-panel-overview" aria-labelledby="player-detail-tab-overview" hidden={selectedTab !== "overview"} tabIndex={0}>
        {selectedTab === "overview" && <>
          <section className="player-detail-section" aria-labelledby="player-overview-title"><h3 id="player-overview-title">Player overview</h3>
            <p className="player-synopsis">{data?.synopsis || `${player.name} · ${normalizePosition(player.position || "")} · ${player.pro_team || "NFL team unknown"}. ${player.rostered_by ? `Rostered by ${player.rostered_by}.` : fullPlayer ? "Not on an imported fantasy roster." : "Roster information has not loaded yet."}`}</p>
            <dl className="player-facts">
              <div><dt>Fantasy team</dt><dd>{player.rostered_by || (fullPlayer ? "Not on an imported fantasy roster" : "Not loaded")}</dd></div>
              {reference.league_name && <div><dt>League</dt><dd>{reference.league_name}</dd></div>}
              <div><dt>Imported fantasy status</dt><dd className={player.status?.toLowerCase() !== "active" ? "league-warning" : ""}>{player.status || "Not supplied"}</dd></div>
              <div><dt>Roster slot</dt><dd>{slotLabel(player.current_slot)}</dd></div>
            </dl>
            <p className="league-caption">Status comes from your last roster import. Reported injuries and articles below may be newer.</p>
          </section>
          {reportState}
          {data && <>
        <section className="player-detail-section" aria-labelledby="player-injuries-title"><h3 id="player-injuries-title">Official injury report</h3>
          {nflSource?.status !== "ok" && <p className="league-warning">{nflSource?.status === "stale" ? "Previously retrieved report; current status could not be verified." : "The current NFL injury report is unavailable."}</p>}
          {data.injury_reports.length ? data.injury_reports.map((report, index) => <div className="player-injury-report" key={index}>
            <p className="player-report-meta">{report.report_period} · {report.team}</p>
            <dl className="player-facts"><div><dt>Injury</dt><dd>{report.injury}</dd></div><div><dt>Game status</dt><dd>{report.game_status}</dd></div><div><dt>Practice</dt><dd>{report.practice_status}</dd></div></dl>
            <p className="league-caption"><SourceLink url={report.url}>NFL injury report</SourceLink> · Retrieved {sourceTime(report.retrieved_at)}</p>
          </div>) : <p>{nflSource?.status === "ok" ? "No matching player entry found on the current NFL injury report. This does not confirm that the player is healthy or available." : "No verified injury entry is available. Check the source again before setting your lineup."}</p>}
        </section>
          </>}
          {sourceSection}
        </>}
      </div>
      <div role="tabpanel" id="player-detail-panel-projections" aria-labelledby="player-detail-tab-projections" hidden={selectedTab !== "projections"} tabIndex={0}>
        {selectedTab === "projections" && (fullPlayer ? <ProjectionDetails player={fullPlayer} ranking={ranking} /> : <p>Projections will appear when player information is available.</p>)}
      </div>
      <div role="tabpanel" id="player-detail-panel-news" aria-labelledby="player-detail-tab-news" hidden={selectedTab !== "news"} tabIndex={0}>
        {selectedTab === "news" && <>
          {reportState}
          {data && <>
        <section className="player-detail-section" aria-labelledby="player-injury-news-title"><h3 id="player-injury-news-title">Injury-related coverage</h3>
          <p className="league-caption">Articles mentioning this player and injury terms; a headline may also discuss teammates.</p>
          {injuryArticles.length ? <Articles items={injuryArticles} /> : <p>{newsSource?.status === "ok" ? `No matching injury articles found in the last ${data.news_window_days} days.` : "No injury articles available from the sources that could be checked."}</p>}
        </section>
        <section className="player-detail-section" aria-labelledby="player-news-title"><h3 id="player-news-title">Recent news & articles</h3><p className="league-caption">Latest matching coverage · past {data.news_window_days} days</p>
          {otherArticles.length ? <Articles items={otherArticles} /> : <p>{newsSource?.status === "ok" ? "No other recent articles matched this player." : "No other articles available from the sources that could be checked."}</p>}
        </section>
          </>}
          {sourceSection}
        </>}
      </div>
    </div>
  </dialog>;
}
