import assert from "node:assert/strict";
import { test } from "node:test";
import { createRequire } from "node:module";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";

// Exercise real query-driven render branches without a browser or a live-data mutation.
const require = createRequire(import.meta.url);
const { QueryClient, QueryClientProvider } = require("@tanstack/react-query");
const { MemoryRouter, Route, Routes } = require("react-router-dom");
const { build } = createRequire(require.resolve("vite"))("esbuild");
const compiled = await build({ entryPoints: [new URL("../src/features/leagues/LeaguePage.tsx", import.meta.url).pathname], bundle: true, write: false, format: "cjs", platform: "node", packages: "external", loader: { ".css": "empty" }, jsx: "automatic" });
const compiledModule = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(require, compiledModule, compiledModule.exports);
const { default: LeaguePage } = compiledModule.exports;
const projectionCompiled = await build({ entryPoints: [new URL("../src/features/leagues/ProjectionDetails.tsx", import.meta.url).pathname], bundle: true, write: false, format: "cjs", platform: "node", packages: "external", jsx: "automatic" });
const projectionModule = { exports: {} };
new Function("require", "module", "exports", projectionCompiled.outputFiles[0].text)(require, projectionModule, projectionModule.exports);
const { ProjectionDetails } = projectionModule.exports;
const league = { id: 1, name: "League with an unusually long name to preserve", source: "manual", season: 2026, scoring: { receptions: 1 }, roster_slots: ["QB", "RB", "W/R/T", "BN"], player_count: 3 };
const players = [
  { id: 1, league_id: 1, name: "Current Runner", position: "RB", pro_team: "CHI", current_slot: "RB", rostered_by: "Team A", ownership: "Team A", status: "Questionable", projected_points: 180, floor: 150, ceiling: 210, ros_value: 0, risk: 0.3 },
  { id: 2, league_id: 1, name: "Bench Runner", position: "RB", pro_team: "BUF", current_slot: "BN", rostered_by: "Team A", ownership: "Team A", status: "Active", projected_points: 360, floor: 310, ceiling: 400, ros_value: 0, risk: 0.2 },
  { id: 3, league_id: 1, name: "Free Agent", position: "QB", pro_team: "KC", current_slot: null, rostered_by: null, ownership: "W", status: "Active", projected_points: 50, floor: 30, ceiling: 70, ros_value: 0, risk: 0.2 },
];
const lineup = { mode: "balanced", season: 2026, week: 1, source: "Open Gridiron weekly model", partial_total: true, error: null, forecasts: [{ player_id: 1, points: 10 }, { player_id: 2, points: 20 }], current_total: 10, projected_total: 20, projected_gain: 10, assignments: [{ slot: "RB", player_id: 2, score: 20, action: "Start", reason: null }], unfilled_slots: ["QB", "W/R/T"], data_as_of: "2026-09-03T12:00:00Z" };
const waivers = [{ player_id: 3, rank: 1, expected_value: 18.2, confidence: 0, rationale: ["Review only: projection period unknown", "FAAB bid withheld: remaining budget and comparable winning-bid evidence are required."] }];

function render(overrides = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, retryOnMount: false, staleTime: Infinity, gcTime: Infinity } } });
  const teamName = overrides.players instanceof Error || (Array.isArray(overrides.players) && !overrides.players.some((player) => player.rostered_by)) ? "" : "Team A";
  const values = [
    [["league", 1], league, "league"], [["roster", 1], players.filter((player) => player.rostered_by), "players"],
    [["weekly-lineup", 1, "balanced", "Team A", ""], lineup, "lineup"], [["waivers-page", 1, { search: "", role: "", team: "", availability: "", status: "", team_name: teamName }], waivers, "waivers"], [["draft-sessions-v2", 1], [], "draft"],
  ];
  for (const [queryKey, fallback, key] of values) {
    let value = key in overrides ? overrides[key] : fallback;
    if (key === "waivers" && Array.isArray(value)) value = { pages: [{ items: value.map((item) => ({ ...item, player: item.player || players.find((player) => player.id === item.player_id) })), total: value.length, available: value.length, next_offset: null, facets: { teams: ["KC"], statuses: ["Active"], positions: ["QB"] } }], pageParams: [0] };
    if (value instanceof Error) client.getQueryCache().build(client, { queryKey }).setState({ status: "error", error: value, fetchStatus: "idle" });
    else if (value !== undefined) client.setQueryData(queryKey, value);
  }
  const markup = renderToStaticMarkup(h(QueryClientProvider, { client }, h(MemoryRouter, { initialEntries: ["/leagues/1"] }, h(Routes, null, h(Route, { path: "/leagues/:leagueId", element: h(LeaguePage, { draftSuiteEnabled: true }) })))));
  client.clear();
  return markup;
}

