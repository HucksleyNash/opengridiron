import type { Player } from "../../types";
import { points, projectionPeriod, rosLabel, sourceTime } from "./league-display";

export type RankingEvidence = { rank: number; expected_value: number; confidence: number; rationale: string[] };

export function ProjectionDetails({ player, ranking }: { player: Player; ranking?: RankingEvidence }) {
  const context = player.projection;
  return <>
    <section className="player-detail-section" aria-labelledby="player-projections-title">
      <h3 id="player-projections-title">Fantasy outlook</h3>
      <p className="player-report-meta">Stored projections · {projectionPeriod(context)}</p>
      <dl className="player-stat-tape">
        <div><dt>Floor</dt><dd>{points(player.floor)}</dd></div>
        <div className="player-projected-stat"><dt>Projected pts</dt><dd>{points(player.projected_points)}</dd></div>
        <div><dt>Ceiling</dt><dd>{points(player.ceiling)}</dd></div>
      </dl>
      <dl className="player-facts"><div><dt>Rest-of-season value</dt><dd>{rosLabel(player)}</dd></div></dl>
      <p className="league-caption">{context?.period === "week" ? "Estimates, not guaranteed outcomes." : "These values are not verified weekly forecasts."}</p>
    </section>
    {ranking && <section className="player-detail-section" aria-labelledby="player-ranking-title">
      <h3 id="player-ranking-title">Ranking evidence</h3>
      <dl className="player-facts">
        <div><dt>Overall league rank</dt><dd>{ranking.rank}</dd></div>
        <div><dt>Rank score</dt><dd>{points(ranking.expected_value)}</dd></div>
        <div><dt>Risk confidence</dt><dd>Not calibrated</dd></div>
      </dl>
      <ul className="player-ranking-reasons">{ranking.rationale.filter((line) => !/^(Rest-of-season value|Next projection)\b/.test(line)).map((line, index) => <li key={index}>{line}</li>)}</ul>
      <p className="league-caption">Matching weekly inputs support selected-team lineup fit; otherwise this is a projection review list. FAAB bids and predictive confidence are withheld. Verify projection timing, scoring and availability.</p>
    </section>}
    <section className="player-detail-section" aria-labelledby="player-provenance-title">
      <h3 id="player-provenance-title">Projection source</h3>
      <dl className="player-facts">
        <div><dt>Provider</dt><dd>{context?.source || "Not recorded for this legacy value"}</dd></div>
        <div><dt>Period</dt><dd>{projectionPeriod(context)}</dd></div>
        <div><dt>Source updated</dt><dd>{sourceTime(context?.source_updated_at)}</dd></div>
        <div><dt>Received by Open Gridiron</dt><dd>{sourceTime(context?.received_at)}</dd></div>
        <div><dt>Scoring basis</dt><dd>{context?.scoring_basis === "league_rules" ? "Declared league-rules scoring" : context?.scoring_basis === "source_points" ? "Source-scored points; not recomputed here" : "Not supplied"}</dd></div>
      </dl>
      <p className="league-caption">Source updated is the provider’s timestamp, when supplied. Received is when Open Gridiron stored these points—not their publication time. Historical values without evidence remain unverified; sync or import documented projections to replace them.</p>
    </section>
  </>;
}
