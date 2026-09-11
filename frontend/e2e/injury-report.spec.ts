import { expect, test, type Page } from "@playwright/test";

const date = "2026-09-09T16:00:00Z";
const game = { id: 1, season: 2026, week: 1, home_team: "BUF", away_team: "KC", kickoff: "2026-09-13T18:00:00Z" };
const sources = [{ name: "NFL injury report", url: "https://www.nfl.com/injuries/", status: "ok", fetched_at: date, checked_at: date }, { name: "Sleeper player status", url: "https://api.sleeper.app/v1/players/nfl", status: "ok", fetched_at: date, checked_at: date }];
const report = { player_name: "Patrick Mahomes", team: "Chiefs", injury: "Ankle", practice_status: "Limited Participation", game_status: "Questionable", report_period: "2026 NFL Injury Report · Injuries - WEEK 1", url: sources[0].url, retrieved_at: date };
const membership = { player_id: 7, league_id: 1, league_name: "Sunday league", fantasy_team: "My first team", is_mine: true, status: "Q", slot: "QB" };
const patrick = { key: "patrick", name: "Patrick Mahomes", team: "KC", position: "QB", injury: "Ankle", practice_status: "Limited Participation", game_status: "Questionable", status_source: "NFL", official_reports: [report], supplemental: [], memberships: [membership, { ...membership, player_id: 8, league_id: 2, league_name: "Work league", fantasy_team: "Rival team", is_mine: false }], is_mine: true, next_game: game };
const reserve = { ...patrick, key: "reserve", name: "Reserve Receiver", team: "BUF", position: "WR", injury: "Knee", game_status: "IR", practice_status: "Not supplied", status_source: "Roster import", official_reports: [], memberships: [{ ...membership, player_id: 9, league_id: 2, league_name: "Work league", fantasy_team: "My second team", status: "IR", slot: "IR" }] };
const unrostered = { ...patrick, key: "outside", name: "Unrostered Player With A Long Name", team: "SF", position: "RB", injury: "Hamstring", game_status: "Out", official_reports: [{ ...report, game_status: "Out", injury: "Hamstring" }], memberships: [], is_mine: false };
const rows = [patrick, reserve, unrostered];
const evidence = [{ id: "official-0", title: report.report_period, kind: "official_report", url: sources[0].url, retrieved_at: date }];
const output = { summary: "Limited practice leaves his game status uncertain.", outlook: "game_time_decision", confidence: "medium", availability: { text: "Questionable for the upcoming game; await the final report.", evidence_ids: ["official-0"] }, workload: { text: "A normal workload is not confirmed.", evidence_ids: ["official-0"] }, fantasy_advice: { text: "Keep a replacement ready before lineup lock.", evidence_ids: ["official-0"] }, next_update: "Watch the final practice report and inactive list.", missing_information: ["A snap limit has not been reported."] };

async function mockInjuries(page: Page, options: { failList?: boolean; failDetail?: boolean; noProviders?: boolean; failCheck?: boolean; stale?: boolean } = {}) {
  const state = { started: 0, polls: 0, detailKeys: [] as string[] };
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (path === "/onboarding/status") return json({ configured: true, auth_required: false, environment: "test", capabilities: { draft_suite: true } });
    if (path === "/system/health") return json({ status: "ok" });
    if (path === "/providers") return json(options.noProviders ? [] : [{ id: 1, name: "Test provider", model: "Test model", provider_type: "openai", enabled: true, task_defaults: ["recommendation"] }]);
    if (path === "/injuries") {
      if (options.failList) return json({ detail: "Report temporarily unavailable" }, 503);
      return json({ season: 2026, items: rows, total: 3, available: 3, my_players: 2, checked_at: date, sources: options.stale ? sources.map((source) => ({ ...source, status: "stale", message: "Refresh failed; showing previously retrieved reports." })) : sources, leagues: [{ id: 1, name: "Sunday league", my_team_name: "My first team" }, { id: 2, name: "Work league", my_team_name: "My second team" }, { id: 3, name: "Team not selected", my_team_name: null }] });
    }
    if (path.endsWith("/checks")) {
      const key = path.split("/")[2];
      if (route.request().method() === "POST") { state.started++; state.polls = 0; }
      else if (state.started) state.polls++;
      const check = { id: 10, key, model: "Test model", status: state.polls < 2 ? "queued" : options.failCheck ? "failed" : "completed", created_at: date, completed_at: state.polls >= 2 ? date : null, target_game: game, evidence, error: options.failCheck && state.polls >= 2 ? "The injury check failed. Run a new check to retry." : null, output: state.polls >= 2 && !options.failCheck ? output : null, stale: Boolean(options.stale) };
      return json(route.request().method() === "POST" ? check : state.started && key === "patrick" ? [check] : [], route.request().method() === "POST" ? 202 : 200);
    }
    if (path.startsWith("/injuries/")) {
      const key = path.split("/")[2];
      state.detailKeys.push(key);
      if (options.failDetail) return json({ detail: "Player sources temporarily unavailable" }, 503);
      return json({ ...rows.find((row) => row.key === key), sources: [...sources.slice(0, 1), { name: "Google News", url: "https://news.google.com", status: "ok", fetched_at: date }], articles: [{ title: "Patrick Mahomes injury update", source: "Team newsroom", url: "https://example.com/practice-update", excerpt: "", category: "injury", published_at: date, retrieved_at: date }, { title: "Unsafe article URL", source: "Unknown", url: "javascript:alert(1)", excerpt: "", category: "news", published_at: null, retrieved_at: date }] });
    }
    return json([]);
  });
  return state;
}

