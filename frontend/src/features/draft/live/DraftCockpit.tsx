import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  BarChart3,
  Bot,
  Check,
  ChevronRight,
  Clock3,
  ListPlus,
  Layers3,
  Pause,
  Play,
  Radio,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  Undo2,
  X,
} from "lucide-react";

import { PlayerDetailsButton, PlayerMentions } from "../../leagues/PlayerDetails";
import { ApiError } from "../../../api";
import { draftApi } from "../api";
import { CandidateCard } from "../components/CandidateCard";
import type { DraftPick, DraftPlayer, DraftRouteLeague, DraftSession } from "../types";
import { syncYahooAtLatestSequence } from "./yahoo-sync-state";

type Props = {
  league: DraftRouteLeague;
  session: DraftSession;
  onSessionChange: (session: DraftSession) => void;
};

const POSITIONS = ["ALL", "QB", "RB", "WR", "TE", "K", "DEF"];
const ROSTER_POSITION_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"];
type YahooSourceMode = "yahoo_scrape_shadow" | "yahoo_oauth_shadow" | "yahoo_scrape_authoritative" | "yahoo_oauth_authoritative";

export function DraftCockpit({ league, session, onSessionChange }: Props) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [position, setPosition] = useState("ALL");
  const [notice, setNotice] = useState<string>();
  const [intentPlayerId, setIntentPlayerId] = useState<number>();
  const [correctionTarget, setCorrectionTarget] = useState<DraftPick>();
  const [confirmingAuthority, setConfirmingAuthority] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const rankingAttempted = useRef(new Set<number>());
  const automaticMock = session.kind === "mock" && session.opponent_mode === "automatic";
  const yahooApproval = session.source_mode.endsWith("_shadow");
  const yahooAutoApprove = session.source_mode.endsWith("_authoritative");
  const yahooTransport = session.source_mode.includes("oauth") ? "yahoo_oauth" : "yahoo_scrape";
  const yahooShadowMode = `${yahooTransport}_shadow` as YahooSourceMode;
  const yahooAuthoritativeMode = `${yahooTransport}_authoritative` as YahooSourceMode;

  const boardQuery = useQuery({
    queryKey: ["draft-board-v2", session.id],
    queryFn: () => draftApi.board(session.id),
    refetchInterval: session.status === "LIVE" ? 5_000 : false,
  });
  const recommendationQuery = useQuery({
    queryKey: ["draft-recommendations-v2", session.id],
    queryFn: () => draftApi.recommendations(session.id),
    enabled: ["LIVE", "PAUSED"].includes(session.status),
    refetchInterval: session.status === "LIVE" ? 5_000 : false,
  });
  const conflictsQuery = useQuery({
    queryKey: ["draft-conflicts", session.id],
    queryFn: () => draftApi.conflicts(session.id),
  });
  const exposureQuery = useQuery({
    queryKey: ["draft-exposure", session.id],
    queryFn: () => draftApi.exposure(session.id),
  });

  const refresh = async () => {
    const updated = await draftApi.session(session.id);
    onSessionChange(updated);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["draft-board-v2", session.id] }),
      queryClient.invalidateQueries({ queryKey: ["draft-recommendations-v2", session.id] }),
      queryClient.invalidateQueries({ queryKey: ["draft-replay", session.id] }),
      queryClient.invalidateQueries({ queryKey: ["draft-conflicts", session.id] }),
      queryClient.invalidateQueries({ queryKey: ["draft-exposure", session.id] }),
      queryClient.invalidateQueries({ queryKey: ["draft-sessions-v2", league.id] }),
    ]);
  };

  const recover = async (error: Error, playerId?: number) => {
    if (error instanceof ApiError && ["draft_conflict", "stale_recommendation", "advice_pending"].includes(error.code || "")) {
      setIntentPlayerId(playerId);
      setNotice(error.code === "advice_pending"
        ? "Advice was still updating. The room refreshed; confirm the same player again."
        : "The board changed. It has been refreshed; confirm the highlighted player again.");
      await refresh();
    }
  };

  const action = useMutation({
    mutationFn: (name: "pause" | "resume" | "complete") => draftApi.action(session.id, name, session.current_sequence),
    onSuccess: async (result) => { onSessionChange(result.session); setNotice(undefined); await refresh(); },
    onError: (error: Error) => void recover(error),
  });
  const record = useMutation({
    mutationFn: (playerId: number) => correctionTarget
      ? draftApi.replacePick(session.id, { expectedSequence: session.current_sequence, targetEventId: correctionTarget.event_id, playerId })
      : draftApi.recordPick(session.id, {
        expectedSequence: session.current_sequence,
        playerId,
        recommendationSnapshotId: recommendationQuery.data?.snapshot_id,
      }),
    onSuccess: async (result) => {
      onSessionChange(result.session);
      setNotice(correctionTarget ? "Correction recorded. The original pick remains in history." : "Pick recorded.");
      setIntentPlayerId(undefined);
      setCorrectionTarget(undefined);
      setSearch("");
      await refresh();
    },
    onError: (error: Error, playerId) => void recover(error, playerId),
  });
  const undo = useMutation({
    mutationFn: (pick: DraftPick) => draftApi.undoPick(session.id, session.current_sequence, pick.event_id),
    onSuccess: async (result) => { onSessionChange(result.session); setNotice("Latest pick undone. History is preserved."); await refresh(); },
    onError: (error: Error) => void recover(error),
  });
  const queue = useMutation({
    mutationFn: ({ playerId, remove = false }: { playerId: number; remove?: boolean }) => {
      const current = boardQuery.data?.queue.map((player) => player.id) || [];
      const next = remove ? current.filter((id) => id !== playerId) : [...new Set([...current, playerId])];
      return draftApi.saveQueue(
        session.id,
        boardQuery.data?.preference_revision || 0,
        next.map((id, index) => ({ player_id: id, queue_rank: index + 1 })),
      );
    },
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["draft-board-v2", session.id] }); },
    onError: (error: Error) => void recover(error),
  });
  const sourceMode = useMutation({
    mutationFn: ({ mode, ownerConfirmed = false }: { mode: "manual" | YahooSourceMode; ownerConfirmed?: boolean }) =>
      draftApi.sourceMode(session.id, session.current_sequence, mode, ownerConfirmed),
    onSuccess: async (updated) => {
      onSessionChange(updated);
      setConfirmingAuthority(false);
      setNotice(updated.source_mode === "manual"
        ? "Yahoo observation disabled."
        : updated.source_mode.endsWith("_authoritative")
          ? "Yahoo auto-approve enabled for safe sequential picks. Conflicts still require approval."
          : "Yahoo approval mode enabled. Every new or conflicting pick requires your approval.");
      await refresh();
    },
    onError: (error: Error) => void recover(error),
  });
  const yahooSync = useMutation({
    mutationFn: () => syncYahooAtLatestSequence({
      loadSession: () => draftApi.session(session.id),
      sync: (expectedSequence) => draftApi.syncYahoo(session.id, expectedSequence),
      onSessionChange,
    }),
    onSuccess: (result) => {
      setNotice(result.status === "already_running"
        ? "A Yahoo check is already running."
        : `Yahoo checked: ${result.counts?.applied || 0} auto-approved, ${result.counts?.confirmed || 0} confirmed, ${result.counts?.proposed || 0} awaiting approval.`);
      void refresh().catch((error: Error) => setNotice(error.message));
    },
    onError: (error: Error) => { setNotice(error.message); void recover(error); },
  });
  const rankingSync = useMutation({
    mutationFn: async (forceRefresh: boolean) => {
      const latest = await draftApi.session(session.id);
      return draftApi.syncRankings(league.id, session.id, latest.current_sequence, forceRefresh);
    },
    onSuccess: async (result) => {
      onSessionChange(result.session);
      setNotice(result.status === "already_running"
        ? "A ranking sync is already running. Retry shortly."
        : "Rankings loaded. Next-turn guidance has been updated.");
      await refresh();
    },
    onError: (error: Error) => { void recover(error); },
  });
  useEffect(() => {
    if (session.kind !== "live" || !league.yahoo_key || session.ranking_snapshot_id
      || rankingAttempted.current.has(session.id)) return;
    rankingAttempted.current.add(session.id);
    rankingSync.mutate(false);
  }, [league.yahoo_key, rankingSync, session.id, session.kind, session.ranking_snapshot_id]);
  const resolveYahoo = useMutation({
    mutationFn: ({ conflictId, resolution }: { conflictId: number; resolution: "keep_canonical" | "accept_incoming" | "ignore_incoming" }) =>
      draftApi.resolveConflict(session.id, conflictId, session.current_sequence, resolution),
    onSuccess: async () => { setNotice("Yahoo proposal resolved and added to the audit trail."); await refresh(); },
    onError: (error: Error) => void recover(error),
  });
  const simulation = useMutation({
    mutationFn: () => draftApi.simulate(
      session.id,
      session.current_sequence,
      (recommendationQuery.data?.candidates || []).map((candidate) => candidate.player_id),
    ),
    onError: (error: Error) => void recover(error),
  });
  const simulationRun = useQuery({
    queryKey: ["draft-computation", session.id, simulation.data?.run_id],
    queryFn: () => draftApi.computation(session.id, simulation.data?.run_id || 0),
    enabled: Boolean(
      simulation.data?.run_id
      && ["queued", "running"].includes(simulation.data.status),
    ),
    refetchInterval: (query) => ["queued", "running"].includes(query.state.data?.status || "") ? 400 : false,
  });
  const automaticOpponent = useMutation({
    mutationFn: (expectedSequence: number) => draftApi.advanceOpponent(session.id, expectedSequence),
    onSuccess: async (result) => {
      onSessionChange(result.session);
      queryClient.setQueryData(["draft-board-v2", session.id], result.board);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["draft-recommendations-v2", session.id] }),
        queryClient.invalidateQueries({ queryKey: ["draft-sessions-v2", league.id] }),
      ]);
    },
    onError: (error: Error) => {
      setNotice(error.message);
      void recover(error);
    },
  });

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (event.defaultPrevented || target.closest?.("dialog") || document.querySelector("dialog[open]")) return;
      const editing = ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if (event.key === "/" && !editing) {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === "Escape") {
        setSearch("");
        setCorrectionTarget(undefined);
        searchRef.current?.blur();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (
      automaticMock
      || session.status !== "LIVE"
      || session.source_mode === "manual"
    ) return;
    let inFlight = false;
    let disposed = false;
    let nextAttemptAt = 0;
    const poll = async () => {
      if (disposed || inFlight || Date.now() < nextAttemptAt || document.visibilityState !== "visible") return;
      inFlight = true;
      try {
        await yahooSync.mutateAsync();
      } catch (error) {
        // The mutation's onError path surfaces the typed provider failure.
        if (error instanceof ApiError && error.code === "yahoo_rate_limited") {
          const retryAfter = typeof error.payload.retry_after_seconds === "number"
            ? error.payload.retry_after_seconds
            : 300;
          nextAttemptAt = Date.now() + retryAfter * 1_000;
        }
      } finally {
        inFlight = false;
      }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void poll();
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 20_000);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [automaticMock, session.id, session.source_mode, session.status]);

  const board = boardQuery.data;
  const recommendations = recommendationQuery.data;
  const simulationResult = simulationRun.data || simulation.data;
  const simulationBusy = simulation.isPending || ["queued", "running"].includes(simulationResult?.status || "");
  const currentTeam = board?.teams.find((team) => team.slot === board.current_team_slot);
  const ownerTeam = board?.teams.find((team) => team.is_owner);
  const rosterPositionCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const pick of ownerTeam?.roster || []) {
      const rosterPosition = pick.position || "—";
      counts.set(rosterPosition, (counts.get(rosterPosition) || 0) + 1);
    }
    return [...counts.entries()].sort(([left], [right]) => {
      const leftIndex = ROSTER_POSITION_ORDER.indexOf(left);
      const rightIndex = ROSTER_POSITION_ORDER.indexOf(right);
      return (leftIndex === -1 ? ROSTER_POSITION_ORDER.length : leftIndex)
        - (rightIndex === -1 ? ROSTER_POSITION_ORDER.length : rightIndex);
    });
  }, [ownerTeam?.roster]);
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return (board?.available_players || []).filter((player) => {
      const matchesPosition = position === "ALL" || player.position === position;
      const matchesSearch = !query || `${player.name} ${player.pro_team} ${player.position}`.toLowerCase().includes(query);
      return matchesPosition && matchesSearch;
    });
  }, [board?.available_players, position, search]);
  const latestPick = board?.picks.at(-1);
  const fullBoard = board?.completed_picks === board?.total_picks;
  const healthLabel = automaticMock
    ? "Automatic mock"
    : yahooAutoApprove
      ? "Yahoo auto-approve"
      : yahooApproval
        ? "Yahoo approval"
        : "Manual canonical";
  const canRecord = session.status === "LIVE"
    && (!automaticMock || Boolean(board?.owner_on_clock))
    && (!board?.owner_on_clock || recommendations?.status === "ready");

  useEffect(() => {
    if (
      !board
      || board.status !== "LIVE"
      || board.opponent_mode !== "automatic"
      || board.owner_on_clock
      || board.completed_picks >= board.total_picks
      || automaticOpponent.isPending
      || automaticOpponent.isError
    ) return;
    const timer = window.setTimeout(
      () => automaticOpponent.mutate(board.current_sequence),
      550,
    );
    return () => window.clearTimeout(timer);
  }, [automaticOpponent, board]);

  if (boardQuery.isLoading) return <div className="draft-cockpit-loading"><span />Restoring the canonical draft board…</div>;
  if (boardQuery.error || !board) return <div className="error-panel" role="alert">{boardQuery.error instanceof Error ? boardQuery.error.message : "The draft board could not be loaded."}</div>;

  return <div className="draft-cockpit">
    <section className="draft-live-strip" aria-label="Draft status">
      <div className={`draft-health ${board.freshness.provisional ? "provisional" : ""}`}><ShieldCheck size={16} /><span><strong>{healthLabel}</strong><small>Read-only · never submits to Yahoo</small></span></div>
      <div><Clock3 size={16} /><span><strong>{board.current_overall_pick ? `Pick ${board.current_overall_pick} · Round ${board.current_round}` : "Draft complete"}</strong><small>{currentTeam ? `${currentTeam.name} is on the clock` : `${board.completed_picks} picks recorded`}</small></span></div>
      <div className={board.owner_on_clock ? "owner-clock" : ""}><span><strong>{board.owner_on_clock ? "You’re on the clock" : board.next_owner_pick ? `Your next pick: ${board.next_owner_pick}` : board.completed_picks === board.total_picks ? "Draft complete" : "No more owner turns"}</strong><small>{ownerTeam?.roster?.length || 0} {(ownerTeam?.roster?.length || 0) === 1 ? "player" : "players"} on your roster</small></span></div>
      <button className={fullBoard ? "primary" : "ghost"} disabled={action.isPending} onClick={() => action.mutate(fullBoard ? "complete" : session.status === "PAUSED" ? "resume" : "pause")}>
        {fullBoard ? <><Check size={15} /> Finish draft</> : session.status === "PAUSED" ? <><Play size={15} /> Resume</> : <><Pause size={15} /> Pause</>}
      </button>
    </section>

    {automaticMock && !board.owner_on_clock && board.status === "LIVE" && <div className="draft-auto-status" role="status" aria-live="polite"><Bot size={17} /><span><strong>{automaticOpponent.isPending ? `${currentTeam?.name || "Opponent"} is choosing…` : "Automatic opponents are advancing"}</strong><small>One deterministic pick at a time. The room stops before every one of your turns.</small></span><button className="ghost" disabled={automaticOpponent.isPending} onClick={() => automaticOpponent.mutate(board.current_sequence)}><Play size={14} /> Pick now</button></div>}

    {notice && <div className="draft-notice" role="status" aria-live="polite"><AlertCircle size={17} /><span><PlayerMentions text={notice} /></span><button aria-label="Dismiss message" onClick={() => setNotice(undefined)}><X size={15} /></button></div>}
    {correctionTarget && <div className="draft-correction" role="status"><RotateCcw size={17} /><span><strong>Correcting pick {correctionTarget.overall_pick}: <PlayerDetailsButton player={{ id: correctionTarget.player_id, name: correctionTarget.player_name }} /></strong><small>Choose the intended player below. The original remains in history.</small></span><button className="ghost" onClick={() => setCorrectionTarget(undefined)}>Cancel</button></div>}

    <section className="panel draft-roster-overview" aria-labelledby="draft-roster-heading">
      <div className="draft-roster-heading">
        <div><span className="eyebrow">Your roster</span><h2 id="draft-roster-heading">{ownerTeam?.name || "Your team"}</h2></div>
        <div className="draft-roster-summary" aria-label="Roster position totals">
          {rosterPositionCounts.map(([rosterPosition, count]) => <span key={rosterPosition}><b>{count}</b> {rosterPosition}</span>)}
          <strong>{ownerTeam?.roster?.length || 0}/{session.round_count}</strong>
        </div>
      </div>
      {ownerTeam?.roster?.length
        ? <div className="draft-owner-roster">{ownerTeam.roster.map((pick) => <div key={pick.event_id}><span className={`position ${pick.position?.toLowerCase()}`}>{pick.position}</span><span className="draft-roster-player"><PlayerDetailsButton player={{ id: pick.player_id, name: pick.player_name, position: pick.position, pro_team: pick.pro_team }} /><small>{pick.pro_team || "NFL team TBD"} · Pick {pick.overall_pick}</small></span><span className={`draft-bye-week ${pick.bye_week ? "" : "unknown"}`}>BYE {pick.bye_week || "TBD"}</span></div>)}</div>
        : <p className="muted">Your picks will stay visible here as the mock draft advances.</p>}
    </section>

    <section className="draft-decision-zone" aria-labelledby="draft-recommendation-heading">
      <div className="draft-section-heading"><div><span className="eyebrow">Decision cockpit</span><h2 id="draft-recommendation-heading">Who should I take?</h2></div><div className="draft-decision-tools">{recommendations?.freshness.positional_run && <span className="draft-run-alert">{recommendations.freshness.positional_run}</span>}{session.kind === "live" && league.yahoo_key && <button className="ghost" disabled={rankingSync.isPending} onClick={() => rankingSync.mutate(Boolean(session.ranking_snapshot_id))}><RefreshCw size={15} className={rankingSync.isPending ? "spin" : ""} />{rankingSync.isPending ? "Syncing rankings…" : rankingSync.isError ? "Retry rankings" : session.ranking_snapshot_id ? "Refresh rankings" : "Sync rankings"}</button>}<button className="ghost" disabled={simulationBusy || !recommendations?.candidates.length} onClick={() => simulation.mutate()}><BarChart3 size={15} />{simulationBusy ? "Comparing…" : "Compare two turns"}</button></div></div>
      {rankingSync.error && <div className="error-panel" role="alert">{rankingSync.error.message}</div>}
      {recommendationQuery.isLoading
        ? <div className="draft-candidate-grid" aria-label="Loading recommendations">{[1, 2, 3].map((rank) => <div className="draft-candidate skeleton" key={rank} />)}</div>
        : recommendations?.candidates.length
          ? <div className="draft-candidate-grid">{recommendations.candidates.map((candidate, index) => <CandidateCard key={candidate.player_id} candidate={candidate} rank={index + 1} canRecord={canRecord || Boolean(correctionTarget)} pending={record.isPending && record.variables === candidate.player_id} onRecord={() => record.mutate(candidate.player_id)} onQueue={() => queue.mutate({ playerId: candidate.player_id })} />)}</div>
          : <div className="draft-unavailable"><AlertCircle size={20} /><div><strong>Deterministic advice is unavailable</strong><p>Add an eligible projection or wait for the current board calculation. No substitute score is fabricated.</p></div></div>}
      {simulationResult && <div className={`draft-simulation ${simulationResult.status}`}><div><span className="eyebrow">Two-turn scenario</span><strong>{simulationResult.status === "ready" ? `${simulationResult.playouts} bounded playouts` : ["queued", "running"].includes(simulationResult.status) ? "Scenario calculation running…" : "Simulation unavailable"}</strong></div>{simulationResult.results.map((result, index) => <div key={result.player_id}><b>{index + 1}</b><span><PlayerDetailsButton player={{ id: result.player_id, name: result.name }} /><small>{result.lower_utility.toFixed(1)}–{result.upper_utility.toFixed(1)} utility range</small></span><em>{result.mean_utility.toFixed(1)}</em></div>)}</div>}
      {simulation.error && <div className="error-panel">{simulation.error.message}</div>}
    </section>

    <div className="draft-workspace">
      <section className="panel draft-board-panel">
        <div className="draft-board-tools">
          <div><span className="eyebrow">Player board</span><h2>{filtered.length} available</h2></div>
          <label className="draft-search"><Search size={16} /><input ref={searchRef} value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search players  /" aria-label="Search available players" /></label>
        </div>
        <div className="draft-position-tabs" aria-label="Position filters">{POSITIONS.map((item) => <button key={item} className={position === item ? "active" : ""} onClick={() => setPosition(item)}>{item}</button>)}</div>
        <div className="draft-player-list">{filtered.slice(0, 80).map((player) => <PlayerRow key={player.id} player={player} queued={board.queue.some((item) => item.id === player.id)} highlighted={intentPlayerId === player.id} disabled={record.isPending || (!correctionTarget && !canRecord)} correction={Boolean(correctionTarget)} onRecord={() => record.mutate(player.id)} onQueue={() => queue.mutate({ playerId: player.id })} />)}{!filtered.length && <div className="draft-list-empty">No available players match this filter.</div>}</div>
      </section>

      <aside className="draft-context-rail">
        <section className="panel draft-source-panel">
          {automaticMock ? <><div className="panel-title"><div><span className="eyebrow">Mock source</span><h2>Team-aware opponents</h2></div><Bot size={18} /></div><p className="muted">Each team chooses from the frozen Yahoo ranking with deterministic roster-need adjustments. Synthetic picks never run in a live or Yahoo-connected room.</p></> : <>
          <div className="panel-title"><div><span className="eyebrow">Live source</span><h2>{session.source_mode === "manual" ? "Manual only" : yahooAutoApprove ? "Yahoo auto-approve" : "Yahoo approval"}</h2></div><Radio size={18} /></div>
          {session.source_mode === "manual" ? <><p className="muted">Enable the scraper in approval mode first. Yahoo cannot change the canonical board until you accept each pick.</p><button className="ghost" disabled={sourceMode.isPending} onClick={() => sourceMode.mutate({ mode: "yahoo_scrape_shadow" })}>Use scraper with approval</button></> : <>
            <p className="muted">{yahooAutoApprove
              ? "Clean next-in-sequence Yahoo picks are recorded automatically. Unknown, conflicting, corrected, or out-of-order picks still wait for approval."
              : "Review Yahoo picks here until you trust the feed, then explicitly enable auto-approve for safe sequential picks."}</p>
            <div className="draft-source-actions">
              <button className="primary" disabled={yahooSync.isPending} onClick={() => yahooSync.mutate()}><RefreshCw className={yahooSync.isPending ? "spin" : ""} size={14} />{yahooSync.isPending ? "Checking…" : "Check Yahoo"}</button>
              {yahooApproval
                ? <button className="ghost" disabled={sourceMode.isPending} onClick={() => setConfirmingAuthority(true)}>Enable auto-approve</button>
                : <button className="ghost" disabled={sourceMode.isPending} onClick={() => sourceMode.mutate({ mode: yahooShadowMode })}>Require approval</button>}
              <button className="ghost" disabled={sourceMode.isPending} onClick={() => sourceMode.mutate({ mode: "manual" })}>Manual only</button>
            </div>
            {confirmingAuthority && yahooApproval && <div className="draft-authority-confirm" role="group" aria-label="Confirm Yahoo auto-approve">
              <strong>Auto-approve safe Yahoo picks?</strong>
              <p>You are confirming that you validated this feed. Only mapped picks arriving next in snake order will apply automatically; every mismatch will still stop for approval.</p>
              <div><button className="ghost" disabled={sourceMode.isPending} onClick={() => setConfirmingAuthority(false)}>Cancel</button><button className="primary" disabled={sourceMode.isPending} onClick={() => sourceMode.mutate({ mode: yahooAuthoritativeMode, ownerConfirmed: true })}>{sourceMode.isPending ? "Enabling…" : "Auto-approve safe picks"}</button></div>
            </div>}
            {(conflictsQuery.data || []).filter((item) => item.status === "unresolved").map((conflict) => <div className="draft-yahoo-proposal" key={conflict.id}><span><strong>Pick {conflict.overall_pick}: {conflict.incoming.player_name ? <PlayerDetailsButton player={{ id: conflict.incoming.player_id, name: conflict.incoming.player_name }} /> : "Yahoo player"}</strong><small>Yahoo differs from or is ahead of the canonical board.</small></span><div><button disabled={resolveYahoo.isPending} onClick={() => resolveYahoo.mutate({ conflictId: conflict.id, resolution: "keep_canonical" })}>Keep board</button><button className="primary" disabled={resolveYahoo.isPending || !conflict.incoming.player_id} onClick={() => resolveYahoo.mutate({ conflictId: conflict.id, resolution: "accept_incoming" })}>Accept Yahoo</button></div></div>)}
            {!conflictsQuery.data?.some((item) => item.status === "unresolved") && <p className="muted">No unresolved Yahoo proposals.</p>}
          </>}</>}
        </section>
        <section className="panel draft-exposure-panel">
          <div className="panel-title"><div><span className="eyebrow">Across leagues</span><h2>Exposure</h2></div><Layers3 size={18} /></div>
          {exposureQuery.data?.players.some((player) => player.league_count > 0) ? <div className="draft-exposure-list">{exposureQuery.data.players.filter((player) => player.league_count > 0).sort((a, b) => b.league_count - a.league_count).slice(0, 5).map((player) => <div key={player.player_id}><span><PlayerDetailsButton player={{ id: player.player_id, name: player.name }} /><small>{player.leagues.map((item) => item.name).join(" · ")}</small></span><b>{player.league_count}</b></div>)}</div> : <p className="muted">No candidate is rostered in another mapped league. Exposure is informational only.</p>}
        </section>
        <section className="panel draft-queue-panel">
          <div className="panel-title"><div><span className="eyebrow">Your queue</span><h2>Next up</h2></div><ListPlus size={18} /></div>
          {board.queue.length ? <div className="draft-queue-list">{board.queue.map((player, index) => <div key={player.id}><b>{index + 1}</b><span><PlayerDetailsButton player={{ id: player.id, name: player.name }} /><small>{player.position} · {player.pro_team} · BYE {player.bye_week || "TBD"}</small></span><button aria-label={`Remove ${player.name} from queue`} onClick={() => queue.mutate({ playerId: player.id, remove: true })}><X size={14} /></button></div>)}</div> : <p className="muted">Queue players from the board or candidate cards. It persists across reloads.</p>}
        </section>
        <section className="panel draft-picks-panel">
          <div className="panel-title"><div><span className="eyebrow">Canonical history</span><h2>Recent picks</h2></div>{latestPick && <button className="ghost" disabled={undo.isPending || session.status === "COMPLETE"} onClick={() => undo.mutate(latestPick)}><Undo2 size={14} /> Undo</button>}</div>
          {board.picks.length ? <div className="draft-pick-list">{[...board.picks].reverse().slice(0, 12).map((pick) => <div key={pick.event_id}><b>{pick.overall_pick}</b><span><PlayerDetailsButton player={{ id: pick.player_id, name: pick.player_name, position: pick.position, pro_team: pick.pro_team }} /><small>{board.teams.find((team) => team.slot === pick.team_slot)?.name} · {pick.position || "—"}</small></span><button className="draft-correct-button" onClick={() => setCorrectionTarget(pick)}>Correct</button></div>)}</div> : <p className="muted">The first recorded pick will appear here.</p>}
        </section>
      </aside>
    </div>
  </div>;
}

