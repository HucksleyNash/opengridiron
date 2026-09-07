import { expect, test, type Locator, type Page } from "@playwright/test";

const date = "2026-09-04T12:00:00Z";
const league = { id: 1, name: "Player link checks", season: 2026, source: "manual", team_names: ["My Team"], my_team_name: "My Team", scoring: {}, roster_slots: ["QB", "BN"], player_count: 3 };
const base = { league_id: 1, status: "Active", ownership: "FA", projected_points: 23, floor: 16, ceiling: 29, risk: 0.2, ros_value: null, projection: { source: "Stored test projection", period: "week", season: 2026, week: 1, ros_value_state: "missing" } };
const allen = { ...base, id: 7, name: "Josh Allen", position: "QB", pro_team: "BUF", rostered_by: "My Team", current_slot: "QB" };
const mahomes = { ...base, id: 8, name: "Patrick Mahomes", position: "QB", pro_team: "KC", rostered_by: "My Team", current_slot: "BN" };
const stroud = { ...base, id: 9, name: "C.J. Stroud", position: "QB", pro_team: "HOU", rostered_by: "", current_slot: "" };
const players = [allen, mahomes, stroud];
const draftPlayer = { ...stroud, athlete_id: 99, bye_week: 10, target: false, fade: false, projected_points: 999 };
const pick = { event_id: 10, sequence: 1, overall_pick: 1, round: 1, team_slot: 1, player_id: allen.id, player_name: allen.name, position: allen.position, pro_team: allen.pro_team, bye_week: 7, source: "manual" };
const candidate = { ...draftPlayer, player_id: stroud.id, score: 88, vor: 10, tier_cliff: false, vor_drop: 1, why_now: "C.J. Stroud has the best available value.", roster_impact: "Adds QB depth", tradeoff: "Check current reports", next_turn: { status: "ready", label: "Likely to remain" }, components: { market_rank: 1 } };
const teams = [{ id: 1, slot: 1, name: "My Team", is_owner: true, roster: [pick] }];

