import { expect, test, type Page } from "@playwright/test";

const player = { id: 7, league_id: 1, name: "Patrick Mahomes", pro_team: "KC", position: "QB", status: "Active", rostered_by: "My Team", current_slot: "QB", ownership: "TEAM", projected_points: 20, floor: 15, ceiling: 28, ros_value: null, risk: 0.3 };
const other = { ...player, id: 8, name: "Josh Allen", pro_team: "BUF", current_slot: "BN" };
const date = "2026-09-20T12:00:00Z";
const value = (source: string, points: number | null, state = "available", warnings: string[] = []) => ({ source, points, state, warnings, reason: points === null ? "No eligible pregame projection was saved." : null, captured_at: points === null ? null : date });

function pointsData(id = 7) {
  return {
    player_id: id, league_id: 1, league_name: "Weekly points league", season: 2026, current_week: 3,
    generated_at: date, scoring: { passing_yards: 0.04, passing_tds: 4 },
    sources: [{ name: "NFL statistics 2026", status: "available", received_at: date }],
    weeks: Array.from({ length: 18 }, (_, index) => ({
      week: index + 1, state: index === 9 ? "bye" : index < 2 ? "final" : "upcoming", opponent: index === 9 ? null : "CHI", home: index % 2 === 0, kickoff: date,
      opengridiron: index === 9 ? value("Open Gridiron", null, "bye") : value("Open Gridiron", index === 1 ? null : id === 7 ? 12.5 : 25.5, index === 1 ? "not_saved" : "available"),
      yahoo: index === 9 ? value("Yahoo", null, "bye") : value("Yahoo", index < 3 ? 13.8 : null, index < 3 ? "available" : "not_published", index === 0 ? ["Yahoo scoring unverified: the saved table copies local scoring settings."] : []),
      actual: index === 9 ? value("NFLverse · league scoring", null, "bye") : value("NFLverse · league scoring", index < 2 ? index === 0 ? 0 : -2 : null, index < 2 ? "available" : "pending"),
    })),
  };
}

async function mock(page: Page, options: { failInitial?: boolean; failRefresh?: boolean; delay?: number; unavailable?: boolean } = {}) {
  const requests: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (path.endsWith("/onboarding/status")) return json({ configured: true, auth_required: false, capabilities: { draft_suite: true } });
    if (path.endsWith("/system/health")) return json({ status: "ok" });
    if (path.endsWith("/leagues/1")) return json({ id: 1, name: "Weekly points league", season: 2026, source: "yahoo_scrape", scoring: {}, roster_slots: ["QB", "BN"], player_count: 2 });
    if (path.endsWith("/players/directory") || path.endsWith("/roster")) return json([player, other]);
    if (path.endsWith("/weekly-lineup")) return json({ season: 2026, week: 3, assignments: [], forecasts: [], current_total: 20, projected_total: 25, projected_gain: 5, unfilled_slots: [], mode: "balanced" });
    if (path.endsWith("/waivers/page")) return json({ items: [], total: 0, available: 0, facets: { teams: [], positions: [], statuses: [] } });
    if (path.endsWith("/analysis-context")) return json({ teams: ["My Team"], suggested_week: 3, schedule_available: true });
    if (/\/players\/[78]$/.test(path)) return json(path.endsWith("/7") ? player : other);
    if (path.endsWith("/synopsis")) return json({ player: path.includes("/7/") ? player : other, synopsis: "Player information", sources: [], injury_reports: [], articles: [] });
    if (path.endsWith("/points")) {
      requests.push(path + url.search);
      if (options.delay) await new Promise((resolve) => setTimeout(resolve, options.delay));
      if (options.failInitial || options.failRefresh && url.searchParams.has("refresh")) return json({ detail: "Points source unavailable" }, 503);
      const data = pointsData(path.includes("/7/") ? 7 : 8);
      if (options.unavailable) {
        data.sources[0].status = "unavailable";
        data.scoring = {} as typeof data.scoring;
        data.weeks = data.weeks.map((week) => ({ ...week, state: "unknown", opponent: null,
          actual: value("NFLverse · league scoring", null, "unavailable"), opengridiron: value("Open Gridiron", null, "unavailable"), yahoo: value("Yahoo", null, "unavailable") }));
      }
      return json(data);
    }
    return json([]);
  });
  return requests;
}

async function openPlayer(page: Page, name = player.name) {
  const trigger = page.getByRole("region", { name: "Team roster 2", exact: true }).getByRole("button", { name: `View ${name} synopsis` });
  await trigger.click();
  return page.getByRole("dialog", { name });
}

