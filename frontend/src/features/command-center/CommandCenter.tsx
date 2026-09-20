import { useMemo, useState, type ReactNode } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, ExternalLink } from "lucide-react";
import { Link } from "react-router-dom";
import { api, post } from "../../api";
import type { Game, League, PoolOverview } from "../../types";
import { formatSourceLabel, uniqueAlertsByTitle } from "../../ui-display-state";
import type { InjuryBoard, InjuryRow } from "../injuries/types";
import type { WeeklyLineup } from "../leagues/league-display";
import { slotLabel } from "../leagues/league-display";
import { PlayerMentionActions } from "../leagues/PlayerDetails";
import { createPlayerMatcher } from "../leagues/player-links";
import { gameForTeam, gamePhase, injuryPriority, isStarter, lineupSummary, needsInjuryReview, ownMemberships, poolEntryState, pregameEstimate, sourceState, teamCode, timestamp } from "./state";
import "./command-center.css";

type Alert = { id: number; title: string; message: string; severity: string; url?: string; read: boolean; created_at: string };
type Snapshot = { id: number; source: string; source_id?: string; retrieved_at: string; status: string };
type NewsSource = { id: number; name: string; enabled: boolean; last_fetched_at?: string };
type Briefing = { id: number; root_id: number; title: string; preview: string; status: string; updated_at: string; context: { league_name?: string; pool_name?: string; team_name?: string; week?: number } };
type Props = {
  leagues: League[]; alerts: Alert[]; snapshots: Snapshot[]; newsSources: NewsSource[];
  games: Game[]; season: number; week?: number; nextGame?: Game;
  scheduleLoading: boolean; scheduleError: Error | null; retrySchedule: () => void;
};
type Lineup = WeeklyLineup & { data_as_of: string; stale_reasons?: string[] };
const age = (value?: string) => {
  if (!value || !Number.isFinite(timestamp(value))) return "time unavailable";
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp(value)) / 60000));
  return minutes < 1 ? "just now" : minutes < 60 ? `${minutes}m ago` : minutes < 1440 ? `${Math.floor(minutes / 60)}h ago` : `${Math.floor(minutes / 1440)}d ago`;
};
const kickoff = (value: string) => new Intl.DateTimeFormat("en-US", { weekday: "short", hour: "numeric", minute: "2-digit", timeZone: "America/Chicago" }).format(new Date(timestamp(value)));
const sourceLabel = (value: string) => ({ "nflverse.schedule": "NFL schedule", "espn.scoreboard": "ESPN scores", "nflverse.rosters": "NFL rosters" }[value] || formatSourceLabel(value));
const leagueHref = (league: League, week?: number) => `/leagues/${league.id}?${new URLSearchParams({ ...(league.my_team_name ? { team: league.my_team_name } : {}), ...(week ? { week: String(week) } : {}) })}`;

function SectionHead({ id, title, detail, href, action }: { id?: string; title: string; detail?: string; href?: string; action?: string }) {
  return <header className="command-section-head"><div><h2 id={id}>{title}</h2>{detail && <p>{detail}</p>}</div>{href && <Link to={href}>{action || "View all"}<ArrowUpRight size={14} aria-hidden="true" /></Link>}</header>;
}
function Note({ children, retry }: { children: ReactNode; retry?: () => void }) {
  return <div className={`command-note ${retry ? "warning" : ""}`}><p>{children}</p>{retry && <button className="ghost" type="button" onClick={retry}>Try again</button>}</div>;
}