async function mockApp(page: Page, options: { complete?: boolean; fail?: boolean; duplicate?: boolean; directoryFail?: boolean } = {}) {
  const writes: string[] = [];
  const requests: string[] = [];
  const session = { id: 20, league_id: 1, league_name: league.name, kind: "mock", opponent_mode: "manual", format: "snake", status: options.complete ? "COMPLETE" : "LIVE", strategy_mode: "adaptive", team_count: 8, round_count: 2, owner_team_slot: 1, source_mode: "manual", current_sequence: 1, preference_revision: 0, replay_generation: 1, teams, readiness: { ready: true, findings: [] }, created_at: date };
  const board = { session_id: 20, status: session.status, current_sequence: 1, preference_revision: 0, total_picks: 16, completed_picks: 1, current_overall_pick: 2, current_round: 1, current_team_slot: 1, owner_on_clock: true, opponent_mode: "manual", next_owner_pick: 2, teams, picks: [pick], available_players: [draftPlayer], queue: [draftPlayer], freshness: { source_mode: "manual", provisional: false } };
  const forecast = (player: typeof allen) => ({ player_id: player.id, name: player.name, team: player.pro_team, position: player.position, rostered_by: player.rostered_by, current_slot: player.current_slot, status: player.status, locked: false, conditional: false, points: 24, floor: 19, ceiling: 30, confidence: "low", reason: null, warnings: [], sample_games: 16, recent_usage: 20, baseline_usage: 18, opponent: null, source_projection: { ...player.projection, points: 23, comparable: true }, difference: 1 });
  const summary = { id: 30, league_id: 1, team_name: "My Team", season: 2026, week: 1, status: "completed", created_at: date, completed_at: date, has_report: true, error: null };
  const output = { summary: "Josh Allen and C.J. Stroud are worth reviewing.", recommendations: ["Start Josh Allen over Patrick Mahomes."], risks: ["Monitor Patrick Mahomes."], missing_information: [], citations: [] };
  const report = { model_version: "test", generated_at: date, season: 2026, week: 1, team_name: "My Team", method: "Test baseline", limitations: [], changes: ["Josh Allen gained a point."], coverage: { modeled: 3, players: 3 }, forecasts: players.map(forecast), league_coverage: [], lineup: { error: null, current_points: 20, recommended_points: 24, gain: 4, bench: [mahomes.name], assignments: [{ slot: "QB", player_id: allen.id, name: allen.name, points: 24, action: "Start", reason: null, conditional: false }], waivers: [{ add_id: stroud.id, add: stroud.name, drop_id: mahomes.id, drop: mahomes.name, gain: 2, conditional: false, reason: "Compare C.J. Stroud with Patrick Mahomes." }] }, sources: [], analysis: { status: "completed", output }, evaluation: { scored_forecasts: 0, comparison_count: 0, note: "Test data" } };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ json: body, status });
    if (route.request().method() !== "GET") writes.push(path);
    if (path.endsWith("/onboarding/status")) return json({ configured: true, auth_required: false, capabilities: { draft_suite: true } });
    if (path.endsWith("/system/health")) return json({ status: "ok" });
    if (path.endsWith("/players/directory")) return options.directoryFail ? json({ detail: "Directory unavailable" }, 503) : json([...players, ...(options.duplicate ? [{ ...allen, id: 17, league_id: 2, league_name: "Second league" }] : [])].map(({ id, league_id, name, pro_team, position, ...rest }) => ({ id, league_id, name, pro_team, position, league_name: "league_name" in rest ? rest.league_name : league.name })));
    const playerMatch = path.match(/\/players\/(\d+)(\/synopsis)?$/);
    if (playerMatch) {
      requests.push(path);
      const player = players.find((item) => item.id === Number(playerMatch[1])) || { ...allen, id: 17, league_id: 2 };
      if (options.fail && !playerMatch[2]) return json({ detail: "Player unavailable" }, 503);
      if (!playerMatch[2]) return json(player);
      if (options.fail) return json({ detail: "Reports unavailable" }, 503);
      return json({ player, synopsis: `${player.name} player overview.`, injury_reports: [], articles: [{ title: "Latest attributed report", url: "https://example.com/player-report", excerpt: "A sourced update.", source: "Test Sports", category: "news", published_at: date, retrieved_at: date }], sources: [{ name: "NFL injury report", url: "https://www.nfl.com/injuries/", status: "ok", checked_at: date, fetched_at: date }, { name: "Google News", url: "https://news.google.com", status: "ok", checked_at: date, fetched_at: date }], news_window_days: 30 });
    }
    if (path.endsWith("/dashboard")) return json({ leagues: [league], pools: [], alerts: [{ id: 1, title: "Josh Allen prepares for opener", message: "Patrick Mahomes also practiced.", severity: "medium", url: "https://example.com/story", read: false, created_at: date }], snapshots: [], analysis_runs: [] });
    if (path.endsWith("/waivers")) return json([{ player_id: stroud.id, rank: 1, subject: "C.J. Stroud (QB, HOU)", expected_value: 20, confidence: 0.8, data_as_of: date }]);
    if (path.endsWith("/leagues")) return json([league]);
    if (path.endsWith("/leagues/1")) return json(league);
    if (path.endsWith("/draft-sessions")) return json([session]);
    if (path.endsWith("/draft-sessions/20")) return json(session);
    if (path.endsWith("/board")) return json(board);
    if (path.endsWith("/recommendations")) return json({ status: "ready", candidates: [candidate], alternatives: [], freshness: {}, forecast_status: "ready" });
    if (path.endsWith("/exposure")) return json({ players: [{ player_id: stroud.id, name: stroud.name, league_count: 1, leagues: [{ name: "Other league" }] }] });
    if (path.endsWith("/replay")) return json({ session_id: 20, decisions: [{ event: { id: 10, overall_pick: 1, player_id: allen.id, player_name: allen.name }, candidates: [candidate], decision_quality: 0.8 }], coaching: { findings: [], minimum_linked_decisions: 3 }, waiver_priorities: [draftPlayer], waiver_moves: [{ add: draftPlayer, drop: pick, projected_point_gain: 2, reason: "Compare the available player." }] });
    if (path.endsWith("/analysis-context")) return json({ teams: ["My Team"], suggested_week: 1 });
    if (path.endsWith("/analyses")) return json([summary]);
    if (path.endsWith("/analyses/30")) return json({ ...summary, report, stale_reasons: [] });
    if (path.endsWith("/roster")) return json([allen, mahomes]);
    if (path.endsWith("/weekly-lineup")) return json({ season: 2026, week: 1, assignments: [], forecasts: [], unfilled_slots: [], mode: "balanced", current_total: 20, projected_total: 24, projected_gain: 4 });
    if (path.endsWith("/waivers/page")) return json({ items: [], total: 0, available: 0, next_offset: null, facets: { teams: [], positions: [], statuses: [] } });
    if (path.endsWith("/analysis/runs")) return json([{ id: 40, task: "chat", question: "Compare Josh Allen with Patrick Mahomes", provider: "Test", model: "Test", status: "completed", output, created_at: date }]);
    if (path.endsWith("/news/items")) return json([{ id: 1, title: "Josh Allen prepares for opener", excerpt: "C.J. Stroud also practiced.", category: "news", severity: "medium", canonical_url: "https://example.com/story", published_at: date }]);
    return json([]);
  });
  return { writes, requests };
}

