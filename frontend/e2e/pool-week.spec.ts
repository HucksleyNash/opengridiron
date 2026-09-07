import { expect, Page, test } from "@playwright/test";

const schedule = { state: "ready", source: "nflverse.schedule", last_success_at: "2026-09-08T12:00:00Z" };
const rules = {
  direction: "winner",
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
const game = {
  id: 31,
  source_game_key: "2026091001",
  source_game_key_kind: "gsis",
  kickoff: "2026-09-10T00:20:00Z",
  locked: false,
  away_team: "GB",
  home_team: "CHI",
  spread_home: -3.5,
  probabilities: { home_win: 0.68, win_kind: "market", home_cover: 0.53, cover_kind: "market" },
  recommendations: [
    { team: "CHI", score: 0.64, probability: 0.68, rationale: ["Estimated win probability 68%", "Future-value adjustment 4%"] },
    { team: "GB", score: 0.28, probability: 0.32, rationale: ["Estimated win probability 32%", "Future-value adjustment 4%"] },
  ],
};

async function mockApp(page: Page, options: { conflict?: boolean; confidence?: boolean; ai?: boolean; partial?: boolean; slowSave?: boolean } = {}) {
  const confidence = Boolean(options.confidence);
  const poolId = confidence ? 2 : 1;
  const entryId = confidence ? 22 : 11;
  let savedPayload: Record<string, unknown> | undefined;
  let checkIns = 0;
  let applies = 0;
  const saves: Record<string, unknown>[] = [];
  let currentCard: Record<string, unknown> | undefined;
  const freshness = { status: options.partial ? "partial" : "ready", checked_at: "2026-09-09T12:00:00Z", sources: [{ name: "NFL schedule, odds and results", status: options.partial ? "unavailable" : "refreshed", checked_at: "2026-09-09T12:00:00Z" }] };
  const secondGame = {
    ...game,
    id: 32,
    source_game_key: "2026091002",
    away_team: "KC",
    home_team: "DEN",
    kickoff: "2026-09-11T00:20:00Z",
    recommendations: [
      { team: "KC", score: 0.58, probability: 0.62, rationale: ["Estimated win probability 62%", "Future-value adjustment 4%"] },
      { team: "DEN", score: 0.34, probability: 0.38, rationale: ["Estimated win probability 38%", "Future-value adjustment 4%"] },
    ],
    suggested_team: "KC",
    suggested_confidence: 1,
  };
  const games = confidence
    ? [{ ...game, suggested_team: "CHI", suggested_confidence: 2 }, secondGame]
    : [game];
  const pool = {
    id: poolId,
    name: confidence ? "Glascott Confidence Pool" : "Sunday Winner Pool",
    season: 2026,
    pool_type: confidence ? "confidence" : "survivor",
    rules,
    suggested_week: 1,
    inactive_entry_count: 0,
    schedule,
    entries: [{
      id: entryId,
      name: "Main entry",
      active: true,
      card_state: "draft",
      required_count: games.length,
      selection_count: 0,
      weight_count: confidence ? 0 : null,
      missing_count: games.length,
    }],
  };

  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
    if (path.endsWith("/onboarding/status")) return json({ configured: true, auth_required: false, environment: "test", timezone: "America/Chicago", capabilities: { draft_suite: true } });
    if (path.endsWith("/providers")) return json(options.ai ? [{ id: 1, name: "Test analyst", model: "test", enabled: true }] : []);
    if (path.endsWith("/players/directory")) return json([]);
    if (path.endsWith("/check-in")) { checkIns += 1; return json(freshness); }
    if (path.endsWith("/analysis/apply")) {
      applies += 1;
      currentCard = { version: 1, state: "complete", required_count: games.length, selection_count: games.length, weight_count: confidence ? games.length : null, missing_count: 0, picks: games.map((game, index) => ({ id: 80 + index, game_id: game.id, team: game.home_team, slot: confidence ? null : 1, confidence: confidence ? index + 1 : null, locked: false })), findings: [] };
      return json({ card: currentCard });
    }
    if (path.endsWith("/analysis")) return json({ run_id: 71, version: 0, can_apply: !options.partial, reason: options.partial ? "Some sources could not refresh. Retry before setting AI picks." : null, freshness, output: { summary: "Pool analysis is ready", recommendations: ["Choose the home team based on the supplied evidence."], risks: ["A favorite can still lose."], missing_information: [], citations: [], picks: games.map((game, index) => ({ game_id: game.id, team: game.home_team, slot: confidence ? null : 1, confidence: confidence ? index + 1 : null })) } });
    if (path.endsWith("/standings")) return json({ entries: [], pending_games: 0 });
    if (path.endsWith("/strategy")) return json({ status: "unavailable", reasons: ["Fixture has no future schedule"], recommendations: [] });
    if (path.endsWith("/system/health")) return json({ status: "ok" });
    if (path.endsWith("/pools/overview")) return json({ generated_at: "2026-09-09T12:00:00Z", pools: [pool] });
    if (path.endsWith(`/pools/${poolId}/entries`)) return json([{ id: entryId, pool_id: poolId, name: "Main entry", active: true }]);
    if (path.endsWith(`/pools/${poolId}/weeks/1`)) return json({
      pool: { id: poolId, name: pool.name, season: 2026, pool_type: pool.pool_type, entry_count: 1, rules },
      week: { number: 1, suggested_week: 1, first_kickoff: game.kickoff, last_kickoff: games.at(-1)?.kickoff },
      schedule,
      entry: { id: entryId, pool_id: poolId, name: "Main entry", active: true, read_only: false },
      card: currentCard || { version: 0, state: "draft", required_count: games.length, selection_count: 0, weight_count: confidence ? 0 : null, missing_count: games.length, picks: [], findings: [] },
      games,
      survivor_slots: confidence ? null : [{ slot: 1, current_pick: null, choices: [
        { game_id: 31, team: "GB", eligible: true, reason: null },
        { game_id: 31, team: "CHI", eligible: true, reason: null },
      ] }],
      configuration_errors: [],
    });
    if (path.endsWith(`/entries/${entryId}/weeks/1/picks`) && route.request().method() === "PUT") {
      savedPayload = route.request().postDataJSON();
      saves.push(savedPayload!);
      if (options.slowSave) await new Promise((resolve) => setTimeout(resolve, 700));
      const picks = (savedPayload.picks || []) as Array<Record<string, unknown>>;
      if (options.conflict) return json({
        error: "card_conflict",
        message: "The weekly card changed or a pick locked.",
        conflicts: [],
        card: {
          version: 1,
          state: "complete",
          required_count: 1,
          selection_count: 1,
          weight_count: null,
          missing_count: 0,
          picks: [{ id: 90, slot: 1, game_id: 31, team: "GB", confidence: null, locked: false }],
          findings: [],
        },
      }, 409);
      currentCard = {
        version: Number(savedPayload.version) + 1,
        state: "complete",
        required_count: games.length,
        selection_count: games.length,
        weight_count: confidence ? games.length : null,
        missing_count: 0,
        picks: picks.map((pick, index) => ({ id: 80 + index, slot: pick.slot || index + 1, ...pick, locked: false })),
        findings: [],
      };
      return json({ card: currentCard });
    }
    return json({ detail: `Unhandled mock route: ${path}` }, 404);
  });
  return { poolId, entryId, getSavedPayload: () => savedPayload, getCheckIns: () => checkIns, getApplies: () => applies, getSaves: () => saves };
}