function GameRow({ game, players, now }: { game: Game; players: string[]; now: number }) {
  const phase = gamePhase(game, now);
  const hasScore = game.away_score != null && game.home_score != null;
  const status = phase === "final" ? hasScore ? "Final" : "Final · score pending" : phase === "started" ? hasScore ? "Latest score" : "Score pending" : "Upcoming";
  return <article className={`command-game ${phase}`}>
    <div className="command-game-top"><span className="command-game-status">{status}</span><time dateTime={game.kickoff}>{kickoff(game.kickoff)}</time></div>
    <div className="command-matchup"><strong>{game.away_team}</strong>{hasScore ? <span className="command-game-score" aria-label={`${game.away_team} ${game.away_score}, ${game.home_team} ${game.home_score}`}>{game.away_score} – {game.home_score}</span> : <span className="command-game-at">at</span>}<strong>{game.home_team}</strong></div>
    {players.length > 0 && <p className="command-game-owned">On your rosters · {players.join(", ")}</p>}
    {phase === "upcoming" ? <div className="command-market-line"><span>Pregame · {pregameEstimate(game)}</span><span>{game.spread_home == null ? "Line —" : `${game.home_team} ${game.spread_home > 0 ? "+" : ""}${game.spread_home.toFixed(1)}`} · {game.total == null ? "O/U —" : `O/U ${game.total.toFixed(1)}`}</span></div> : null}
    {phase === "upcoming" && <small className="command-game-provenance">{game.win_probability_kind === "market" ? "Market estimate" : game.win_probability_kind === "manual" ? "Manual estimate" : "Model / source estimate"} · {sourceLabel(game.source)} · {age(game.source_timestamp)}</small>}
  </article>;
}

function InjuryItem({ row, games, now }: { row: InjuryRow; games: Game[]; now: number }) {
  const memberships = ownMemberships(row);
  const game = gameForTeam(games, row.team);
  const started = game && timestamp(game.kickoff) <= now;
  const received = row.official_reports[0]?.retrieved_at || row.supplemental[0]?.retrieved_at;
  return <article className="command-injury-row">
    <div><Link className="command-player-name" to={`/injuries?mine=true&player=${encodeURIComponent(row.key)}`}>{row.name}<ArrowUpRight size={13} aria-hidden="true" /></Link><span className="command-caption">{row.position} · {row.team}{row.injury && row.injury !== "Not supplied" ? ` · ${row.injury}` : ""}</span>
      <div className="command-memberships">{memberships.map((member) => <Link key={member.player_id} to={`/leagues/${member.league_id}`}>{member.league_name} · {slotLabel(member.slot)}</Link>)}</div>
    </div>
    <div className="command-injury-state"><strong className={started ? "" : isStarter(memberships.find((member) => isStarter(member.slot))?.slot) ? "urgent" : "warning"}>{row.game_status}</strong><span>{row.status_source} · {received ? age(received) : "update time unavailable"}</span><span>{game ? `${started ? "Game started · " : ""}${kickoff(game.kickoff)} CT` : "No game in this week’s schedule"}</span></div>
  </article>;
}

