import { expect, Page, test } from "@playwright/test";

async function mockDesignState(page: Page) {
  const rules = {
    direction: "loser",
    basis: "straight_up",
    picks_per_week: 1,
    max_team_uses: 1,
    allowed_teams: [],
    blocked_teams: [],
    tie_result: "push",
    lock_mode: "game_start",
    confidence_weights: [],
    future_value_weight: 0.1,
  };
  const pool = {
    id: 1,
    name: "Sunday Loser Pool",
    season: 2026,
    pool_type: "survivor",
    rules,
    suggested_week: 1,
    inactive_entry_count: 0,
    schedule: { state: "ready", source: "nflverse.schedule", last_success_at: "2026-09-08T12:00:00Z" },
    entries: [{ id: 11, name: "Main entry", active: true, card_state: "draft", required_count: 1, selection_count: 0, weight_count: null, missing_count: 1 }],
  };
  const game = {
    id: 31,
    source_game_key: "2026091001",
    source_game_key_kind: "gsis",
    kickoff: "2026-09-10T00:20:00Z",
    locked: false,
    away_team: "GB",
    home_team: "CHI",
    probabilities: { home_win: 0.68, win_kind: "market", home_cover: 0.53, cover_kind: "market" },
    recommendations: [
      { team: "CHI", score: 0.64, probability: 0.68, rationale: ["Estimated win probability 68%"] },
      { team: "GB", score: 0.28, probability: 0.32, rationale: ["Estimated win probability 32%"] },
    ],
  };

  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    if (path.endsWith("/onboarding/status")) return json({ configured: true, auth_required: false, environment: "test", timezone: "America/Chicago", capabilities: { draft_suite: true } });
    if (path.endsWith("/providers") || path.endsWith("/players/directory")) return json([]);
    if (path.endsWith("/standings")) return json({ entries: [], pending_games: 0 });
    if (path.endsWith("/strategy")) return json({ status: "unavailable", reasons: ["Fixture has no future schedule"], recommendations: [] });
    if (path.endsWith("/system/health")) return json({ status: "ok" });
    if (path.endsWith("/pools/overview")) return json({ generated_at: "2026-09-09T12:00:00Z", pools: [pool] });
    if (path.endsWith("/pools/1/entries")) return json([{ id: 11, pool_id: 1, name: "Main entry", active: true }]);
    if (path.endsWith("/pools/1/weeks/1")) return json({
      pool: { id: 1, name: pool.name, season: 2026, pool_type: pool.pool_type, entry_count: 1, rules },
      week: { number: 1, suggested_week: 1, first_kickoff: game.kickoff, last_kickoff: game.kickoff },
      schedule: pool.schedule,
      entry: { id: 11, pool_id: 1, name: "Main entry", active: true, read_only: false },
      card: { version: 0, state: "draft", required_count: 1, selection_count: 0, weight_count: null, missing_count: 1, picks: [], findings: [] },
      games: [game],
      survivor_slots: [{ slot: 1, current_pick: null, choices: [{ game_id: 31, team: "GB", eligible: true, reason: null }, { game_id: 31, team: "CHI", eligible: true, reason: null }] }],
      configuration_errors: [],
    });
    return json({ detail: `Unhandled mock route: ${path}` });
  });
}

test("mobile navigation keeps primary labels visible and secondary destinations under More", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "mobile-only navigation assertion");
  await mockDesignState(page);
  await page.goto("/pools/1/weeks/1?entry_id=11");

  const mobileNav = page.locator(".mobile-nav");
  await expect(mobileNav.getByText("Home", { exact: true })).toBeVisible();
  await expect(mobileNav.getByText("Leagues", { exact: true })).toBeVisible();
  await expect(mobileNav.getByText("Draft", { exact: true })).toBeVisible();
  await expect(mobileNav.getByText("Pools", { exact: true })).toBeVisible();
  await mobileNav.locator("summary").click();
  await expect(mobileNav.getByText("News wire", { exact: true })).toBeVisible();
  await expect(mobileNav.getByText("Analyst desk", { exact: true })).toBeVisible();
  await expect(mobileNav.getByText("Settings", { exact: true })).toBeVisible();
});

test("survivor autosave status omits confidence-only weight counts", async ({ page }) => {
  await mockDesignState(page);
  await page.goto("/pools/1/weeks/1?entry_id=11");
  await expect(page.getByText("0/1 selection", { exact: true })).toBeVisible();
  await expect(page.locator(".autosave-bar")).not.toContainText("weights");
});