test("overview opens the entry and autosaves a winner", async ({ page }) => {
  const mocked = await mockApp(page);
  await page.goto("/pools");
  await expect(page.getByRole("heading", { name: "Pool week" })).toBeVisible();
  await expect(page.getByText("Sunday Winner Pool")).toBeVisible();
  await page.getByRole("link", { name: /Main entry/ }).click();
  await expect(page.getByRole("heading", { name: "Choose one winner" })).toBeVisible();
  await page.getByRole("button", { name: /CHI.*68%/ }).click();
  await expect.poll(() => mocked.getSavedPayload()).toBeTruthy();
  expect(mocked.getSavedPayload()?.version).toBe(0);
  await expect(page.getByText(/Week complete/)).toBeVisible();
});

test("suggested confidence card fills every game and every weight", async ({ page }) => {
  const mocked = await mockApp(page, { confidence: true });
  await page.goto(`/pools/${mocked.poolId}/weeks/1?entry_id=${mocked.entryId}`);
  await page.getByRole("button", { name: /Use suggested card/ }).click();
  await expect.poll(() => mocked.getSavedPayload()).toBeTruthy();
  const picks = mocked.getSavedPayload()?.picks as Array<Record<string, unknown>>;
  expect(picks).toHaveLength(2);
  expect(new Set(picks.map((pick) => pick.confidence))).toEqual(new Set([1, 2]));
  await expect(page.getByText(/Week complete/)).toBeVisible();
});

