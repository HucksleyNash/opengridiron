import { AlertTriangle, ArrowRight, Clock3, ListPlus } from "lucide-react";

import { PlayerDetailsButton, PlayerMentions } from "../../leagues/PlayerDetails";
import type { RecommendationCandidate } from "../types";

type Props = {
  candidate: RecommendationCandidate;
  rank: number;
  canRecord: boolean;
  pending: boolean;
  onRecord: () => void;
  onQueue: () => void;
};

function marketRank(candidate: RecommendationCandidate) {
  const rank = candidate.components.market_rank;
  return typeof rank === "number" ? `Yahoo rank #${Math.round(rank)}` : "Yahoo rank unavailable";
}

function whyNow(candidate: RecommendationCandidate) {
  if (candidate.projected_points > 0) return candidate.why_now;
  return `${marketRank(candidate)} · ${candidate.roster_impact}`;
}

function tradeoff(candidate: RecommendationCandidate) {
  const hasPlayerRisk = Boolean(candidate.range_model);
  if (hasPlayerRisk) return candidate.tradeoff;
  const availability = candidate.next_turn.label.toLowerCase();
  const timing = availability.startsWith("likely")
    ? `${candidate.name} may survive to your next pick, so taking ${candidate.position} now spends flexibility.`
    : availability.startsWith("unlikely")
      ? `Waiting is likely to lose this ${candidate.position} option before your next pick.`
      : `The Yahoo market is close to your next turn, so waiting carries real availability risk.`;
  return `${timing} No player-specific floor, ceiling, or risk model is loaded.`;
}

export function CandidateCard({ candidate, rank, canRecord, pending, onRecord, onQueue }: Props) {
  const projectionLabel = candidate.projected_points > 0 && candidate.range_model
    ? `${candidate.projected_points.toFixed(1)} projected · NFLverse P20–P80 ${candidate.floor.toFixed(1)}–${candidate.ceiling.toFixed(1)} · ${Math.round(candidate.risk * 100)}% downside`
    : candidate.projected_points > 0
      ? `${candidate.projected_points.toFixed(1)} projected pts`
    : `${marketRank(candidate)} · projection not loaded`;
  return <article className={`draft-candidate candidate-${rank}`}>
    <div className="draft-candidate-top">
      <span className="draft-candidate-rank">{rank}</span>
      <span className={`position ${candidate.position.toLowerCase()}`}>{candidate.position}</span>
      <span className={`draft-bye-week ${candidate.bye_week ? "" : "unknown"}`}>BYE {candidate.bye_week || "TBD"}</span>
      <span className="draft-score"><strong>{candidate.score.toFixed(1)}</strong><small>decision score</small></span>
    </div>
    <div className="draft-candidate-name"><h3><PlayerDetailsButton player={{ id: candidate.player_id, name: candidate.name, position: candidate.position, pro_team: candidate.pro_team }} /></h3><span>{candidate.pro_team} · {projectionLabel}</span></div>
    <div className="draft-case">
      <div><ArrowRight size={15} /><span><strong>Why now</strong><small><PlayerMentions text={whyNow(candidate)} players={[{ id: candidate.player_id, name: candidate.name }]} /></small></span></div>
      <div><Clock3 size={15} /><span><strong>Next turn</strong><small>{candidate.next_turn.label}</small></span></div>
      <div><AlertTriangle size={15} /><span><strong>Tradeoff</strong><small><PlayerMentions text={tradeoff(candidate)} players={[{ id: candidate.player_id, name: candidate.name }]} /></small></span></div>
    </div>
    {candidate.evidence?.[0] && <div className="draft-evidence"><span>{candidate.evidence[0].stale ? "Stale evidence" : candidate.evidence[0].source}</span><strong><PlayerMentions text={candidate.evidence[0].title} href={candidate.evidence[0].url} /></strong><small>Attributed · does not alter the deterministic score</small></div>}
    <div className="draft-impact"><PlayerMentions text={candidate.roster_impact} />{candidate.tier_cliff && <span>Tier cliff</span>}</div>
    <div className="draft-candidate-actions">
      <button className="primary" disabled={!canRecord || pending} onClick={onRecord}>{pending ? "Recording…" : "Record pick"}</button>
      <button className="ghost" disabled={pending} onClick={onQueue} aria-label={`Add ${candidate.name} to queue`}><ListPlus size={16} /> Queue</button>
    </div>
  </article>;
}
