import assert from "node:assert/strict";
import { test } from "node:test";
import { createRequire } from "node:module";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const { build } = createRequire(require.resolve("vite"))("esbuild");
const { QueryClient, QueryClientProvider } = require("@tanstack/react-query");
const { MemoryRouter, Routes, Route } = require("react-router-dom");
async function load(path) {
  const result = await build({ entryPoints: [new URL(path, import.meta.url).pathname], bundle: true, write: false, format: "cjs", platform: "node", packages: "external", loader: { ".css": "empty" }, jsx: "automatic" });
  const module = { exports: {} };
  new Function("require", "module", "exports", result.outputFiles[0].text)(require, module, module.exports);
  return module.exports;
}
const { Report } = await load("../src/features/league-analysis/LeagueAnalysisPanel.tsx");
const { availability, sourceLabel, safeSourceUrl, reportForWeek } = await load("../src/features/league-analysis/forecast-display.ts");
const { default: LeaguePage } = await load("../src/features/leagues/LeaguePage.tsx");

const forecast = (overrides = {}) => ({ player_id: 1, name: "Roster Runner", team: "CHI", position: "RB", rostered_by: "Team A", current_slot: "RB", status: "Active", locked: false, conditional: false, points: 12, floor: 6, ceiling: 18, confidence: "low", reason: null, warnings: [], sample_games: 16, recent_usage: 12, baseline_usage: 10, opponent: "GB", source_projection: { points: 300, source: "Yahoo", period: "season", season: 2026, week: null, comparable: false }, difference: null, ...overrides });
function savedFixture() {
  return { id: 5, league_id: 1, team_name: "Team A", week: 1, season: 2026, status: "partial", has_report: true, stale_reasons: [], created_at: "2026-09-03T12:00:00Z", completed_at: "2026-09-03T12:01:00Z", error: null,
    report: { model_version: "test-baseline", generated_at: "2026-09-03T12:01:00Z", season: 2026, week: 1, team_name: "Team A", method: "Saved method", limitations: ["No proven advantage"], changes: ["First report"], coverage: { modeled: 2, players: 3 }, league_coverage: [{ team: "Team A", modeled: 1, players: 2 }],
      forecasts: [forecast(), forecast({ player_id: 2, name: "Conditional Kicker", position: "K", rostered_by: null, status: "Inactive", conditional: true, warnings: ["Conditional on playing: Inactive.", "Current role is unverified."] })],
      lineup: { error: null, gain: 0, current_points: 12, recommended_points: 12, partial_total: true, assignments: [{ player_id: 1, name: "Roster Runner", slot: "W/R/T", points: 12, action: "Hold", reason: null, conditional: false }], waivers: [{ add_id: 2, add: "Conditional Kicker", drop_id: 1, drop: "Roster Runner", gain: 1.6, conditional: true, reason: "Verify long-term value." }], bench: [], unfilled_slots: ["QB"] },
      sources: [{ name: "NFL injury report", status: "available", url: "https://www.nfl.com/injuries/" }],
      analysis: { status: "completed", provider: "Fixture", model: "test", output: { summary: "Saved analyst summary", recommendations: ["Saved recommendation"], risks: ["Saved risk"], missing_information: ["Saved gap"], citations: ["https://www.nfl.com/injuries/", "https://data.example.com/player_stats.csv", "javascript:alert(1)"] } },
      evaluation: { scored_forecasts: 0, comparison_count: 0, mae: null, paired_model_mae: null, source_mae: null, note: "Observational only" },
    } };
}
const renderReport = (saved = savedFixture()) => renderToStaticMarkup(h(Report, { saved }));

test("waiver status, confidence, and every structured warning accompany the conditional gain", () => {
  const html = renderReport();
  const move = html.slice(html.indexOf('<article class="weekly-waiver-row"'), html.indexOf("</article>"));
  for (const expected of ["Evaluate Conditional Kicker", "+1.6", "Conditional estimate", "Inactive", "low confidence", "16 games", "Conditional on playing: Inactive.", "Current role is unverified."]) assert.ok(move.includes(expected), expected);
  assert.ok(!html.includes("Add Conditional Kicker"));
  assert.ok(html.indexOf("Verify before considering") < html.indexOf("Evaluate Conditional Kicker"));
});

test("missing or non-active availability never appears as an unqualified supported move", () => {
  for (const player of [undefined, forecast({ status: "" }), forecast({ status: "IR-Return" }), forecast({ points: null }), forecast({ warnings: ["Role unverified"] })]) assert.equal(availability(player).needsVerification, true);
  assert.equal(availability(forecast()).needsVerification, false);
  assert.equal(availability(forecast(), true).needsVerification, true);
  const saved = savedFixture();
  saved.report.forecasts = [];
  assert.ok(renderReport(saved).includes("Availability evidence is missing from this saved report"));
});

test("zero, missing estimates, and incompatible season totals stay distinct", () => {
  const saved = savedFixture();
  saved.report.forecasts = [forecast({ points: 0, difference: 99 }), forecast({ player_id: 3, name: "Missing Player", points: null, confidence: "unavailable" })];
  const html = renderReport(saved);
  assert.ok(html.includes("0.0 pts"));
  assert.ok(html.includes("Unavailable"));
  assert.ok(!html.includes("+99.0 pts difference"));
  assert.ok(html.includes("Not comparable"));
  assert.ok(html.includes("<summary>Imported source projection</summary><p>300.0 pts"));
  assert.ok(html.includes('class="weekly-evidence" role="cell"><p class="weekly-instruction"><strong>low confidence'));
});

