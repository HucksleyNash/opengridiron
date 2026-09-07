import { expect, Page, test } from "@playwright/test";

const generated = "2026-09-04T01:00:00Z";
function reportFixture() {
  const forecast = (id: number, name: string, points: number | null, rostered_by: string | null) => ({
    player_id: id, name, team: "CHI", position: "RB", rostered_by, current_slot: id === 1 ? "RB" : "BN",
    status: "Active", locked: false, conditional: false, points, floor: points == null ? null : points - 5,
    ceiling: points == null ? null : points + 5, confidence: points == null ? "unavailable" : "low",
    reason: points == null ? "Fewer than four prior NFL games; no independent forecast." : null,
    warnings: ["Preseason baseline uses prior-season production; current role is unverified."], sample_games: 16,
    recent_usage: 18, baseline_usage: 15, opponent: "GB", difference: null,
    source_projection: { points: 280, source: "Yahoo", period: "season", season: 2026, week: null, comparable: false },
  });
  return {
    model_version: "opengridiron-weekly-v1", generated_at: generated, season: 2026, week: 1, team_name: "My Team",
    method: "Independent historical weekly baseline.", limitations: ["Experimental baseline; no demonstrated advantage over Yahoo."],
    changes: ["First saved analysis for this team and week."], coverage: { modeled: 3, players: 4 },
    forecasts: [forecast(1, "Current Starter", 10, "My Team"), forecast(2, "Bench Upgrade", 15, "My Team"), forecast(3, "Waiver Target", 20, null), forecast(4, "Rookie", null, "My Team")],
    league_coverage: [{ team: "My Team", modeled: 2, players: 3 }],
    lineup: { error: null, current_points: 10, recommended_points: 15, gain: 5, partial_total: false, unfilled_slots: [], bench: ["Current Starter"],
      assignments: [{ slot: "RB", player_id: 2, name: "Bench Upgrade", points: 15, action: "Start", reason: null, conditional: false }],
      waivers: [{ add_id: 3, add: "Waiver Target", drop_id: 1, drop: "Current Starter", gain: 5, conditional: false, reason: "Check long-term value before dropping." }] },
    sources: [{ name: "NFL statistics 2025", status: "available", received_at: generated }],
    analysis: { provider: "Test analyst", model: "test", status: "completed", output: { summary: "Prioritize the supported lineup improvement.", recommendations: ["Start Bench Upgrade."], risks: ["Verify availability before kickoff."], missing_information: [], citations: ["https://example.com/evidence", "javascript:alert(1)"] } },
    evaluation: { scored_forecasts: 0, mae: null, comparison_count: 0, paired_model_mae: null, source_mae: null, note: "Observational tracking, not a validated backtest." },
  };
}

async function mockLeague(page: Page, partial = false, leagueId = 1) {
  const report = reportFixture();
  const summaries: Record<string, unknown>[] = [];
  let polls = 0;
  const unexpected: string[] = [];
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const players = report.forecasts.map((forecast) => ({
    ...forecast, id: forecast.player_id, league_id: leagueId, pro_team: forecast.team,
    ownership: forecast.rostered_by || "FA", projected_points: 280,
    floor: 200, ceiling: 320, risk: 0.5, ros_value: null, evidence: [],
  }));
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    const league = { id: leagueId, name: `Test League ${leagueId}`, season: 2026, source: "manual", my_team_name: "My Team", team_names: ["My Team", "Rival"], scoring: {}, roster_slots: ["RB"], player_count: 4 };
    if (/\/analysis-context$|\/analyses(?:\/\d+)?$/.test(path)) expect(path).toContain(`/leagues/${leagueId}/`);
    if (path.endsWith("/onboarding/status")) return json({ configured: true, auth_required: false, capabilities: { draft_suite: true } });
    if (path.endsWith("/system/health")) return json({ status: "ok" });
    if (path.endsWith("/dashboard")) return json({ leagues: [league], pools: [], alerts: [], snapshots: [], news_sources: [], analysis_runs: [] });
    if (path.endsWith("/providers")) return json([{ id: 1, name: "Test analyst", model: "test", enabled: true, task_defaults: ["recommendation"] }]);
    if (path.endsWith("/players/directory") || path.endsWith("/roster")) return json(players);
    if (path.endsWith("/draft-sessions")) return json([]);
    if (path.endsWith("/weekly-lineup")) return json({
      mode: url.searchParams.get("mode") || "balanced", season: 2026, week: Number(url.searchParams.get("week") || 1),
      source: "Independent weekly model", partial_total: true, error: null, forecasts: report.forecasts,
      assignments: [{ slot: "RB", player_id: 2, score: 15, action: "Start", reason: null }],
      current_total: 10, projected_total: 15, projected_gain: 5, unfilled_slots: [], data_as_of: generated,
    });
    if (path.endsWith("/analysis-context")) return json({ teams: ["My Team", "Rival"], suggested_week: 1, schedule_available: true });
    if (path.endsWith("/analyses") && route.request().method() === "POST") {
      const input = route.request().postDataJSON();
      expect(input.team_name).toBe("My Team");
      expect(input.week).toBe(1);
      const run = { id: 1, league_id: leagueId, team_name: "My Team", season: 2026, week: 1, status: "queued", created_at: generated, completed_at: null, has_report: false, error: null };
      summaries.push(run);
      return json(run, 202);
    }
    if (path.endsWith("/analyses")) {
      if (url.searchParams.get("team_name") === "Rival") return json([]);
      if (summaries.length && ++polls > 1) Object.assign(summaries[0], { status: partial ? "partial" : "completed", has_report: true, completed_at: generated });
      return json(summaries);
    }
    if (path.endsWith("/analyses/1")) {
      if (partial) Object.assign(report.analysis, { status: "failed", output: null, error: "The analyst failed. Forecasts are saved; check provider settings and retry." });
      return json({ ...summaries[0], report, stale_reasons: ["Roster changed since this run."] });
    }
    if (path.endsWith("/games") || path.endsWith("/waivers") || path.endsWith("/pools") || path.endsWith("/news/sources")) return json([]);
    if (path.endsWith("/leagues")) return json([league]);
    if (path.endsWith(`/leagues/${leagueId}`)) return json(league);
    if (path.endsWith("/waivers/page")) return json({ items: [], total: 0, available: 0, next_offset: null, facets: { teams: [], statuses: [], positions: [] } });
    unexpected.push(`${route.request().method()} ${path}`);
    return json({ detail: `Missing test fixture for ${path}` }, 500);
  });
  return { unexpected, pageErrors };
}