test("a stale autosave reloads the canonical card and offers safe reapply", async ({ page }) => {
  const mocked = await mockApp(page, { conflict: true });
  await page.goto(`/pools/${mocked.poolId}/weeks/1?entry_id=${mocked.entryId}`);
  await page.getByRole("button", { name: /CHI.*68%/ }).click();
  await expect(page.getByText("Another tab changed this card.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Reapply unlocked changes" })).toBeVisible();
  await expect(page.getByRole("button", { name: /GB.*32%/ })).toHaveAttribute("aria-pressed", "true");
});

test("mobile pool week has no horizontal page overflow", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "mobile-only layout assertion");
  const mocked = await mockApp(page);
  await page.goto(`/pools/${mocked.poolId}/weeks/1?entry_id=${mocked.entryId}`);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await expect(page.locator(".autosave-bar")).toBeVisible();
});

test("opening and returning to a pool checks sources and supports a manual refresh", async ({ page }) => {
  const mocked = await mockApp(page);
  await page.goto(`/pools/${mocked.poolId}/weeks/1?entry_id=${mocked.entryId}`);
  await expect.poll(mocked.getCheckIns).toBeGreaterThan(0);
  const first = mocked.getCheckIns();
  await page.getByRole("button", { name: "Refresh data" }).click();
  await expect.poll(mocked.getCheckIns).toBeGreaterThan(first);
  const second = mocked.getCheckIns();
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(mocked.getCheckIns).toBeGreaterThan(second);
});

for (const confidence of [false, true]) {
  test(`AI previews and saves a ${confidence ? "confidence" : "survivor"} card`, async ({ page }, testInfo) => {
    const mocked = await mockApp(page, { ai: true, confidence });
    await page.goto(`/pools/${mocked.poolId}/weeks/1?entry_id=${mocked.entryId}`);
    await page.getByRole("button", { name: "Analyze picks" }).click();
    await expect(page.getByText("Pool analysis is ready")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("pool-analysis.png"), fullPage: true });
    expect(mocked.getApplies()).toBe(0);
    expect(mocked.getSavedPayload()).toBeUndefined();
    await page.getByRole("button", { name: "Set AI picks" }).click();
    await expect(page.getByText("AI picks saved for this entry.")).toBeVisible();
    expect(mocked.getApplies()).toBe(1);
    await expect(page.getByText(/Week complete/)).toBeVisible();
  });
}

test("failed sources keep cached games and withhold AI setting", async ({ page }) => {
  const mocked = await mockApp(page, { ai: true, partial: true });
  await page.goto(`/pools/${mocked.poolId}/weeks/1?entry_id=${mocked.entryId}`);
  await expect(page.getByText("Some sources could not refresh. Review coverage before making picks.")).toBeVisible();
  await expect(page.getByRole("button", { name: /CHI.*68%/ })).toBeVisible();
  await page.getByRole("button", { name: "Analyze picks" }).click();
  await expect(page.getByRole("button", { name: "Set AI picks" })).toBeDisabled();
  expect(mocked.getApplies()).toBe(0);
});

test("a source check during autosave preserves the newest local selection", async ({ page }) => {
  const mocked = await mockApp(page, { slowSave: true });
  await page.goto(`/pools/${mocked.poolId}/weeks/1?entry_id=${mocked.entryId}`);
  await page.getByRole("button", { name: /CHI.*68%/ }).click();
  await expect.poll(() => mocked.getSaves().length).toBe(1);
  await page.getByRole("button", { name: /GB.*32%/ }).click();
  await page.getByRole("button", { name: "Refresh data" }).click();
  await expect.poll(() => mocked.getSaves().length).toBe(2);
  await expect(page.getByText(/Week complete/)).toBeVisible();
  await expect(page.getByRole("button", { name: /GB.*32%/ })).toHaveAttribute("aria-pressed", "true");
  expect(mocked.getSaves()[1].version).toBe(1);
  expect((mocked.getSaves()[1].picks as {team: string}[])[0].team).toBe("GB");
});
