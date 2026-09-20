import { expect, Page, test } from "@playwright/test";

const defaultGame = { id: 1, season: 2099, week: 1, away_team: "NE", home_team: "SEA", kickoff: "2099-09-10T00:20:00Z", home_win_probability: 0.62, home_cover_probability: 0.51, spread_home: 3.5, total: 44.5, source: "nflverse", source_timestamp: "2099-09-01T12:00:00Z", win_probability_kind: "market", cover_probability_kind: "market" };
type BoardGame = Omit<typeof defaultGame, "spread_home" | "total"> & { spread_home: number | null; total: number | null; home_score?: number | null; away_score?: number | null; completed?: boolean };

async function mockCommandCenter(page: Page, games: BoardGame[] = [defaultGame], refreshedGames?: BoardGame[]) {
  const league = { id: 1, name: "North Star", season: 2099, source: "yahoo_scrape", scoring: {}, roster_slots: ["QB"], player_count: 120 };
  const pool = { id: 1, name: "Sunday Pool", pool_type: "survivor", season: 2099, entry_count: 3, rules: { direction: "winner", basis: "straight_up", picks_per_week: 1, max_team_uses: 1, allowed_teams: [], blocked_teams: [], tie_result: "push", lock_mode: "game_start", confidence_weights: [], future_value_weight: 0.1 } };
  let currentGames = games;
  let scoreChecks = 0;

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
    if (path.endsWith("/games")) return json(currentGames);
    if (path.endsWith("/sync/nflverse/schedule")) return json({ status: "completed", updated: currentGames.length, created: 0 });
    if (path.endsWith("/sync/live-scores")) {
      scoreChecks += 1;
      if (scoreChecks > 1) currentGames = refreshedGames || currentGames;
      return json({ status: "completed", updated: currentGames.length, matched: currentGames.length });
    }
    if (path.endsWith("/integrations/yahoo/scraper/sync")) return json({ status: "completed" });
    if (path.endsWith("/news/sources") || path.endsWith("/data-sources")) return json([]);
    return json({ detail: `Unhandled mock route: ${path}` });
  });
}