test("lineup shows API comparison, true start/bench changes, unfilled slots, and model caveats", () => {
  const html = render();
  for (const text of ["Potential change", "+10.0", "Start Bench Runner", "Bench Current Runner", "Unfilled starter slots:", "projection period unknown", "Source update times not supplied", "may replace stored projections", "Prediction confidence is not calibrated"]) assert.ok(html.includes(text), text);
  assert.match(html, /aria-pressed="true">Balanced/);
  assert.ok(html.indexOf("lineup-review-heading") < html.indexOf("current-roster-heading"));
  assert.match(html, /<summary>Bench · 1 player<\/summary>/);
  assert.ok(!html.includes("Next projection 50.0"));
  assert.ok(renderToStaticMarkup(h(ProjectionDetails, { player: players[2], ranking: waivers[0] })).includes("0.0 · legacy input, unverified"));
  assert.ok(html.includes('aria-label="Ranking details for Free Agent"'));
  assert.ok(html.includes("Player data file"));
});

test("league failures render recovery instead of permanent loading", () => {
  const html = render({ league: new Error("League request failed") });
  assert.ok(html.includes("Could not load this league"));
  assert.ok(html.includes("Try again"));
  assert.ok(!html.includes("Loading league…"));
});

test("roster failures cannot masquerade as missing roster or produce recommendations", () => {
  const html = render({ players: new Error("Roster request failed") });
  assert.ok(html.includes("Could not load roster data"));
  assert.ok(html.includes("Teams unavailable"));
  assert.ok(!html.includes("No roster imported"));
  assert.ok(!html.includes("Suggested changes"));
});

test("lineup failures retain current roster and waiver access", () => {
  const html = render({ lineup: new Error("Lineup request failed") });
  assert.ok(html.includes("Could not calculate the lineup"));
  assert.ok(html.includes("Current Runner"));
  assert.ok(html.includes("Free Agent"));
  assert.ok(!html.includes("Potential change"));
});

test("waiver failure and true empty state are distinct", () => {
  const failure = render({ waivers: new Error("Waiver request failed") });
  assert.ok(failure.includes("Could not load waiver rankings"));
  assert.ok(failure.includes("Retry watchlist"));
  assert.ok(!failure.includes("No available players."));
  const empty = render({ players: [], waivers: [] });
  assert.ok(empty.includes("No roster imported"));
  assert.ok(empty.includes("No available players."));
  assert.ok(!empty.includes("Potential change"));
});

test("initial league and lineup loading are explicit", () => {
  assert.ok(render({ league: undefined }).includes("Loading league…"));
  assert.ok(render({ lineup: undefined }).includes("Calculating balanced lineup…"));
});

test("source context separates publication and receipt times and preserves missing ROS", () => {
  const projection = { source: "Test model", period: "week", season: 2026, week: 1, source_updated_at: "2026-09-01T12:00:00Z", received_at: "2026-09-03T12:00:00Z", scoring_basis: "source_points", scoring: { receptions: 1 }, ros_value_state: "missing" };
  const html = renderToStaticMarkup(h(ProjectionDetails, { player: { ...players[2], ros_value: null, projection }, ranking: waivers[0] }));
  for (const text of ["Test model", "2026 · Week 1", "Source updated", "Received by Open Gridiron", "Not supplied", "Source-scored points; not recomputed here", "FAAB bid withheld", "Risk confidence"]) assert.ok(html.includes(text), text);
  assert.ok(!html.includes("0.0 · may be missing"));
});

test("recommended, current and bench roster rows display weekly values while source projections retain season totals", () => {
  const html = render();
  const rosters = html.slice(html.indexOf('<div class="league-roster-grid">'), html.indexOf('id="player-projections"'));
  assert.ok(rosters.includes("Week 1 pts"));
  assert.ok(rosters.includes('class="numeric">10.0</td>'));
  assert.ok(rosters.includes('class="numeric">20.0</td>'));
  assert.ok(!rosters.includes("180.0") && !rosters.includes("360.0"));
  assert.ok(html.includes("180.0") && html.includes("360.0"));
  assert.ok(html.includes("Open Gridiron weekly projections"));
});

test("missing weekly projections never fall back to season totals and explicit weekly zero stays zero", () => {
  const html = render({ lineup: { ...lineup, forecasts: [{ player_id: 1, points: null }, { player_id: 2, points: 0 }], current_total: null, projected_total: null, projected_gain: null, assignments: [{ slot: "RB", player_id: 1, score: null, action: "Hold", reason: "Missing history" }] } });
  const rosters = html.slice(html.indexOf('<div class="league-roster-grid">'), html.indexOf('id="player-projections"'));
  assert.ok(rosters.includes("Not available"));
  assert.ok(rosters.includes('class="numeric">0.0</td>'));
  assert.ok(!rosters.includes("180.0") && !rosters.includes("360.0"));
  assert.ok(html.includes("Totals include only modeled players"));
});
