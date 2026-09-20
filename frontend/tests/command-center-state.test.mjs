import test from "node:test";
import assert from "node:assert/strict";
import { gamePhase, injuryPriority, lineupSummary, needsInjuryReview, poolEntryState, pregameEstimate, sourceState } from "../src/features/command-center/state.ts";

const now = Date.parse("2026-09-20T19:00:00Z");
const game = { away_team: "LA", home_team: "SEA", kickoff: "2026-09-20T20:25:00Z", home_win_probability: .7, win_probability_kind: "market", completed: false };
test("game context distinguishes started and final games and rejects unverified odds", () => {
  assert.equal(gamePhase(game, now), "upcoming");
  assert.equal(gamePhase({ ...game, kickoff: "2026-09-20T17:00:00Z" }, now), "started");
  assert.equal(gamePhase({ ...game, completed: true }, now), "final");
  assert.equal(pregameEstimate(game), "SEA 70%");
  assert.equal(pregameEstimate({ ...game, win_probability_kind: "legacy_unknown" }), "Estimate unverified");
  assert.equal(pregameEstimate({ ...game, home_win_probability: NaN }), "Estimate unavailable");
});
test("injury watch excludes benign entries and prioritizes upcoming starters with team aliases", () => {
  const row = { team: "LAR", game_status: "Out", memberships: [{ is_mine: true, slot: "WR" }] };
  assert.equal(needsInjuryReview(row), true);
  assert.equal(needsInjuryReview({ ...row, game_status: "Not designated" }), false);
  assert.ok(injuryPriority(row, [game], now) < injuryPriority({ ...row, memberships: [{ is_mine: true, slot: "Bench" }] }, [game], now));
  assert.ok(injuryPriority(row, [game], now) < injuryPriority(row, [{ ...game, kickoff: "2026-09-20T17:00:00Z" }], now));
});
test("small modeled swaps stay informational and incomplete coverage never looks ready", () => {
  const lineup = { forecasts: [{ player_id: 1, current_slot: "WR" }, { player_id: 2, current_slot: "BN" }], assignments: [{ player_id: 2 }], projected_gain: .1, projected_total: 90, partial_total: false, unfilled_slots: [], error: null };
  assert.deepEqual(lineupSummary(lineup), { text: "1 lineup option to review", tone: "neutral", changes: 1 });
  assert.equal(lineupSummary({ ...lineup, partial_total: true }).text, "Incomplete forecast coverage");
  assert.equal(lineupSummary({ ...lineup, error: "Stale roster" }).text, "Review source coverage");
  assert.equal(lineupSummary({ ...lineup, unfilled_slots: ["WR"] }).tone, "urgent");
});
test("source and local pool labels preserve missing, stale and incomplete states", () => {
  assert.equal(sourceState("failed", "2026-09-20T18:00:00Z", now).label, "Unavailable");
  assert.equal(sourceState("fresh", "2026-09-18T18:00:00Z", now).label, "Older data");
  assert.equal(sourceState("fresh", undefined, now).label, "Not checked");
  assert.equal(poolEntryState({ card_state: "locked_incomplete", missing_count: 1 }).text, "Locked · incomplete");
  assert.equal(poolEntryState({ card_state: "complete", missing_count: 0 }).text, "Complete here");
  assert.equal(poolEntryState({ card_state: "draft", missing_count: 0 }).text, "Finish saved card");
});
