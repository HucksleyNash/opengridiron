import { FormEvent, useEffect, useRef, useState } from "react";
import { ArrowRight, Bot, CheckCircle2, ClipboardList, RefreshCw, ShieldCheck } from "lucide-react";

import type { DraftSessionInput, YahooDraftOrderRefresh } from "../api";
import type { DraftRouteLeague, DraftSession } from "../types";

type Props = {
  league: DraftRouteLeague;
  session?: DraftSession;
  pending: boolean;
  syncingRankings: boolean;
  syncingYahooTeams: boolean;
  error?: Error | null;
  rankingError?: Error | null;
  yahooTeamError?: Error | null;
  onCreate: (input: DraftSessionInput) => void;
  onStart: () => void;
  onSyncRankings: (refresh: boolean) => void;
  onSyncYahooTeams: () => Promise<YahooDraftOrderRefresh>;
};

function initialTeamNames(league: DraftRouteLeague) {
  const imported = (league.team_names || []).map((name) => name.trim()).filter(Boolean);
  if (imported.length >= 8 && imported.length <= 16) return imported;
  return Array.from({ length: 12 }, (_, index) => index === 0 ? league.my_team_name || "My Team" : `Team ${index + 1}`);
}

export function DraftSetup({ league, session, pending, syncingRankings, syncingYahooTeams, error, rankingError, yahooTeamError, onCreate, onStart, onSyncRankings, onSyncYahooTeams }: Props) {
  const [kind, setKind] = useState<"live" | "mock">("mock");
  const [opponentMode, setOpponentMode] = useState<"manual" | "automatic">("automatic");
  const [teamNames, setTeamNames] = useState(() => initialTeamNames(league));
  const [teamCount, setTeamCount] = useState(() => initialTeamNames(league).length);
  const [roundCount, setRoundCount] = useState(16);
  const [ownerSlot, setOwnerSlot] = useState(() => Math.max(0, initialTeamNames(league).indexOf(league.my_team_name || "")) + 1);
  const [yahooRefresh, setYahooRefresh] = useState<YahooDraftOrderRefresh>();
  const importedTeamCount = league.team_names?.length || 0;
  const autoSyncSession = useRef<number | undefined>(undefined);

  useEffect(() => {
    const names = initialTeamNames(league);
    setTeamNames(names);
    setTeamCount(names.length);
    setOwnerSlot(Math.max(0, names.indexOf(league.my_team_name || "")) + 1);
  }, [league.id]);

  useEffect(() => {
    const needsRanking = session?.readiness.findings.some((finding) => finding.code.startsWith("ranking_"));
    if (
      session?.kind === "mock"
      && session.opponent_mode === "automatic"
      && needsRanking
      && !syncingRankings
      && !rankingError
      && autoSyncSession.current !== session.id
    ) {
      autoSyncSession.current = session.id;
      onSyncRankings(false);
    }
  }, [onSyncRankings, rankingError, session, syncingRankings]);

  const refreshYahooTeams = async () => {
    const ownerName = teamNames[ownerSlot - 1]?.trim().toLowerCase();
    let result: YahooDraftOrderRefresh;
    try {
      result = await onSyncYahooTeams();
    } catch {
      return;
    }
    setYahooRefresh(result);
    if (session || !result.orderVerified || result.teamNames.length < 8 || result.teamNames.length > 16) {
      return;
    }
    const importedOwner = ownerName
      ? result.teamNames.findIndex((name) => name.toLowerCase() === ownerName)
      : -1;
    setTeamNames(result.teamNames);
    setTeamCount(result.teamNames.length);
    setOwnerSlot(importedOwner >= 0 ? importedOwner + 1 : 1);
  };

  const yahooRefreshStatus = yahooRefresh?.ownerMatchRequired
    ? "Yahoo published the order, but your team name could not be matched uniquely. The room was left unchanged."
    : yahooRefresh?.sessionUpdated
      ? `Applied Yahoo’s ${yahooRefresh.teamNames.length}-team draft order to this room.`
      : yahooRefresh?.orderVerified
        ? `Loaded ${yahooRefresh.teamNames.length} teams in Yahoo’s draft-slot order. Confirm your team below.`
        : yahooRefresh
          ? "Yahoo has not published a complete 8–16 team draft order yet. The current room order was left unchanged."
          : undefined;

  if (session) {
    return <div className="draft-setup-grid">
      <section className="panel draft-ready-card">
        <span className="eyebrow">Session ready</span>
        <h2>{session.kind === "mock" ? "Mock draft" : "Live draft"} · {session.team_count} teams</h2>
        <p>The room uses a frozen copy of this league’s scoring, roster slots, and player projections. Starting locks the draft order.</p>
        {session.kind === "live" && league.yahoo_key && <div className="draft-team-sync">
          <span><strong>Yahoo draft order</strong><small>Refresh after Yahoo assigns slots. A complete order is applied to this room before it starts.</small></span>
          <button type="button" className="ghost" disabled={syncingYahooTeams} onClick={() => void refreshYahooTeams()}><RefreshCw className={syncingYahooTeams ? "spin" : ""} size={14} />{syncingYahooTeams ? "Scraping…" : "Refresh Yahoo order"}</button>
        </div>}
        {yahooRefreshStatus && !yahooTeamError && <p className={yahooRefresh?.orderVerified && !yahooRefresh.ownerMatchRequired ? "success" : "muted"} role="status">{yahooRefreshStatus}</p>}
        {yahooTeamError && <div className="error-panel" role="alert">{yahooTeamError.message}</div>}
        {session.kind === "mock" && session.opponent_mode === "automatic" && <div className={`draft-ranking-sync ${session.readiness.ready ? "ready" : ""}`}>
          <Bot size={19} />
          <span><strong>{syncingRankings ? "Syncing Yahoo rankings and NFLverse ranges…" : session.readiness.ready ? "Automatic opponents ready" : "Yahoo rankings required"}</strong><small>{session.readiness.ready ? "Yahoo projections, NFLverse ranges, and the ranked pool are frozen for this draft." : "Start stays disabled until enough ranked, projected players are mapped."}</small></span>
          <button type="button" className="ghost" disabled={syncingRankings} onClick={() => onSyncRankings(Boolean(session.ranking_snapshot_id))}><RefreshCw className={syncingRankings ? "spin" : ""} size={14} />{rankingError ? "Retry" : session.ranking_snapshot_id ? "Refresh" : "Sync now"}</button>
        </div>}
        <div className="draft-readiness">
          {session.readiness.ready
            ? <div className="draft-finding ready"><CheckCircle2 size={18} /><span><strong>Ready to draft</strong><small>{session.round_count} rounds · Pick {session.owner_team_slot}</small></span></div>
            : session.readiness.findings.map((finding) => <div className="draft-finding" key={finding.code}><ClipboardList size={18} /><span><strong>{finding.message}</strong><small>{finding.code.replaceAll("_", " ")}</small></span></div>)}
        </div>
        <button className="primary draft-start" onClick={onStart} disabled={pending || syncingYahooTeams || !session.readiness.ready}>
          {pending ? "Starting…" : "Enter the draft room"}<ArrowRight size={17} />
        </button>
        {error && <div className="error-panel" role="alert">{error.message}</div>}
        {rankingError && <div className="error-panel" role="alert">{rankingError.message}</div>}
      </section>
      <section className="panel draft-order-card">
        <span className="eyebrow">Snake order</span>
        <h2>Your table</h2>
        <div className="draft-team-order">{session.teams.map((team) => <div className={team.is_owner ? "owner" : ""} key={team.slot}><b>{team.slot}</b><span>{team.name}</span>{team.is_owner && <small>You</small>}</div>)}</div>
      </section>
    </div>;
  }

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const confirmedNames = teamNames.map((name, index) => name.trim() || `Team ${index + 1}`);
    const input: DraftSessionInput & { team_names: string[] } = {
      kind,
      team_count: teamCount,
      round_count: roundCount,
      owner_team_slot: ownerSlot,
      owner_team_name: confirmedNames[ownerSlot - 1] || "My Team",
      team_names: confirmedNames,
      source_mode: "manual",
      opponent_mode: kind === "mock" ? opponentMode : "manual",
    };
    onCreate(input);
  };

  const resizeTeams = (value: number) => {
    setTeamCount(value);
    setOwnerSlot((current) => Math.min(current, value));
    setTeamNames((current) => Array.from(
      { length: Math.max(0, value) },
      (_, index) => current[index] || `Team ${index + 1}`,
    ));
  };

  const chooseOwnerSlot = (value: number) => {
    if (!importedTeamCount && value !== ownerSlot) {
      setTeamNames((current) => current.map((name, index) => {
        if (index === value - 1) return current[ownerSlot - 1] || "My Team";
        if (index === ownerSlot - 1) return `Team ${ownerSlot}`;
        return name;
      }));
    }
    setOwnerSlot(value);
  };

  return <div className="draft-setup-grid">
    <section className="panel draft-intro-card">
      <span className="eyebrow">Read-only second screen</span>
      <h2>Make the next pick with context.</h2>
      <p>The Draft Suite tracks who is gone, your roster, tier cliffs, and the best alternatives. It gives advice but never submits a pick to Yahoo.</p>
      <div className="draft-promise"><ShieldCheck size={20} /><span><strong>You keep final control</strong><small>Every recorded pick stays local and reversible.</small></span></div>
      <div className="draft-promise"><ClipboardList size={20} /><span><strong>Inputs are frozen</strong><small>At-time advice stays reproducible in replay.</small></span></div>
    </section>
    <section className="panel draft-create-card">
      <span className="eyebrow">New session</span>
      <h2>Set the table</h2>
      <p className="muted">Using {league.player_count} players from {league.name}.</p>
      <form onSubmit={submit}>
        <label className="field"><span>Session type</span><select value={kind} onChange={(event) => setKind(event.target.value as "live" | "mock")}><option value="mock">Mock draft</option><option value="live">Live draft</option></select></label>
        {kind === "mock" && <label className="field"><span>Opponent picks</span><select aria-label="Opponent picks" value={opponentMode} onChange={(event) => setOpponentMode(event.target.value as "manual" | "automatic")}><option value="automatic">Automatic · recommended</option><option value="manual">Manual entry</option></select><small>Automatic mocks use the frozen Yahoo order plus each team’s roster needs.</small></label>}
        <div className="draft-setup-fields">
          <label className="field"><span>Teams</span><input type="number" min="8" max="16" value={teamCount} onChange={(event) => resizeTeams(Number(event.target.value))} /></label>
          <label className="field"><span>Rounds</span><input type="number" min="1" max="30" value={roundCount} onChange={(event) => setRoundCount(Number(event.target.value))} /></label>
          <label className="field"><span>Your slot</span><input type="number" min="1" max={teamCount} value={ownerSlot} onChange={(event) => chooseOwnerSlot(Number(event.target.value))} /></label>
        </div>
        <p className="muted">{importedTeamCount
          ? league.team_order_source === "yahoo_draft_order"
            ? `Yahoo’s ${importedTeamCount}-team draft order is loaded. Confirm your team is marked.`
            : `Yahoo found ${importedTeamCount} team names. Refresh after slots are assigned, then mark yours.`
          : "Name each team in draft-slot order, then mark yours."}</p>
        {kind === "live" && league.yahoo_key && <div className="draft-team-sync">
          <span><strong>Yahoo draft order</strong><small>Refresh after Yahoo assigns slots. Only a complete Round 1 order is loaded into the room.</small></span>
          <button type="button" className="ghost" disabled={syncingYahooTeams} onClick={() => void refreshYahooTeams()}><RefreshCw className={syncingYahooTeams ? "spin" : ""} size={14} />{syncingYahooTeams ? "Scraping…" : "Refresh Yahoo order"}</button>
        </div>}
        {yahooRefreshStatus && !yahooTeamError && <p className={yahooRefresh?.orderVerified ? "success" : "muted"} role="status">{yahooRefreshStatus}</p>}
        {yahooTeamError && <div className="error-panel" role="alert">{yahooTeamError.message}</div>}
        <div className="draft-team-order">
          {teamNames.map((name, index) => {
            const slot = index + 1;
            const isOwner = slot === ownerSlot;
            return <div className={isOwner ? "owner" : ""} key={slot}>
              <b>{slot}</b>
              <input aria-label={`Draft slot ${slot} team name`} value={name} maxLength={160} required onChange={(event) => setTeamNames((current) => current.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} />
              <button type="button" className={isOwner ? "primary" : "ghost"} aria-label={`Mark draft slot ${slot} as your team`} onClick={() => chooseOwnerSlot(slot)}>{isOwner ? "You" : "Mine"}</button>
            </div>;
          })}
        </div>
        <button className="primary" disabled={pending || league.player_count === 0}>{pending ? "Creating room…" : "Create draft room"}</button>
        {league.player_count === 0 && <div className="error-panel">Add or synchronize players before creating a draft.</div>}
        {error && <div className="error-panel" role="alert">{error.message}</div>}
      </form>
    </section>
  </div>;
}