test("command center matches the approved week-and-intelligence composition", async ({ page }) => {
  await mockCommandCenter(page);
  const marketRequests: string[] = [];
  page.on("request", (request) => { if (request.url().includes("/waivers")) marketRequests.push(request.url()); });
  await page.goto("/");

  await expect(page.locator(".dashboard-tape")).toContainText("NFL week 01");
  await expect(page.locator(".dashboard-tape")).toContainText("NE @ SEA");
  await expect(page.locator(".command-center-metrics")).toContainText("Next kickoff");
  await expect(page.getByText("Player market", { exact: true })).toHaveCount(0);
  expect(marketRequests).toEqual([]);
  await expect(page.getByRole("heading", { name: "Source status" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Week 1 board" })).toBeVisible();

  const wire = await page.locator(".command-center-wire").boundingBox();
  const intel = await page.locator(".command-center-intel").boundingBox();
  expect(wire).not.toBeNull();
  expect(intel).not.toBeNull();
  if (page.viewportSize()!.width > 1100) {
    expect(intel!.x).toBeGreaterThan(wire!.x);
  } else {
    expect(intel!.y).toBeGreaterThanOrEqual(wire!.y + wire!.height);
  }
});

// Synthetic slate: Thursday results, Sunday games, and Monday's final kickoff.
function weeklyGames(): BoardGame[] {
  const teams = ["ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC", "LAC", "LA", "LV", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS"];
  return Array.from({ length: 16 }, (_, index) => ({
    ...defaultGame, id: index + 1, week: 2,
    away_team: teams[index * 2], home_team: teams[index * 2 + 1],
    kickoff: index === 0 ? "2026-09-18T00:20:00Z" : index === 15 ? "2026-09-22T00:15:00Z" : index < 4 ? "2026-09-20T17:00:00Z" : "2026-09-20T20:25:00Z",
    away_score: index === 0 ? 0 : index === 1 ? 17 : index === 3 ? 7 : null,
    home_score: index === 0 ? 24 : index === 1 ? 23 : index === 3 ? 10 : null,
    completed: index < 2,
  }));
}

test("week board shows the entire slate with available scores and honest result states", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-09-20T20:00:00Z"));
  await mockCommandCenter(page, weeklyGames().reverse());
  await page.goto("/");

  const games = page.locator(".command-game");
  await expect(games).toHaveCount(16);
  const final = games.filter({ hasText: "ARI" });
  await expect(final.getByLabel("ARI 0, ATL 24")).toBeVisible();
  await expect(final).toContainText("Final");
  await expect(final).not.toContainText("Pregame");
  await expect(games.filter({ hasText: "BAL" }).getByLabel("BAL 17, BUF 23")).toBeVisible();
  const pending = games.filter({ hasText: "CAR" });
  await expect(pending).toContainText("Score pending");
  await expect(pending.locator(".command-game-score")).toHaveCount(0);
  const started = games.filter({ hasText: "CIN" });
  await expect(started.getByLabel("CIN 7, CLE 10")).toBeVisible();
  await expect(started).toContainText("Latest score");
  const monday = games.filter({ hasText: "TEN" });
  await expect(monday).toContainText("WAS");
  await expect(monday.locator("time")).toBeVisible();
  await expect(monday.locator(".command-game-score")).toHaveCount(0);
  await expect(page.getByText("Scores refresh every 30 seconds while this tab is visible.", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("week board tolerates unpublished betting lines and missing results", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-09-20T20:00:00Z"));
  await mockCommandCenter(page, [{ ...weeklyGames()[2], spread_home: null, total: null }]);
  await page.goto("/");
  const game = page.locator(".command-game");
  await expect(game).toContainText("Score pending");
  await expect(game.locator("time")).toBeVisible();
  await expect(game).not.toContainText("Pregame");
});

test("refresh sources pulls the latest Game Pulse scores", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-09-20T20:00:00Z"));
  const pending = { ...weeklyGames()[2], away_score: null, home_score: null, completed: false };
  const live = { ...pending, away_score: 10, home_score: 14 };
  await mockCommandCenter(page, [pending], [live]);
  await page.goto("/");

  const game = page.locator(".command-game");
  await expect(game).toContainText("Score pending");
  await page.getByRole("button", { name: "Refresh sources" }).click();
  await expect(game.getByLabel("CAR 10, CHI 14")).toBeVisible();
  await expect(game).toContainText("Latest score");
  await expect(page.getByRole("status")).toContainText("live NFL scores");
});

for (const completed of [false, true]) {
  test(`week board keeps Monday's slate after its final kickoff (completed=${completed})`, async ({ page }) => {
    await page.clock.setFixedTime(new Date("2026-09-22T04:00:00Z")); // Monday, 11pm Central.
    const games = weeklyGames().map((game) => ({ ...game, completed, away_score: completed ? 14 : null, home_score: completed ? 21 : null }));
    const nextWeek = { ...defaultGame, id: 17, week: 3, kickoff: "2026-09-25T00:20:00Z" };
    await mockCommandCenter(page, [...games, nextWeek]);
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Week 2 board" })).toBeVisible();
    await expect(page.locator(".command-game")).toHaveCount(16);
    await expect(page.locator(".dashboard-tape")).toContainText("NFL week 02");
  });
}

test("week board advances to the upcoming slate on Tuesday even if past results are pending", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-09-22T06:00:00Z")); // Tuesday, 1am Central.
  await mockCommandCenter(page, [...weeklyGames(), { ...defaultGame, id: 17, week: 3, kickoff: "2026-09-25T00:20:00Z" }]);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Week 3 board" })).toBeVisible();
  await expect(page.locator(".command-game")).toHaveCount(1);
});

test("week board keeps the final slate when the season has ended", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2027-03-01T20:00:00Z"));
  await mockCommandCenter(page, [{ ...weeklyGames()[0], week: 18 }]);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Week 18 board" })).toBeVisible();
  await expect(page.getByLabel("ARI 0, ATL 24")).toBeVisible();
});

test("week board explains an empty schedule", async ({ page }) => {
  await mockCommandCenter(page, []);
  await page.goto("/");
  await expect(page.getByText("No games loaded for this week.")).toBeVisible();
});
