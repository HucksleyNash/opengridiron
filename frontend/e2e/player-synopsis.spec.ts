import { expect, test, type Page } from "@playwright/test";

const player = { id: 7, league_id: 1, name: "Patrick Mahomes", pro_team: "KC", position: "QB", status: "Questionable", rostered_by: "My Team", current_slot: "QB", ownership: "TEAM", projected_points: 20, floor: 15, ceiling: 28, ros_value: null, risk: 0.3,
  projection: { period: "week", season: 2026, week: 1, source: "Test projection", source_updated_at: "2026-09-03T12:00:00Z" } };
const bench = { ...player, id: 8, name: "Josh Allen", pro_team: "BUF", current_slot: "BN", projected_points: 25, status: "Active" };
const date = "2026-09-03T12:00:00Z";
function synopsis(selected: typeof player, empty = false, partial = false) {
  return { player: selected, synopsis: `${selected.name} is a ${selected.position} for ${selected.pro_team}. Imported fantasy status: ${selected.status}.`, news_window_days: 30,
    injury_reports: empty ? [] : [{ player_name: selected.name, team: "Chiefs", injury: "Ankle", practice_status: "Limited participation", game_status: "Questionable", report_period: "2026 NFL Injury Report · Injuries - WEEK 1", url: "https://www.nfl.com/injuries/", retrieved_at: date }],
    articles: empty ? [] : [{ title: `${selected.name} injury update`, excerpt: "An attributed practice update.", source: "Test Sports", category: "injury", url: "https://example.com/injury", published_at: date, retrieved_at: date }, { title: `${selected.name} prepares for opener`, excerpt: "", source: "Team newsroom", category: "news", url: "https://example.com/news", published_at: null, retrieved_at: date }],
    sources: [{ name: "NFL injury report", url: "https://www.nfl.com/injuries/", status: partial ? "stale" : "ok", checked_at: date, fetched_at: date, message: partial ? "Refresh failed; showing previously retrieved reports." : null }, { name: "Google News", url: "https://news.google.com", status: partial ? "unavailable" : "ok", checked_at: date, fetched_at: partial ? null : date, message: null }],
  };
}

async function mockWorkspace(page: Page, options: { fail?: boolean; empty?: boolean; partial?: boolean; delay?: number } = {}) {
  const requests: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    const path = url.pathname;
    if (path.endsWith("/onboarding/status")) return json({ configured: true, auth_required: false, environment: "test", capabilities: { draft_suite: true } });
    if (path.endsWith("/system/health")) return json({ status: "ok" });
    if (path.endsWith("/leagues/1")) return json({ id: 1, name: "Player synopsis checks", season: 2026, source: "manual", scoring: {}, roster_slots: ["QB", "BN"], player_count: 2 });
    if (path.endsWith("/roster")) return json([player, bench]);
    if (path.endsWith("/weekly-lineup")) return json({ season: 2026, week: 1, assignments: [{ player_id: bench.id, slot: "QB", score: 25 }], forecasts: [{ player_id: player.id, points: 20 }, { player_id: bench.id, points: 25 }], current_total: 20, projected_total: 25, projected_gain: 5, unfilled_slots: [], mode: "balanced" });
    if (path.endsWith("/waivers/page")) return json({ items: [], total: 0, available: 0, next_offset: null, facets: { teams: [], statuses: [], positions: [] } });
    if (path.endsWith("/synopsis")) {
      requests.push(url.pathname + url.search);
      if (options.delay) await new Promise((resolve) => setTimeout(resolve, options.delay));
      if (options.fail) return json({ detail: "Reports temporarily unavailable" }, 503);
      return json(synopsis(path.includes("/7/") ? player : bench, options.empty, options.partial));
    }
    return json([]);
  });
  return requests;
}