function PlayerRow({ player, queued, highlighted, disabled, correction, onRecord, onQueue }: { player: DraftPlayer; queued: boolean; highlighted: boolean; disabled: boolean; correction: boolean; onRecord: () => void; onQueue: () => void }) {
  const projection = player.projected_points > 0
    ? `${player.projected_points.toFixed(1)} projected pts`
    : "projection not loaded";
  const hasRange = player.floor > 0 && player.ceiling > player.projected_points;
  const risk = hasRange
    ? ` · P20–P80 ${player.floor.toFixed(0)}–${player.ceiling.toFixed(0)} · ${Math.round(player.risk * 100)}% downside`
    : "";
  return <div className={`draft-board-row ${highlighted ? "intent" : ""}`}>
    <span className={`position ${player.position.toLowerCase()}`}>{player.position}</span>
    <span className="draft-board-player"><span><PlayerDetailsButton player={{ id: player.id, name: player.name }} /><span className={`draft-bye-week ${player.bye_week ? "" : "unknown"}`}>BYE {player.bye_week || "TBD"}</span></span><small>{player.pro_team} · {projection}{risk}</small></span>
    {highlighted && <span className="draft-intent"><Check size={13} /> Confirm again</span>}
    <button className="ghost draft-queue-button" disabled={queued} onClick={onQueue}>{queued ? <Check size={15} /> : <ListPlus size={15} />}<span>{queued ? "Queued" : "Queue"}</span></button>
    <button className="draft-record-button" disabled={disabled} onClick={onRecord}>{correction ? "Use replacement" : "Record"}<ChevronRight size={15} /></button>
  </div>;
}
