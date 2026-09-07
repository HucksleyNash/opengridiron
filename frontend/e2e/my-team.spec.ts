import { expect, test, type BrowserContext, type Page } from "@playwright/test";

function fixtures() {
  return [1, 2].map((id) => ({ id, name: `League ${id}`, season: 2026, source: "manual", scoring: {}, roster_slots: ["RB", "BN"], player_count: 2,
    my_team_name: null as string | null, team_names: ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel"] }));
}

async function mockApi(context: BrowserContext, leagues: ReturnType<typeof fixtures>, failures = { save: false }) {
  await context.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path.endsWith("/onboarding/status")) return json({ configured: true, auth_required: false, capabilities: { draft_suite: true } });
    if (path.endsWith("/system/health")) return json({ status: "ok" });
    if (path.endsWith("/leagues")) return json(leagues);
    if (path.endsWith("/dashboard")) return json({ leagues, pools: [], alerts: [], snapshots: [], news_sources: [], analysis_runs: [] });
    const league = leagues.find((item) => path.startsWith(`/api/v1/leagues/${item.id}/`) || path === `/api/v1/leagues/${item.id}`);
    if (league) {
      if (path.endsWith("/my-team")) {
        expect(route.request().method()).toBe("PUT");
        if (failures.save) return json({ detail: "Save unavailable. Please try again." }, 503);
        league.my_team_name = route.request().postDataJSON().my_team_name;
        return json(league);
      }
      if (path.endsWith(`/leagues/${league.id}`)) return json(league);
      if (path.endsWith("/roster")) return json(["Alpha", "Bravo"].map((name, index) => ({ id: index + 1, league_id: league.id, name: `${name} Runner`, pro_team: "CHI", position: "RB", status: "Active", ownership: name, rostered_by: name, current_slot: "RB", projected_points: 10, floor: 5, ceiling: 15, risk: 0.5 })));
      if (path.endsWith("/analysis-context")) return json({ teams: ["Alpha", "Bravo"], suggested_week: 1, schedule_available: true });
      if (path.endsWith("/weekly-lineup")) return json({ mode: "balanced", season: 2026, week: 1, forecasts: [], assignments: [], current_total: null, projected_total: null, projected_gain: null, unfilled_slots: ["RB"], partial_total: true, limitations: [], error: null });
      if (path.endsWith("/waivers/page")) return json({ items: [], total: 0, available: 0, next_offset: null, facets: { teams: [], statuses: [], positions: [] } });
    }
    return json([]);
  });
}

const workingTeam = (page: Page) => page.getByRole("group", { name: "Team and week for both league views" }).getByRole("combobox", { name: "Fantasy team", exact: true });
const savedTeam = (page: Page) => page.locator(".league-team-setting > summary");
async function teamSetting(page: Page) {
  const setting = page.locator(".league-team-setting");
  if (await setting.getAttribute("open") === null) await setting.locator("summary").click();
  return page.getByRole("combobox", { name: "My team", exact: true });
}
async function chooseMyTeam(page: Page, name: string) {
  await (await teamSetting(page)).selectOption(name);
  await expect(savedTeam(page)).toContainText(`My team: ${name || "Not set"}`);
}

test("my team is saved per league across navigation, reloads, Forecast and new drafts", async ({ page, context, browser }) => {
  const leagues = fixtures();
  await mockApi(context, leagues);
  await page.goto("/leagues/1");
  await chooseMyTeam(page, "Bravo");
  await expect(workingTeam(page)).toHaveValue("Bravo");
  await workingTeam(page).selectOption("Alpha");
  await page.reload();
  await expect(workingTeam(page)).toHaveValue("Alpha");
  await expect(savedTeam(page)).toContainText("My team: Bravo");
  await page.getByRole("tab", { name: "Forecast", exact: true }).click();
  await expect(workingTeam(page)).toHaveValue("Alpha");
  await chooseMyTeam(page, "Alpha");
  await expect(workingTeam(page)).toHaveValue("Alpha");
  await chooseMyTeam(page, "Bravo");
  await expect(workingTeam(page)).toHaveValue("Bravo");
  await page.screenshot({ path: `/tmp/my-team-${test.info().project.name}.png`, fullPage: true });
  await expect(page.locator("body")).toHaveJSProperty("scrollWidth", await page.locator("body").evaluate((el) => el.clientWidth));
  await page.goto("/leagues/2");
  await expect(savedTeam(page)).toContainText("My team: Not set");
  await chooseMyTeam(page, "Alpha");
  await page.goto("/leagues/1");
  await expect(savedTeam(page)).toContainText("My team: Bravo");
  await page.getByRole("link", { name: "Open draft room", exact: true }).click();
  await expect(page.getByLabel("Your slot", { exact: true })).toHaveValue("2");
  const freshContext = await browser.newContext({ serviceWorkers: "block" });
  try {
    await mockApi(freshContext, leagues);
    const freshPage = await freshContext.newPage();
    await freshPage.goto("http://127.0.0.1:4173/leagues/1?tab=forecast");
    await expect(savedTeam(freshPage)).toContainText("My team: Bravo");
    await expect(workingTeam(freshPage)).toHaveValue("Bravo");
  } finally { await freshContext.close(); }
});

test("failed saves retain the existing choice and can be retried or cleared", async ({ page, context }) => {
  const leagues = fixtures();
  leagues[0].my_team_name = "Bravo";
  const failures = { save: true };
  await mockApi(context, leagues, failures);
  await page.goto("/leagues/1");
  await (await teamSetting(page)).selectOption("Alpha");
  await expect(page.getByRole("alert").filter({ hasText: "Could not save your team" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "My team", exact: true })).toHaveValue("Bravo");
  failures.save = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(savedTeam(page)).toContainText("My team: Alpha");
  await chooseMyTeam(page, "");
  await expect(page.getByText("My team cleared.", { exact: true })).toBeVisible();
  await page.reload();
  await expect(savedTeam(page)).toContainText("My team: Not set");
});

test("an unavailable saved team is explicit and never silently replaced", async ({ page, context }) => {
  const leagues = fixtures();
  leagues[0].my_team_name = "Old team name";
  await mockApi(context, leagues);
  await page.goto("/leagues/1");
  await expect(page.getByText("Your saved team is no longer in the imported teams.", { exact: false })).toBeVisible();
  await expect(workingTeam(page)).toHaveValue("");
  await page.getByRole("tab", { name: "Forecast", exact: true }).click();
  await expect(page.getByRole("button", { name: "Run league analysis", exact: true })).toBeDisabled();
  await chooseMyTeam(page, "Bravo");
  await expect(workingTeam(page)).toHaveValue("Bravo");
});