test("comparable forecasts retain their signed difference and evidence", () => {
  const saved = savedFixture();
  saved.report.forecasts = [forecast({ difference: -2, source_projection: { points: 14, period: "week", season: 2026, week: 1, source: "Yahoo", comparable: true } })];
  assert.ok(renderReport(saved).includes("-2.0 pts difference"));
  assert.ok(renderReport(saved).includes("Matching weekly period and scoring"));
});

test("report leads with decision, labels partial totals, and places comparison before full briefing", () => {
  const html = renderReport();
  assert.ok(html.includes("No supported lineup upgrade found"));
  assert.ok(html.includes("Lineup gain · modeled portion"));
  assert.ok(html.includes("Modeled portion of lineup"));
  assert.ok(html.includes("FLEX"));
  assert.ok(html.indexOf('id="weekly-comparison"') < html.indexOf('<details class="weekly-briefing"'));
  assert.ok(html.includes('<details class="weekly-briefing" id="weekly-briefing"><summary>'));
  for (const text of ["Saved analyst summary", "Saved recommendation", "Saved risk", "Saved gap", "Saved method", "test-baseline", "First report"]) assert.ok(html.includes(text), text);
});

test("citations identify sources and files while rejecting unsafe or credential-bearing URLs", () => {
  assert.equal(sourceLabel("https://www.nfl.com/injuries/", savedFixture().report.sources), "NFL injury report");
  assert.equal(sourceLabel("https://data.example.com/player_stats.csv", []), "data.example.com · player stats.csv (file)");
  assert.equal(sourceLabel("https://data.example.com/player_stats.csv", [{ name: "NFL statistics", url: "https://data.example.com/player_stats.csv" }]), "NFL statistics (file)");
  for (const value of ["javascript:alert(1)", "data:text/html,x", "/relative", "https://user:password@example.com", "broken"]) assert.equal(safeSourceUrl(value), false);
  assert.equal(safeSourceUrl("https://www.nfl.com/injuries/"), true);
  const html = renderReport();
  assert.ok(html.includes(">NFL injury report<"));
  assert.ok(!html.includes("javascript:"));
  assert.ok(!html.includes(">Source 1<"));
});

test("saved report selection cannot silently cross the working week", () => {
  const runs = [{ id: 3, week: 2, has_report: false }, { id: 2, week: 1, has_report: true }, { id: 1, week: 1, has_report: true }];
  assert.equal(reportForWeek(runs, 2, 2)?.id, 3);
  assert.equal(reportForWeek(runs, 1)?.id, 2);
  assert.equal(reportForWeek(runs, 1, 1)?.id, 1);
  assert.equal(reportForWeek(runs, 3), undefined);
});

function renderWorkspace(url, extra = []) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, retryOnMount: false, staleTime: Infinity, gcTime: Infinity } } });
  const league = { id: 1, name: "Test league", season: 2026, source: "manual", my_team_name: "Team A", team_names: ["Team A", "Team B"], roster_slots: ["RB"], scoring: {}, player_count: 2 };
  const values = [
    [["league", 1], league], [["roster", 1], [forecast({ id: 1, pro_team: "CHI" }), forecast({ id: 2, pro_team: "GB", rostered_by: "Team B" })]],
    [["weekly-analysis-context", 1], { teams: ["Team A", "Team B"], suggested_week: 1, schedule_available: true }], [["providers"], []],
    [["weekly-analyses", 1, "Team B"], []], ...extra,
  ];
  for (const [key, data] of values) client.setQueryData(key, data);
  const html = renderToStaticMarkup(h(QueryClientProvider, { client }, h(MemoryRouter, { initialEntries: [url] }, h(Routes, null, h(Route, { path: "/leagues/:leagueId", element: h(LeaguePage, { draftSuiteEnabled: false }) })))));
  client.clear();
  return html;
}

test("both routes read the same team and week from the URL without changing saved My team", () => {
  for (const tab of ["", "&tab=forecast"]) {
    const html = renderWorkspace(`/leagues/1?team=Team+B&week=2${tab}`);
    assert.ok(html.includes("My team: Team A"));
    const context = html.slice(html.indexOf('class="league-working-context"'), html.indexOf('class="league-view-panel"'));
    assert.match(context, /<option selected="">Team B<\/option>/);
    assert.match(context, /<option value="2" selected="">Week 2<\/option>/);
    assert.ok(context.includes("Shared across Overview and Forecast"));
    if (tab) assert.ok(html.includes("No saved report for Team B · Week 2"));
  }
});

test("invalid week and unavailable team URLs cannot produce an invalid analysis", () => {
  const html = renderWorkspace("/leagues/1?tab=forecast&team=Deleted&week=99");
  assert.ok(html.includes("Selected team unavailable"));
  assert.ok(html.includes("Current: 1"));
  assert.match(html, /type="submit" disabled=""/);
});
