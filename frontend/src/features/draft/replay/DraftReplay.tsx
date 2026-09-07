import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, History, RotateCcw, ShieldCheck, Trophy } from "lucide-react";

import { PlayerDetailsButton, PlayerMentions } from "../../leagues/PlayerDetails";
import { draftApi } from "../api";
import type { DraftRouteLeague, DraftSession } from "../types";

type Props = {
  league: DraftRouteLeague;
  session: DraftSession;
  onSessionChange: (session: DraftSession) => void;
};

export function DraftReplay({ league, session, onSessionChange }: Props) {
  const queryClient = useQueryClient();
  const replay = useQuery({ queryKey: ["draft-replay", session.id], queryFn: () => draftApi.replay(session.id) });
  const board = useQuery({ queryKey: ["draft-board-v2", session.id], queryFn: () => draftApi.board(session.id) });
  const reopen = useMutation({
    mutationFn: () => draftApi.action(session.id, "reopen", session.current_sequence, "Owner reopened the completed draft"),
    onSuccess: async (result) => {
      onSessionChange(result.session);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["draft-board-v2", session.id] }),
        queryClient.invalidateQueries({ queryKey: ["draft-recommendations-v2", session.id] }),
        queryClient.invalidateQueries({ queryKey: ["draft-sessions-v2", league.id] }),
      ]);
    },
  });
  const owner = board.data?.teams.find((team) => team.is_owner);
  const ownerRosterCount = owner?.roster?.length || 0;
  const averageQuality = replay.data?.decisions.length
    ? replay.data.decisions.reduce((total, decision) => total + decision.decision_quality, 0) / replay.data.decisions.length
    : undefined;

  return <div className="draft-replay-page">
    <section className="draft-complete-hero panel">
      <div className="draft-complete-icon"><Trophy size={28} /></div>
      <div><span className="eyebrow">Draft complete</span><h2>{owner?.name || "Your roster"} is ready.</h2><p>{board.data?.completed_picks || 0} canonical picks are preserved with generation {session.replay_generation} of the replay.</p></div>
      <button className="ghost" disabled={reopen.isPending} onClick={() => reopen.mutate()}><RotateCcw size={16} /> Reopen draft</button>
    </section>
    <div className="draft-replay-grid">
      <section className="panel">
        <div className="panel-title"><div><span className="eyebrow">Owner roster</span><h2>What you built</h2></div><strong>{ownerRosterCount} {ownerRosterCount === 1 ? "player" : "players"}</strong></div>
        <div className="draft-final-roster">{owner?.roster?.map((pick) => <div key={pick.event_id}><span className={`position ${pick.position?.toLowerCase()}`}>{pick.position}</span><span><PlayerDetailsButton player={{ id: pick.player_id, name: pick.player_name, pro_team: pick.pro_team, position: pick.position }} /><small>{pick.pro_team} · Bye {pick.bye_week || "TBD"} · Pick {pick.overall_pick}</small></span></div>)}</div>
      </section>
      <section className="panel draft-replay-decisions">
        <div className="panel-title"><div><span className="eyebrow">No-hindsight replay</span><h2>Decisions at the time</h2></div><History size={19} /></div>
        <div className="draft-replay-summary"><ShieldCheck size={17} /><span><strong>{averageQuality === undefined ? "No linked owner advice" : `${Math.round(averageQuality * 100)}% average decision quality`}</strong><small>{replay.data?.message || "Loading immutable recommendation snapshots…"}</small></span></div>
        {replay.data?.decisions.map((decision) => <article key={decision.event.id}>
          <div><b>Pick {decision.event.overall_pick}</b><span className="draft-at-time">At time</span></div>
          <h3><PlayerDetailsButton player={{ id: decision.event.player_id, name: decision.event.player_name }} /></h3>
          <p>{decision.candidates[0]?.name === decision.event.player_name ? "You took the top deterministic option." : <>The top deterministic option was {decision.candidates[0] ? <PlayerDetailsButton player={{ id: decision.candidates[0].player_id, name: decision.candidates[0].name }} /> : "unavailable"}.</>}</p>
          <div className="draft-quality"><span style={{ width: `${Math.round(decision.decision_quality * 100)}%` }} /><small>{Math.round(decision.decision_quality * 100)}%</small></div>
        </article>)}
        {!replay.isLoading && !replay.data?.decisions.length && <p className="muted">Owner picks recorded before recommendation snapshots remain visible in history but are not retroactively graded.</p>}
      </section>
    </div>
    <div className="draft-post-grid">
      <section className="panel"><div className="panel-title"><div><span className="eyebrow">Immediate follow-up</span><h2>Waiver upgrades</h2></div></div>{replay.data?.waiver_moves?.length ? <div className="draft-waiver-list">{replay.data.waiver_moves.map((move, index) => <div key={`${move.add.id}-${move.drop.player_id}`}><b>{index + 1}</b><span><strong>Add <PlayerDetailsButton player={move.add} /> · drop <PlayerDetailsButton player={{ id: move.drop.player_id, name: move.drop.player_name }} /></strong><small>+{move.projected_point_gain.toFixed(1)} projected pts · <PlayerMentions text={move.reason} /></small></span></div>)}</div> : replay.data?.waiver_priorities.length ? <div className="draft-waiver-list">{replay.data.waiver_priorities.map((player, index) => <div key={player.id}><b>{index + 1}</b><span><PlayerDetailsButton player={player} /><small>{player.position} · {player.pro_team} · {player.projected_points.toFixed(1)} pts</small></span></div>)}</div> : <p className="muted">No compatible undrafted upgrade remains in the bound projection.</p>}</section>
      <section className="panel"><div className="panel-title"><div><span className="eyebrow">Next-draft practice</span><h2>Coaching · {replay.data?.coaching.lane || session.kind}</h2></div></div>{replay.data?.coaching.findings.length ? replay.data.coaching.findings.map((finding) => <div className="draft-coaching" key={finding.code}><strong><PlayerMentions text={finding.observation} /></strong><p><PlayerMentions text={finding.exercise} /></p><small>{finding.version} · based on {finding.evidence_event_ids.length} at-time decisions</small></div>) : <p className="muted">Complete at least {replay.data?.coaching.minimum_linked_decisions || 3} owner turns in this {session.kind} lane with linked advice before the suite names a repeatable practice pattern.</p>}</section>
    </div>
    {reopen.error && <div className="error-panel">{reopen.error.message}</div>}
    <a className="button ghost" href={`/leagues/${league.id}`}>Review league projections <ArrowRight size={15} /></a>
  </div>;
}
