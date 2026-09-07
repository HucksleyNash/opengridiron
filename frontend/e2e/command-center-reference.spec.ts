import { expect, Page, test } from "@playwright/test";

async function mockCommandCenter(page: Page) {
  const league = { id: 1, name: "North Star", season: 2099, source: "yahoo_scrape", scoring: {}, roster_slots: ["QB"], player_count: 120 };
  const pool = { id: 1, name: "Sunday Pool", pool_type: "survivor", season: 2099, entry_count: 3, rules: { direction: "winner", basis: "straight_up", picks_per_week: 1, max_team_uses: 1, allowed_teams: [], blocked_teams: [], tie_result: "push", lock_mode: "game_start", confidence_weights: [], future_value_weight: 0.1 } };
  const game = { id: 1, season: 2099, week: 1, away_team: "NE", home_team: "SEA", kickoff: "2099-09-10T00:20:00Z", home_win_probability: 0.62, home_cover_probability: 0.51, spread_home: 3.5, total: 44.5, source: "nflverse", source_timestamp: "2099-09-01T12:00:00Z", win_probability_kind: "market", cover_probability_kind: "market" };

  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    if (path.endsWith("/onboarding/status")) return json({ configured: true, auth_required: false, environment: "test", timezone: "America/Chicago", capabilities: { draft_suite: true } });
    if (path.endsWith("/system/health")) return json({ status: "ok" });
    if (path.endsWith("/dashboard")) return json({
      leagues: [league],
      pools: [pool],
      alerts: [{ id: 1, title: "Practice injury report changed", message: "Availability moved after practice.", severity: "warning", read: false, created_at: "2099-09-01T12:00:00Z" }],
      snapshots: [{ id: 1, source: "yahoo_scrape", retrieved_at: "2099-09-01T12:00:00Z", status: "fresh" }, { id: 2, source: "nflverse.draft_model", retrieved_at: "2099-09-01T12:01:00Z", status: "fresh" }],
      analysis_runs: [],
    });
    if (path.endsWith("/games")) return json([game]);
    if (path.endsWith("/leagues/1/waivers")) return json([{ rank: 1, subject: "Josh Allen (QB, BUF)", expected_value: 196.1, confidence: 0.86, data_as_of: "2099-09-01T12:00:00Z" }]);
    return json({ detail: `Unhandled mock route: ${path}` });
  });
}

test("command center matches the approved week-and-intelligence composition", async ({ page }) => {
  await mockCommandCenter(page);
  await page.goto("/");

  await expect(page.locator(".dashboard-tape")).toContainText("NFL week 01");
  await expect(page.locator(".dashboard-tape")).toContainText("NE @ SEA");
  await expect(page.locator(".command-center-metrics")).toContainText("Next kickoff");
  await expect(page.getByRole("heading", { name: "Top available value" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Source status" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Week 1 board" })).toBeVisible();

  const wire = await page.locator(".command-center-wire").boundingBox();
  const intel = await page.locator(".command-center-intel").boundingBox();
  expect(wire).not.toBeNull();
  expect(intel).not.toBeNull();
  if (page.viewportSize()!.width >= 1100) {
    expect(intel!.x).toBeGreaterThan(wire!.x);
  } else {
    expect(intel!.y).toBeGreaterThanOrEqual(wire!.y + wire!.height);
  }
});