test("filters my players within the selected league and preserves filter URLs", async ({ page }) => {
  await mockInjuries(page);
  await page.goto("/injuries");
  const list = page.getByRole("region", { name: "Injury list", exact: true });
  await expect(list.getByRole("button", { name: "Patrick Mahomes", exact: true })).toBeVisible();
  // This control follows the router's concurrent URL transition; assert its
  // settled state rather than Playwright check()'s synchronous post-click read.
  await page.getByRole("checkbox", { name: "My players", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "My players", exact: true })).toBeChecked();
  await expect(list.getByRole("button", { name: unrostered.name })).toHaveCount(0);
  await page.getByRole("combobox", { name: "League", exact: true }).selectOption("2");
  await expect(list.getByRole("button", { name: "Patrick Mahomes", exact: true })).toHaveCount(0);
  await expect(list.getByRole("button", { name: "Reserve Receiver", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("checkbox", { name: "My players", exact: true })).toBeChecked();
  await expect(page.getByRole("combobox", { name: "League", exact: true })).toHaveValue("2");
  await page.getByRole("combobox", { name: "League", exact: true }).selectOption("3");
  await expect(page.getByText("Set your team to include its players:")).toBeVisible();
  await expect(page.getByRole("heading", { name: "No players match these filters" })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters", exact: true }).first().click();
  await page.getByRole("searchbox", { name: "Search players or injuries" }).fill("ankle");
  await expect(list.getByRole("button", { name: "Patrick Mahomes", exact: true })).toBeVisible();
  await expect(list.getByRole("button", { name: "Reserve Receiver", exact: true })).toHaveCount(0);
  await page.getByRole("combobox", { name: "Position", exact: true }).selectOption("WR");
  await expect(page.getByRole("heading", { name: "No players match these filters" })).toBeVisible();
});

test("opens detailed sources and saves a cited AI check across reloads", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const state = await mockInjuries(page);
  await page.goto("/injuries");
  await expect(page.getByRole("button", { name: "Patrick Mahomes", exact: true })).toBeVisible();
  await page.screenshot({ path: `/private/tmp/injury-list-${test.info().project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Patrick Mahomes", exact: true }).press("Enter");
  const detail = page.getByRole("region", { name: "Patrick Mahomes injury details", exact: true });
  await expect(detail.getByRole("heading", { name: "Patrick Mahomes", exact: true })).toBeFocused();
  await expect(detail.getByRole("link", { name: "Patrick Mahomes injury update" })).toHaveAttribute("href", "https://example.com/practice-update");
  await expect(detail.getByRole("link", { name: "Unsafe article URL" })).toHaveCount(0);
  await detail.getByRole("button", { name: "Run AI check", exact: true }).click();
  await expect(detail.getByText(/Checking injury reports and recent coverage/)).toBeVisible();
  await expect(detail.getByText("Game-time decision", { exact: true })).toBeVisible();
  await expect(detail.getByText("Keep a replacement ready before lineup lock.")).toBeVisible();
  await expect(detail.getByRole("link", { name: report.report_period }).first()).toHaveAttribute("href", sources[0].url);
  await page.reload();
  await expect(detail.getByText("Game-time decision", { exact: true })).toBeVisible();
  expect(state.started).toBe(1);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `/private/tmp/injury-detail-${test.info().project.name}.png`, fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await detail.getByRole("button", { name: "Close injury details", exact: true }).click();
  await expect(page.getByRole("button", { name: "Patrick Mahomes", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "Reserve Receiver", exact: true }).click();
  const reserveDetail = page.getByRole("region", { name: "Reserve Receiver injury details", exact: true });
  await expect(reserveDetail.getByText("Game-time decision", { exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("shows recoverable errors, provider setup, and stale evidence", async ({ page }) => {
  const options = { failList: true, failDetail: true, noProviders: true, stale: true };
  await mockInjuries(page, options);
  await page.goto("/injuries");
  await expect(page.getByRole("alert")).toContainText("Report temporarily unavailable", { timeout: 15000 });
  options.failList = false;
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.getByText("Stale · retrieved", { exact: false }).first()).toBeVisible();
  await page.getByRole("button", { name: "Patrick Mahomes", exact: true }).click();
  const detail = page.getByRole("region", { name: "Patrick Mahomes injury details", exact: true });
  await expect(detail.getByRole("link", { name: "Configure an AI provider" })).toBeVisible();
  await expect(detail.getByRole("button", { name: "Run AI check", exact: true })).toBeDisabled();
  await expect(detail.getByRole("alert")).toContainText("Player sources temporarily unavailable", { timeout: 15000 });
  options.failDetail = false;
  await detail.getByRole("button", { name: "Retry sources", exact: true }).click();
  await expect(detail.getByRole("link", { name: "Patrick Mahomes injury update" })).toBeVisible();
});

test("failed AI checks can be retried without losing player context", async ({ page }) => {
  const options = { failCheck: true };
  const state = await mockInjuries(page, options);
  await page.goto("/injuries?player=patrick&mine=true");
  const detail = page.getByRole("region", { name: "Patrick Mahomes injury details", exact: true });
  await detail.getByRole("button", { name: "Run AI check", exact: true }).click();
  await expect(detail.getByRole("alert")).toContainText("The injury check failed.");
  options.failCheck = false;
  await detail.getByRole("button", { name: "Run AI check", exact: true }).click();
  await expect(detail.getByText("Game-time decision", { exact: true })).toBeVisible();
  expect(state.started).toBe(2);
  await expect(page.getByRole("checkbox", { name: "My players", exact: true })).toBeChecked();
});