async function checkPlayer(page: Page, trigger: Locator, name: string) {
  const url = page.url();
  await trigger.click();
  const dialog = page.getByRole("dialog", { name, exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Player overview" })).toBeVisible();
  await dialog.getByRole("tab", { name: "Projections", exact: true }).click();
  await expect(dialog.getByText("Stored test projection", { exact: true })).toBeVisible();
  await expect(dialog.getByText("23.0", { exact: true })).toBeVisible();
  await expect(dialog.getByText("999.0", { exact: true })).toHaveCount(0);
  await dialog.getByRole("tab", { name: "News & sources" }).click();
  await expect(dialog.getByRole("link", { name: /Latest attributed report/ })).toBeVisible();
  expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  expect(page.url()).toBe(url);
}

test("draft names open current details without recording picks, editing the queue or clearing search", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const { writes } = await mockApp(page);
  await page.goto("/draft/1");
  await page.getByLabel("Search available players").fill("Stroud");
  for (const [selector, player] of [[".draft-owner-roster", allen], [".draft-candidate-name", stroud], [".draft-case", stroud], [".draft-player-list", stroud], [".draft-queue-list", stroud], [".draft-pick-list", allen], [".draft-exposure-list", stroud]] as const) {
    const trigger = page.locator(selector).getByRole("button", { name: `View ${player.name} synopsis` }).first();
    await checkPlayer(page, trigger, player.name);
  }
  await expect(page.getByLabel("Search available players")).toHaveValue("Stroud");
  await page.locator(".draft-pick-list").getByRole("button", { name: "Correct" }).click();
  await checkPlayer(page, page.locator(".draft-correction").getByRole("button", { name: "View Josh Allen synopsis" }), allen.name);
  await expect(page.locator(".draft-correction")).toBeVisible();
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
  await page.screenshot({ path: `/private/tmp/player-links-draft-${test.info().project.name}.png`, fullPage: true });
});

test("completed draft roster, decisions, alternatives and both sides of waiver moves are clickable", async ({ page }) => {
  const { writes } = await mockApp(page, { complete: true });
  await page.goto("/draft/1");
  for (const [selector, name] of [[".draft-final-roster", allen.name], [".draft-replay-decisions h3", allen.name], [".draft-replay-decisions p", stroud.name], [".draft-waiver-list", stroud.name], [".draft-waiver-list", allen.name]]) {
    await checkPlayer(page, page.locator(selector).getByRole("button", { name: `View ${name} synopsis` }), name);
  }
  expect(writes).toEqual([]);
});

test("forecast lineup, bench, waivers, comparison and analyst mentions share the player drawer", async ({ page }) => {
  await mockApp(page);
  await page.goto("/leagues/1?tab=forecast&team=My+Team&week=1");
  for (const [selector, name] of [[".weekly-lineup-row", allen.name], ["#weekly-lineup > p", mahomes.name], [".weekly-waiver-row > strong", stroud.name], [".weekly-waiver-row > p", mahomes.name], [".weekly-table-wrap", allen.name]]) {
    await checkPlayer(page, page.locator(selector).getByRole("button", { name: `View ${name} synopsis` }).first(), name);
  }
  await page.locator(".weekly-briefing > summary").click();
  await checkPlayer(page, page.locator(".weekly-briefing").getByRole("button", { name: "View Josh Allen synopsis" }).first(), allen.name);
  await page.screenshot({ path: `/private/tmp/player-links-forecast-${test.info().project.name}.png`, fullPage: true });
});

test("Command Center, news and saved analyst text link players without nesting interactive controls", async ({ page }) => {
  await mockApp(page);
  await page.goto("/");
  await checkPlayer(page, page.locator(".command-market").getByRole("button", { name: "View C.J. Stroud synopsis" }), stroud.name);
  await checkPlayer(page, page.locator(".command-alert").getByRole("button", { name: "View Josh Allen synopsis" }), allen.name);
  await expect(page.getByRole("link", { name: "Open article: Josh Allen prepares for opener" })).toHaveAttribute("href", "https://example.com/story");
  await page.goto("/news");
  await checkPlayer(page, page.locator(".news-feed").getByRole("button", { name: "View Josh Allen synopsis" }), allen.name);
  await checkPlayer(page, page.locator(".news-feed").getByRole("button", { name: "View C.J. Stroud synopsis" }), stroud.name);
  await expect(page.getByRole("link", { name: "Read article" })).toHaveAttribute("href", "https://example.com/story");
  await page.goto("/analysis");
  await checkPlayer(page, page.locator(".analysis-history").getByRole("button", { name: "View Patrick Mahomes synopsis" }), mahomes.name);
  await page.getByRole("button", { name: /Open saved analysis:/ }).click();
  await checkPlayer(page, page.locator(".analysis-answer ol").getByRole("button", { name: "View Josh Allen synopsis" }), allen.name);
  expect(await page.locator("button button, a button, button a").count()).toBe(0);
});

test("duplicate names require a league choice instead of silently using another record", async ({ page }) => {
  const { requests } = await mockApp(page, { duplicate: true });
  await page.goto("/news");
  await page.getByRole("button", { name: "View Josh Allen synopsis" }).click();
  const dialog = page.getByRole("dialog", { name: allen.name });
  await expect(dialog.getByText("Choose the league record to view its roster and projections.")).toBeVisible();
  expect(requests).toEqual([]);
  await dialog.getByRole("button", { name: /Second league/ }).click();
  await expect(dialog.getByText("Josh Allen player overview.")).toBeVisible();
  expect(requests).toContain("/api/v1/players/17");
  expect(requests).not.toContain("/api/v1/players/7");
});

test("loading failures remain retryable without showing a draft snapshot as current information", async ({ page }) => {
  const options = { fail: true };
  await mockApp(page, options);
  await page.goto("/draft/1");
  await page.locator(".draft-player-list").getByRole("button", { name: "View C.J. Stroud synopsis" }).press("Enter");
  const dialog = page.getByRole("dialog", { name: stroud.name });
  await expect(dialog.getByRole("button", { name: "Retry player information" })).toBeVisible();
  await dialog.getByRole("tab", { name: "Projections", exact: true }).click();
  await expect(dialog.getByText("999.0", { exact: true })).toHaveCount(0);
  options.fail = false;
  await dialog.getByRole("button", { name: "Retry player information" }).click();
  await expect(dialog.getByText("Stored test projection")).toBeVisible();
  await dialog.screenshot({ path: `/private/tmp/player-links-details-${test.info().project.name}.png` });
});