export function CommandCenterContent({ leagues, alerts, snapshots, newsSources, games, season, week, nextGame, scheduleLoading, scheduleError, retrySchedule }: Props) {
  const queryClient = useQueryClient();
  const now = Date.now();
  const [showAllNews, setShowAllNews] = useState(false);
  const liveScores = useQuery({
    queryKey: ["command-live-scores", season, week],
    queryFn: async () => {
      await post(`/sync/live-scores?season=${season}&week=${week}`);
      // Read the newly saved scores without refreshing unrelated roster/news sources.
      await queryClient.invalidateQueries({ queryKey: ["dashboard-games", season] }, { throwOnError: true });
      return { checkedAt: new Date().toISOString() };
    },
    enabled: Boolean(week),
    staleTime: 0,
    refetchOnMount: "always",
    refetchOnWindowFocus: "always",
    refetchOnReconnect: "always",
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    retry: false,
  });
  const activeLeagues = leagues.filter((league) => league.season === season);
  const ownerLeagues = activeLeagues.filter((league) => league.my_team_name);
  const injuries = useQuery({ queryKey: ["command-injuries", season], queryFn: async ({ signal }) => {
    const result = await api<InjuryBoard>("/injuries?mine=true", { signal });
    if (!Array.isArray(result.items)) throw new Error("Injury report unavailable");
    return result;
  }, enabled: ownerLeagues.length > 0, staleTime: 60_000, refetchInterval: 300_000, retry: false });
  const pools = useQuery({ queryKey: ["pool-overview"], queryFn: async ({ signal }) => {
    const result = await api<PoolOverview>("/pools/overview", { signal });
    if (!Array.isArray(result.pools)) throw new Error("Pool overview unavailable");
    return result;
  }, staleTime: 60_000, refetchInterval: 60_000, retry: false });
  const lineups = useQueries({ queries: ownerLeagues.map((league) => ({
    queryKey: ["weekly-lineup", String(league.id), "balanced", league.my_team_name, String(week)],
    queryFn: async ({ signal }: { signal: AbortSignal }) => {
      const result = await api<Lineup>(`/leagues/${league.id}/weekly-lineup?${new URLSearchParams({ mode: "balanced", team_name: league.my_team_name!, week: String(week) })}`, { signal });
      if (!Array.isArray(result.forecasts) || !Array.isArray(result.assignments) || result.week !== week || result.season !== season) throw new Error("Weekly lineup context unavailable");
      return result;
    }, enabled: Boolean(week), staleTime: 300_000, refetchInterval: 300_000, retry: false,
  })) });
  const briefings = useQuery({ queryKey: ["command-briefings", week], queryFn: async ({ signal }) => {
    const result = await api<{ items: Briefing[] }>(`/analysis/library?limit=3${week ? `&week=${week}` : ""}`, { signal });
    if (!Array.isArray(result.items)) throw new Error("Saved analysis unavailable");
    return result.items;
  }, staleTime: 60_000, retry: false });
  const ownerPlayers = lineups.flatMap((query, index) => (query.data?.forecasts || []).map((player) => ({ name: player.name, team: player.team, id: player.player_id, league_id: ownerLeagues[index].id, league_name: ownerLeagues[index].name })));
  const knownOwners = [...ownerPlayers, ...(injuries.data?.items || []).flatMap((row) => ownMemberships(row).map((member) => ({ name: row.name, team: row.team, id: member.player_id, league_id: member.league_id, league_name: member.league_name })))];
  const matchOwners = useMemo(() => createPlayerMatcher(knownOwners.map((player) => ({ name: player.name, id: player.id, league_id: player.league_id }))), [lineups.map((query) => query.dataUpdatedAt).join(","), injuries.dataUpdatedAt, ownerLeagues.map((league) => `${league.id}:${league.my_team_name}`).join(",")]);
  const news = uniqueAlertsByTitle(alerts).map((alert) => {
    const mentions = matchOwners(`${alert.title} ${alert.message}`).filter((part) => part.player).map((part) => part.text.toLowerCase());
    const affected = [...new Set(knownOwners.filter((player) => mentions.includes(player.name.toLowerCase())).map((player) => player.league_name))];
    return { ...alert, affected };
  }).sort((a, b) => Number(b.affected.length > 0) - Number(a.affected.length > 0) || ({ critical: 0, urgent: 0, high: 1, warning: 2, medium: 2 }[a.severity] ?? 3) - ({ critical: 0, urgent: 0, high: 1, warning: 2, medium: 2 }[b.severity] ?? 3) || timestamp(b.created_at) - timestamp(a.created_at));
  const watch = (injuries.data?.items || []).filter((row) => ownMemberships(row).length && needsInjuryReview(row)).sort((a, b) => injuryPriority(a, games, now) - injuryPriority(b, games, now) || a.name.localeCompare(b.name));
  const entries = (pools.data?.pools || []).filter((pool) => pool.season === season).flatMap((pool) => pool.entries.filter((entry) => entry.active).map((entry) => ({ pool, entry, state: poolEntryState(entry) }))).sort((a, b) => Number(b.state.needsReview) - Number(a.state.needsReview));
  const missingEntries = entries.filter(({ state }) => state.needsReview).length;
  const nextInjury = watch.find((row) => {
    const game = gameForTeam(games, row.team);
    return game && timestamp(game.kickoff) > now && ownMemberships(row).some((member) => isStarter(member.slot));
  });
  const nextEntry = entries.find(({ state }) => state.needsReview);
  const nextReview = nextInjury ? {
    href: `/injuries?mine=true&player=${encodeURIComponent(nextInjury.key)}`,
    title: `${nextInjury.name} · ${nextInjury.game_status}`,
    detail: `${ownMemberships(nextInjury).filter((member) => isStarter(member.slot)).map((member) => `${member.league_name} · ${slotLabel(member.slot)}`).join(" / ")} · Kickoff ${kickoff(gameForTeam(games, nextInjury.team)!.kickoff)} CT`,
    action: "Review injury report",
  } : nextEntry ? {
    href: `/pools/${nextEntry.pool.id}/weeks/${nextEntry.pool.suggested_week}?entry_id=${nextEntry.entry.id}`,
    title: `${nextEntry.pool.name} · ${nextEntry.state.text}`,
    detail: `${nextEntry.entry.name} · Week ${nextEntry.pool.suggested_week} · Picks saved here`,
    action: "Review pool entry",
  } : null;
  const latestSources = [...new Map(snapshots.slice().sort((a, b) => timestamp(a.retrieved_at) - timestamp(b.retrieved_at)).map((snapshot) => [snapshot.source, snapshot])).values()].sort((a, b) => Number(["failed", "error"].includes(b.status)) - Number(["failed", "error"].includes(a.status)) || timestamp(b.retrieved_at) - timestamp(a.retrieved_at));
  const scoreboard = snapshots.find((snapshot) => snapshot.source === "espn.scoreboard" && snapshot.source_id === `${season}:${week}`);
  const scoreCheckedAt = [scoreboard?.retrieved_at, liveScores.data?.checkedAt].filter((value): value is string => Boolean(value)).sort((a, b) => timestamp(b) - timestamp(a))[0];
  const groups = [
    { phase: "upcoming", title: "Upcoming" }, { phase: "started", title: "Started · latest available scores" }, { phase: "final", title: "Final results" },
  ];
  const injuryMetric = !ownerLeagues.length ? "—" : injuries.isPending ? "…" : injuries.error ? "—" : String(watch.length).padStart(2, "0");

  return <>
    <section className="command-center-metrics" aria-label="Current decision metrics">
      <a href="#command-reminders"><small>My leagues</small><strong>{String(activeLeagues.length).padStart(2, "0")}</strong><span>Lineup & roster review</span></a>
      <a href="#command-injuries" className={watch.length ? "attention" : ""}><small>Roster injury watch</small><strong>{injuryMetric}</strong><span>{injuries.error ? "Report unavailable" : "Reported designations"}</span></a>
      <a href="#command-pools" className={missingEntries ? "attention" : ""}><small>Entries to review</small><strong>{pools.isPending ? "…" : pools.error ? "—" : String(missingEntries).padStart(2, "0")}</strong><span>Picks saved here</span></a>
      <a href="#command-news"><small>Recent headlines</small><strong>{String(news.length).padStart(2, "0")}</strong><span>Latest news batch</span></a>
      <a href="#command-games"><small>Next kickoff · CT</small><strong className="command-next-time">{nextGame ? kickoff(nextGame.kickoff) : "—"}</strong><span>{nextGame ? `${nextGame.away_team} @ ${nextGame.home_team}` : "No upcoming game loaded"}</span></a>
    </section>
    {nextReview && <Link className="command-next-review" to={nextReview.href}><span className="command-next-label">Next to review</span><span><strong>{nextReview.title}</strong><small>{nextReview.detail}</small></span><span className="command-next-action">{nextReview.action}<ArrowUpRight size={16} aria-hidden="true" /></span></Link>}
    <div className="command-center-grid">
      <div className="command-center-wire">
        <section id="command-reminders" className="command-section" aria-labelledby="command-reminders-title">
          <SectionHead id="command-reminders-title" title="Your week, at a glance" detail={`Week ${week || "—"} · Lineups, reminders and saved picks`} href="/leagues" action="Leagues" />
          {!activeLeagues.length && <Note><Link to="/leagues">Add a league</Link> to see your roster and lineup reminders.</Note>}
          {activeLeagues.map((league) => {
            const query = lineups[ownerLeagues.findIndex((owner) => owner.id === league.id)];
            const lineup = query?.data;
            const summary = lineup ? lineupSummary(lineup) : null;
            const affected = watch.filter((row) => ownMemberships(row).some((member) => member.league_id === league.id && isStarter(member.slot)));
            return <article className="command-league-row" key={league.id}>
              <div><Link className="command-row-title" to={leagueHref(league, week)}>{league.name}<ArrowUpRight size={14} aria-hidden="true" /></Link><span className="command-caption">{league.my_team_name || "Choose My team in this league"}</span>{lineup && <span className="command-caption">OG weekly model · {age(lineup.data_as_of)}</span>}</div>
              <div className="command-league-summary"><strong className={summary?.tone}>{!league.my_team_name ? "Set your team" : !week ? "Waiting for the weekly schedule" : query?.error ? "Lineup unavailable" : !lineup ? "Loading lineup review…" : summary?.text}</strong>
                {lineup && !lineup.error && !lineup.partial_total && lineup.projected_gain != null && <span>{lineup.projected_total?.toFixed(1)} modeled pts{summary?.changes ? ` · ${lineup.projected_gain >= 0 ? "+" : ""}${lineup.projected_gain.toFixed(1)} vs current` : ""}</span>}
                {affected.length > 0 && <span className="command-league-watch">{affected.length} {affected.length === 1 ? "starter" : "starters"} on injury watch</span>}
                {lineup?.error && <span>{lineup.error}</span>}
                {query?.error && <button type="button" className="ghost" onClick={() => void query.refetch()}>Retry lineup</button>}
              </div>
            </article>;
          })}
          <div id="command-pools" className="command-pool-heading"><h3>Pool reminders</h3><Link to="/pools">Open pools<ArrowUpRight size={14} aria-hidden="true" /></Link></div>
          {pools.error ? <Note retry={() => void pools.refetch()}>Pool readiness is unavailable. Check entries before their lock times.</Note> : pools.isPending ? <Note>Loading saved pool entries…</Note> : !entries.length ? <Note><Link to="/pools">Open pools</Link> to add or review an entry.</Note> : entries.map(({ pool, entry, state }) => <Link key={entry.id} className="command-pool-row" to={`/pools/${pool.id}/weeks/${pool.suggested_week}?entry_id=${entry.id}`}><span><strong>{pool.name}</strong><small>{entry.name} · Week {pool.suggested_week}</small></span><span className={state.tone}>{state.text}<ArrowUpRight size={14} aria-hidden="true" /></span></Link>)}
          {entries.length > 0 && <p className="command-footnote">Local pick status · Confirm actual submissions with your pool provider.</p>}
        </section>
        <section id="command-injuries" className="command-section" aria-labelledby="command-injuries-title">
          <SectionHead id="command-injuries-title" title="On your injury watch" detail="Reported status · Upcoming starters first" href="/injuries?mine=true" action="Full report" />
          {!ownerLeagues.length ? <Note>Set <Link to="/leagues">My team</Link> in each league to identify your players.</Note> : injuries.error ? <Note retry={() => void injuries.refetch()}>The injury report could not load. Availability has not been verified.</Note> : injuries.isPending ? <Note>Checking your rosters against the injury report…</Note> : !watch.length ? <Note>No adverse designations in the available report. This does not confirm every player’s availability.</Note> : watch.slice(0, 6).map((row) => <InjuryItem key={row.key} row={row} games={games} now={now} />)}
          {watch.length > 6 && <Link className="command-show-more" to="/injuries?mine=true">Review all {watch.length} players</Link>}
          {injuries.data && <p className="command-footnote">{injuries.data.sources.map((source) => `${source.name}: ${source.status === "ok" ? "retrieved" : source.status} ${source.fetched_at ? age(source.fetched_at) : "· time unavailable"}`).join(" · ")}</p>}
        </section>
        <section id="command-news" className="command-section" aria-labelledby="command-news-title">
          <SectionHead id="command-news-title" title="What changed" detail="Your players first, then priority NFL news" href="/news" action="News wire" />
          {news.length ? news.slice(0, showAllNews ? news.length : 5).map((alert) => <article key={alert.id} className={`command-alert ${alert.severity}`}>
            <div className="command-news-meta"><time dateTime={alert.created_at}>{age(alert.created_at)}</time><span className="command-alert-tag">{alert.severity}</span>{alert.affected.length > 0 && <span className="command-news-relevance">On your rosters · {alert.affected.join(" / ")}</span>}</div>
            <h3>{alert.url ? <a href={alert.url} target="_blank" rel="noopener noreferrer">{alert.title}</a> : alert.title}</h3><p>{alert.message}</p>
            <div className="command-news-actions"><PlayerMentionActions text={`${alert.title} ${alert.message}`} />{alert.url && <a className="command-article-link" href={alert.url} target="_blank" rel="noopener noreferrer" aria-label={`Open article: ${alert.title}`}>Read article<ExternalLink size={13} aria-hidden="true" /></a>}</div>
          </article>) : <Note>No recent headlines loaded. Refresh sources to check the news wire.</Note>}
          {news.length > 5 && <button className="command-show-more ghost" type="button" aria-expanded={showAllNews} onClick={() => setShowAllNews(!showAllNews)}>{showAllNews ? "Show priority headlines" : `Show all ${news.length} recent headlines`}</button>}
        </section>
        <section className="command-section" aria-labelledby="command-briefings-title"><SectionHead id="command-briefings-title" title="Recent analyst notes" detail="Saved conversations · Check their date and evidence" href="/analysis?view=history" action="History" />
          {briefings.error ? <Note retry={() => void briefings.refetch()}>Saved analysis could not load.</Note> : briefings.isPending ? <Note>Loading saved analysis…</Note> : !briefings.data?.length ? <Note>No saved analysis for this week. <Link to="/analysis">Ask the analyst</Link> about a decision.</Note> : briefings.data.map((briefing) => <Link key={briefing.id} className="command-briefing" to={`/analysis?parent_run_id=${briefing.root_id || briefing.id}`}><strong>{briefing.title}</strong><span>{[briefing.context.league_name || briefing.context.pool_name, briefing.context.team_name, briefing.context.week ? `Week ${briefing.context.week}` : null, briefing.status, age(briefing.updated_at)].filter(Boolean).join(" · ")}</span>{briefing.preview && <p>{briefing.preview}</p>}</Link>)}
        </section>
      </div>
      <aside className="command-center-intel" aria-label="NFL games and source context">
        <section id="command-games" className="command-side-block" aria-labelledby="command-games-title"><SectionHead id="command-games-title" title={`Week ${week || "—"} board`} detail="Game Pulse · All times Central" />
          {scheduleError ? <Note retry={retrySchedule}>The schedule could not load. Saved games may be out of date.</Note> : null}
          {games.length ? <>
            <p className="command-board-note">Scores refresh every 30 seconds while this tab is visible.</p><p className="command-score-check">{scoreCheckedAt ? `ESPN scoreboard checked ${age(scoreCheckedAt)}. Individual score times aren’t supplied.` : liveScores.isFetching ? "Checking ESPN scores…" : "Individual score update times aren’t supplied."}</p>
            {liveScores.isError && <Note retry={() => void liveScores.refetch()}>Score updates are unavailable. Showing saved scores; retrying every 30 seconds while this tab is visible.</Note>}
            {groups.map(({ phase, title }) => {
              const group = games.filter((game) => gamePhase(game, now) === phase);
              return group.length ? <div className="command-game-group" key={phase}><h3>{title}<span>{group.length}</span></h3>{group.map((game) => <GameRow key={game.id} game={game} now={now} players={[...new Set(ownerPlayers.filter((player) => [game.away_team, game.home_team].some((team) => teamCode(team) === teamCode(player.team))).map((player) => player.name))]} />)}</div> : null;
            })}
          </> : <Note>{scheduleLoading ? "Loading the current NFL slate…" : "No games loaded for this week."}</Note>}
        </section>
        <section className="command-side-block" aria-labelledby="command-sources-title"><SectionHead id="command-sources-title" title="Source status" detail="Recent checks & retrieval times" href="/news" action="Sources" />
          {[...latestSources.map((source) => ({ key: `snapshot-${source.source}`, name: sourceLabel(source.source), status: source.status, at: source.retrieved_at })), ...newsSources.filter((source) => source.enabled).map((source) => ({ key: `news-${source.id}`, name: source.name, status: "", at: source.last_fetched_at }))].map((source) => {
            const state = sourceState(source.status, source.at, now);
            return <div className="command-source-row" key={source.key}><span className={`status ${state.tone === "urgent" ? "error" : state.tone === "warning" ? "stale" : "fresh"}`} aria-hidden="true" /><span><strong>{source.name}</strong><small className={state.tone}>{state.label}</small></span><time dateTime={source.at}>{age(source.at)}</time></div>;
          })}
          {!latestSources.length && !newsSources.length && <Note>No source checks recorded. Refresh sources to collect data.</Note>}
        </section>
      </aside>
    </div>
  </>;
}