test("roster and lineup open the correct player, preserve context and restore keyboard focus", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const requests = await mockWorkspace(page, { delay: 250 });
  await page.goto("/leagues/1");
  const roster = page.getByRole("region", { name: "Current roster", exact: true });
  const trigger = roster.getByRole("button", { name: "View Patrick Mahomes synopsis" });
  await trigger.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Patrick Mahomes" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("Loading injury reports and recent articles…")).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Official injury report" })).toBeVisible();
  await expect(dialog.getByText("Ankle", { exact: true })).toBeVisible();
  await dialog.getByRole("tab", { name: "News & sources" }).click();
  await expect(dialog.getByRole("link", { name: "Patrick Mahomes injury update" })).toHaveAttribute("href", "https://example.com/injury");
  await expect(dialog.getByRole("link", { name: "Patrick Mahomes prepares for opener" })).toHaveAttribute("target", "_blank");
  await expect(dialog.getByText(/Publication date not supplied/)).toBeVisible();
  const bounds = await dialog.boundingBox();
  expect(bounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await dialog.screenshot({ path: `/private/tmp/player-synopsis-${test.info().project.name}.png` });
  const close = dialog.getByRole("button", { name: "Close player synopsis" });
  await close.focus();
  await page.keyboard.press("Shift+Tab");
  expect(await dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  await expect(page.getByRole("combobox", { name: "Fantasy team", exact: true })).toHaveValue("My Team");
  const recommended = page.getByRole("region", { name: "Recommended starters" });
  await recommended.getByRole("button", { name: "View Josh Allen synopsis" }).click();
  const second = page.getByRole("dialog", { name: "Josh Allen" });
  await second.getByRole("tab", { name: "News & sources" }).click();
  await expect(second.getByRole("link", { name: "Josh Allen injury update" })).toBeVisible();
  await expect(second.getByText("Patrick Mahomes", { exact: true })).toHaveCount(0);
  await second.getByRole("button", { name: "Refresh reports", exact: true }).click();
  await expect.poll(() => requests.some((url) => url.endsWith("/8/synopsis?refresh=true"))).toBe(true);
  await second.getByRole("button", { name: "Close player synopsis" }).click();
  await roster.locator("summary").filter({ hasText: "Bench" }).click();
  await roster.getByRole("button", { name: "View Josh Allen synopsis" }).click();
  await expect(page.getByRole("dialog", { name: "Josh Allen" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("failures can retry and empty coverage does not claim the player is healthy", async ({ page }) => {
  const options = { fail: true, empty: true };
  await mockWorkspace(page, options);
  await page.goto("/leagues/1");
  await page.getByRole("region", { name: "Current roster", exact: true }).getByRole("button", { name: "View Patrick Mahomes synopsis" }).click();
  const dialog = page.getByRole("dialog", { name: "Patrick Mahomes" });
  await expect(dialog.getByRole("alert")).toContainText("Reports temporarily unavailable");
  await expect(dialog.getByRole("heading", { name: "Player overview" })).toBeVisible();
  options.fail = false;
  await dialog.getByRole("button", { name: "Try again" }).click();
  await expect(dialog.getByText(/This does not confirm that the player is healthy/)).toBeVisible();
  await dialog.getByRole("tab", { name: "News & sources" }).click();
  await expect(dialog.getByText("No matching injury articles found in the last 30 days.")).toBeVisible();
  await expect(dialog.getByText("No other recent articles matched this player.")).toBeVisible();
});

test("partial source failures retain and label previously fetched reports", async ({ page }) => {
  await mockWorkspace(page, { partial: true });
  await page.goto("/leagues/1");
  await page.getByRole("region", { name: "Current roster", exact: true }).getByRole("button", { name: "View Patrick Mahomes synopsis" }).click();
  const dialog = page.getByRole("dialog", { name: "Patrick Mahomes" });
  await expect(dialog.getByText("Some sources are unavailable or out of date. Available reports are shown below.")).toBeVisible();
  await expect(dialog.getByText("Previously retrieved report; current status could not be verified.")).toBeVisible();
  await expect(dialog.getByText("Ankle", { exact: true })).toBeVisible();
  await dialog.getByRole("tab", { name: "News & sources" }).click();
  await expect(dialog.getByRole("link", { name: "Patrick Mahomes injury update" })).toBeVisible();
});