test("run weekly analysis, compare source periods, and reopen saved report", async ({ page }) => {
  const mocked = await mockLeague(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Command center", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Weekly league analysis" })).toHaveCount(0);
  await page.goto("/leagues/1");
  await expect(page.getByRole("heading", { name: "Test League 1", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Lineup review", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Overview", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Forecast", exact: true })).toBeFocused();
  await expect(page).toHaveURL(/\/leagues\/1\?tab=forecast$/);
  await expect(page.getByRole("heading", { name: "Weekly league analysis" })).toBeVisible();
  await expect(page.getByLabel("Analysis league", { exact: true })).toHaveCount(0);
  const button = page.getByRole("button", { name: "Run league analysis", exact: true });
  const context = page.getByRole("group", { name: "Team and week for both league views" });
  await expect(context.getByRole("combobox", { name: "Fantasy team", exact: true })).toHaveValue("My Team");
  await expect(button).toBeEnabled();
  await button.click();
  await expect(page.getByText("You can leave this page", { exact: false })).toBeVisible();
  await expect(page.getByRole("heading", { name: "My Team · Week 1", exact: true })).toBeVisible({ timeout: 15000 });
  await page.getByText("Analyst briefing · recommendations and risks", { exact: true }).click();
  await expect(page.getByText("Prioritize the supported lineup improvement.")).toBeVisible();
  await expect(page.getByText("Recheck before acting.")).toBeVisible();
  const followup = new URL(await page.getByRole("link", { name: "Ask a follow-up about this report", exact: true }).getAttribute("href") || "", page.url());
  expect(Object.fromEntries(followup.searchParams)).toEqual({ league_id: "1", league_report_id: "1", team_name: "My Team", week: "1" });
  await expect(page.getByRole("article", { name: "Evaluate Waiver Target" })).toBeVisible();
  await expect(page.getByText("Not comparable", { exact: true })).toHaveCount(3);
  await expect(page.locator('a[href^="javascript:"]')).toHaveCount(0);
  await page.getByLabel("Players", { exact: true }).selectOption("available");
  await expect(page.locator(".weekly-table-wrap").getByText("Waiver Target", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByText("Analyst briefing · recommendations and risks", { exact: true }).click();
  await expect(page.getByText("Prioritize the supported lineup improvement.")).toBeVisible();
  await page.getByText("Sources, coverage, and forecast method", { exact: true }).click();
  await expect(page.getByText("No completed-game outcomes scored yet.", { exact: false })).toBeVisible();
  await expect(page.locator("body")).toHaveJSProperty("scrollWidth", await page.locator("body").evaluate((el) => el.clientWidth));
  await page.screenshot({ path: `/tmp/weekly-analysis-${test.info().project.name}.png`, fullPage: true });
  await context.getByRole("combobox", { name: "Fantasy team", exact: true }).selectOption("Rival");
  await expect(page.getByText("Prioritize the supported lineup improvement.")).toHaveCount(0);
  await page.getByRole("tab", { name: "Overview", exact: true }).click();
  await expect(page).toHaveURL(/\/leagues\/1\?team=Rival$/);
  await expect(page.getByRole("heading", { name: "Lineup review", exact: true })).toBeVisible();
  expect(mocked.unexpected).toEqual([]);
  expect(mocked.pageErrors).toEqual([]);
});

test("a direct Forecast link uses the open league and preserves advice on provider failure", async ({ page }) => {
  const mocked = await mockLeague(page, true, 2);
  await page.addInitScript(() => localStorage.setItem("analysis-league", "1"));
  await page.goto("/leagues/2?tab=forecast");
  await expect(page.getByRole("heading", { name: "Test League 2", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Forecast", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("group", { name: "Team and week for both league views" }).getByRole("combobox", { name: "Fantasy team", exact: true })).toHaveValue("My Team");
  await page.getByRole("button", { name: "Run league analysis", exact: true }).click();
  await expect(page.getByRole("heading", { name: "My Team · Week 1", exact: true })).toBeVisible({ timeout: 15000 });
  await page.getByText("Analyst briefing · recommendations and risks", { exact: true }).click();
  await expect(page.getByText("The analyst failed.", { exact: false })).toBeVisible({ timeout: 15000 });
  await expect(page.getByRole("article", { name: "Evaluate Waiver Target" })).toBeVisible();
  await page.getByText("Run a fresh analysis", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Run league analysis", exact: true })).toBeEnabled();
  expect(mocked.unexpected).toEqual([]);
  expect(mocked.pageErrors).toEqual([]);
});
