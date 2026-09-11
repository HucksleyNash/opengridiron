import assert from "node:assert/strict";
import test from "node:test";
import { lineupChanges, orderedRoster, points, projectionPeriod, projectionSummary, rosLabel, sourceTime, rosterGroup, signedPoints, slotLabel } from "../src/features/leagues/league-display.ts";

const player = (id, position, current_slot, projected_points = 100) => ({ id, name: `Player ${id}`, position, current_slot, projected_points });
const slots = ["QB", "RB", "RB", "WR", "WR", "TE", "W/R/T", "K", "DEF", "BN", "IR", "IR+"];

test("rosters follow football order and separate bench, reserve, and unassigned", () => {
  const roster = [player(1, "DEF", "DEF"), player(2, "RB", "BN"), player(3, "QB", "QB"), player(4, "WR", "IR+"), player(5, "WR", null), player(6, "RB", "RB"), player(7, "WR", "WR")];
  assert.deepEqual(orderedRoster(roster, slots).map(p => p.id), [3, 6, 7, 1, 2, 4, 5]);
  assert.equal(rosterGroup(player(8, "WR", "NA"), slots), "Reserve");
  assert.equal(rosterGroup(player(9, "RB", "unexpected"), slots), "Unassigned");
  assert.equal(rosterGroup(player(10, "D/ST", "D/ST"), slots), "Starters");
  assert.equal(slotLabel("W/R/T"), "FLEX");
  assert.equal(slotLabel("BN"), "Bench");
});

test("lineup changes compare player membership, not repeated slot ordering", () => {
  const a = player(1, "WR", "WR"), b = player(2, "WR", "WR"), c = player(3, "RB", "W/R/T"), d = player(4, "RB", "BN");
  const result = lineupChanges([a, b, c, d], { assignments: [{ slot: "WR", player: b }, { slot: "WR", player: a }, { slot: "W/R/T", player: d }] }, slots);
  assert.deepEqual(result.start.map(row => row.player.id), [4]);
  assert.deepEqual(result.bench.map(p => p.id), [3]);
  const unchanged = lineupChanges([a, b, c], { assignments: [{ slot: "WR", player: b }, { slot: "WR", player: a }, { slot: "W/R/T", player: c }] }, slots);
  assert.deepEqual(unchanged, { start: [], bench: [] });
});

test("slot-only reshuffling does not invent a start or bench action", () => {
  const a = player(1, "WR", "WR"), b = player(2, "WR", "W/R/T");
  assert.deepEqual(lineupChanges([a, b], { assignments: [{ slot: "W/R/T", player: a }, { slot: "WR", player: b }] }, slots), { start: [], bench: [] });
});

test("point formatting preserves zero, negative values, and missing-data distinctions", () => {
  assert.equal(points(1690.3), "1,690.3");
  assert.equal(points(0), "0.0");
  assert.equal(points(null), "Not available");
  assert.equal(points(NaN), "Not available");
  assert.equal(signedPoints(7.5), "+7.5");
  assert.equal(signedPoints(-2.2), "-2.2");
});

test("projection summaries never turn missing dates or mixed periods into fresh weekly forecasts", () => {
  const weekly = { period: "week", season: 2026, week: 1 };
  const season = { period: "season", season: 2026 };
  assert.equal(projectionPeriod(weekly), "2026 · Week 1");
  assert.equal(projectionPeriod(season), "2026 · Full season");
  assert.match(projectionSummary([{ projection: weekly }, { projection: season }]), /Mixed projection periods/);
  assert.match(projectionSummary([{}]), /unknown/);
  assert.equal(sourceTime(null), "Not supplied");
  assert.equal(sourceTime("not a date"), "Not supplied");
  assert.equal(rosLabel({ ros_value: 0, projection: { ros_value_state: "provided" } }), "0.0");
  assert.equal(rosLabel({ ros_value: null, projection: { ros_value_state: "missing" } }), "Not supplied");
  assert.match(rosLabel({ ros_value: 0 }), /legacy input, unverified/);
});

test("weekly objective values do not substitute source totals or central values", async () => {
  const { weeklyPoints } = await import("../src/features/leagues/league-display.ts");
  assert.equal(weeklyPoints({ points: 15.1, floor: 7.2, ceiling: 24 }, "floor"), 7.2);
  assert.equal(weeklyPoints({ points: 15.1, floor: 7.2, ceiling: 24 }, "balanced"), 15.1);
  assert.equal(weeklyPoints({ points: 15.1, floor: null, ceiling: 0 }, "ceiling"), 0);
  assert.equal(weeklyPoints({ points: 15.1, floor: null }, "floor"), null);
  assert.equal(weeklyPoints(undefined, "balanced"), null);
});

test("shared source summaries retain independent period, provider, and timestamp exceptions", async () => {
  const { sharedProjectionContext, scoringLabel } = await import("../src/features/leagues/league-display.ts");
  const a = { projection: { source: "Yahoo", period: "season", season: 2026, source_updated_at: null } };
  const b = { projection: { source: "Other", period: "week", season: 2026, week: 2, source_updated_at: "2026-09-02" } };
  assert.deepEqual(sharedProjectionContext([a, a]), { source: "Yahoo", period: "2026 · Full season", updated: "" });
  assert.deepEqual(sharedProjectionContext([a, b]), { source: null, period: null, updated: null });
  assert.equal(scoringLabel("passing_yards_yahoo_default"), "Passing yards (Yahoo default)");
});