test("Points loads on demand and shows both projections, actual zeroes, missing history and all weeks", async ({ page }, testInfo) => {
  const requests = await mock(page, { delay: 250 });
  await page.goto("/leagues/1");
  const dialog = await openPlayer(page);
  expect(requests).toHaveLength(0);
  await dialog.getByRole("tab", { name: "Overview", exact: true }).press("ArrowRight");
  await dialog.getByRole("tab", { name: "Projections", exact: true }).press("ArrowRight");
  await expect(dialog.getByRole("tab", { name: "Points", exact: true })).toBeFocused();
  await expect(dialog.getByText("Loading weekly projections and actual points…")).toBeVisible();
  const table = dialog.getByRole("table", { name: "Weekly projected and actual fantasy points" });
  await expect(table.locator("tbody tr")).toHaveCount(18);
  await expect(dialog.getByText("Weekly points league · 2026 season · League scoring")).toBeVisible();
  await expect(table.locator("tbody tr").first()).toContainText("12.5");
  await expect(table.locator("tbody tr").first()).toContainText("13.8");
  await expect(table.locator("tbody tr").first()).toContainText("0.0");
  await expect(table.locator("tbody tr").nth(1)).toContainText("Not saved");
  await expect(table.locator("tbody tr").nth(1)).toContainText("-2.0");
  await expect(table.locator("tbody tr").nth(9)).toContainText("Bye");
  await expect(table.locator("tbody tr").last()).toContainText("18");
  // StrictMode may cancel and restart the first request. Revisiting a loaded
  // tab must reuse its cache without requesting the data again.
  const initialRequests = requests.length;
  expect(initialRequests).toBeGreaterThan(0);
  expect(requests.every((path) => path === "/api/v1/players/7/points")).toBe(true);
  await dialog.getByRole("tab", { name: "Overview", exact: true }).click();
  await dialog.getByRole("tab", { name: "Points", exact: true }).click();
  await expect(table.locator("tbody tr")).toHaveCount(18);
  expect(requests).toHaveLength(initialRequests);
  expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await dialog.screenshot({ path: testInfo.outputPath("player-points.png") });
  await dialog.press("Escape");
  await expect(page.getByRole("region", { name: "Team roster 2", exact: true }).getByRole("button", { name: "View Patrick Mahomes synopsis" })).toBeFocused();
});

test("Points retries initial failure and retains loaded values when a refresh fails", async ({ page }) => {
  const failures = { failInitial: true, failRefresh: true };
  await mock(page, failures);
  await page.goto("/leagues/1");
  const dialog = await openPlayer(page);
  await dialog.getByRole("tab", { name: "Points", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("Points source unavailable");
  failures.failInitial = false;
  await dialog.getByRole("button", { name: "Try again" }).click();
  await expect(dialog.locator(".player-points-table tbody tr")).toHaveCount(18);
  await dialog.getByRole("button", { name: "Refresh points" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Points source unavailable");
  await expect(dialog.locator(".player-points-table tbody tr").first()).toContainText("12.5");
});

test("Points source warnings open their evidence and switching players isolates cached data", async ({ page }) => {
  await mock(page);
  await page.goto("/leagues/1");
  let dialog = await openPlayer(page);
  await dialog.getByRole("tab", { name: "Points", exact: true }).click();
  await dialog.getByRole("button", { name: "Yahoo week 1: review scoring or source warning" }).click();
  await expect(dialog.getByText("Yahoo scoring unverified: the saved table copies local scoring settings.")).toBeVisible();
  await expect(dialog.locator("#player-points-evidence-1")).toBeFocused();
  await expect(page).not.toHaveURL(/#/);
  await dialog.getByText("Weekly point details", { exact: true }).click();
  await expect(dialog.locator("#player-points-evidence-1")).not.toBeVisible();
  await dialog.getByText("Weekly point details", { exact: true }).press("Tab");
  await expect(dialog.getByRole("button", { name: "Close player synopsis" })).toBeFocused();
  await dialog.getByRole("button", { name: "Close player synopsis" }).press("Shift+Tab");
  await expect(dialog.getByText("Weekly point details", { exact: true })).toBeFocused();
  await dialog.press("Escape");
  dialog = await openPlayer(page, other.name);
  await dialog.getByRole("tab", { name: "Points", exact: true }).click();
  await expect(dialog.locator(".player-points-table tbody tr").first()).toContainText("25.5");
  await expect(dialog.locator(".player-points-table tbody tr").first()).not.toContainText("12.5");
});

test("Points explains missing schedule, scoring and unavailable sources", async ({ page }) => {
  await mock(page, { unavailable: true });
  await page.goto("/leagues/1");
  const dialog = await openPlayer(page);
  await dialog.getByRole("tab", { name: "Points", exact: true }).click();
  await expect(dialog.getByText("No verified weekly schedule is available for this player.")).toBeVisible();
  await expect(dialog.getByText("Configure this league’s scoring to calculate actual points and independent forecasts.")).toBeVisible();
  await expect(dialog.locator(".player-points-table tbody tr")).toHaveCount(18);
  await expect(dialog.locator(".player-points-table")).not.toContainText("0.0");
});
