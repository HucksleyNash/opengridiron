import { expect, test, type Page } from "@playwright/test";
import type { Game } from "../src/types";
import { commandFixture, fixtureNow } from "./fixtures/command-center";

async function liveWorkspace(page: Page, options: { fail?: boolean; empty?: boolean } = {}) {
  const state = {
    checks: 0, fail: options.fail || false, hold: false,
    release: () => {}, mutations: [] as string[],
    games: options.empty ? [] : structuredClone(commandFixture["/games"]) as Game[],
  };
  await page.clock.install({ time: new Date(fixtureNow) });
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    if (route.request().method() !== "GET") state.mutations.push(path);
    if (path === "/sync/live-scores") {
      expect(route.request().method()).toBe("POST");
      expect(url.searchParams.get("season")).toBe("2026");
      expect(url.searchParams.get("week")).toBe("2");
      state.checks += 1;
      if (state.hold) await new Promise<void>((resolve) => { state.release = resolve; });
      if (state.fail) return route.fulfill({ status: 502, json: { detail: "ESPN temporarily unavailable" } });
      state.games[1] = { ...state.games[1], away_score: (state.checks - 1) * 7, home_score: 3, completed: state.checks >= 3 };
      return route.fulfill({ json: { status: "completed", matched: 1, updated: 1 } });
    }
    const value = path === "/games" ? state.games : commandFixture[path];
    return route.fulfill({ status: value === undefined ? 404 : 200, json: value ?? { detail: "Fixture source unavailable" } });
  });
  await page.goto("/");
  await expect(page.locator(".command-game")).toHaveCount(state.games.length);
  return state;
}

const pulseGame = (page: Page) => page.locator(".command-game").filter({ hasText: "CAR" });

test("page load, 30-second heartbeats, and reload pull scores without a full source refresh", async ({ page }) => {
  const state = await liveWorkspace(page);
  await expect(pulseGame(page).getByLabel("CAR 0, ATL 3")).toBeVisible();
  await expect(page.locator(".command-score-check")).toContainText("checked just now");
  expect(state.checks).toBe(1);

  await page.clock.runFor(10_000);
  expect(state.checks).toBe(1);
  await page.clock.runFor(20_100);
  await expect(pulseGame(page).getByLabel("CAR 7, ATL 3")).toBeVisible();
  expect(state.checks).toBe(2);

  await page.clock.runFor(30_100);
  await expect(pulseGame(page).getByLabel("CAR 14, ATL 3")).toBeVisible();
  await expect(pulseGame(page)).toContainText("Final");
  await expect(page.locator(".command-game")).toHaveCount(16);

  await page.reload();
  await expect(pulseGame(page).getByLabel("CAR 21, ATL 3")).toBeVisible();
  expect(state.mutations).toEqual(Array(4).fill("/sync/live-scores"));
});

test("failed heartbeat retains scores and the next heartbeat recovers", async ({ page }) => {
  const state = await liveWorkspace(page);
  await expect(pulseGame(page).getByLabel("CAR 0, ATL 3")).toBeVisible();
  state.fail = true;
  await page.clock.runFor(30_100);
  await expect(page.locator("#command-games")).toContainText("Showing saved scores");
  await expect(pulseGame(page).getByLabel("CAR 0, ATL 3")).toBeVisible();
  await expect(page.locator(".command-game")).toHaveCount(16);
  expect(state.checks).toBe(2);

  state.fail = false;
  await page.clock.runFor(30_100);
  await expect(pulseGame(page).getByLabel("CAR 14, ATL 3")).toBeVisible();
  await expect(page.locator("#command-games")).not.toContainText("Showing saved scores");
});

test("failed initial check offers an immediate retry", async ({ page }) => {
  const state = await liveWorkspace(page, { fail: true });
  await expect(page.locator("#command-games")).toContainText("Showing saved scores");
  await expect(page.getByLabel("MIN 10, CHI 14")).toBeVisible();
  state.fail = false;
  await page.locator("#command-games").getByRole("button", { name: "Try again" }).click();
  await expect(pulseGame(page).getByLabel("CAR 7, ATL 3")).toBeVisible();
  expect(state.checks).toBe(2);
});

test("polling pauses in hidden tabs and off the Command Center, then checks on return", async ({ page }) => {
  const state = await liveWorkspace(page);
  await expect(pulseGame(page).getByLabel("CAR 0, ATL 3")).toBeVisible();
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
  });
  await page.clock.runFor(90_000);
  expect(state.checks).toBe(1);
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
  });
  await expect(pulseGame(page).getByLabel("CAR 7, ATL 3")).toBeVisible();

  await page.locator("#command-news").getByRole("link", { name: "News wire", exact: true }).click();
  await expect(page).toHaveURL(/\/news$/);
  await page.clock.runFor(60_000);
  expect(state.checks).toBe(2);
  await page.locator('nav:visible a[href="/"]').click();
  await expect(pulseGame(page).getByLabel("CAR 14, ATL 3")).toBeVisible();
  expect(state.checks).toBe(3);
});

test("slow score checks do not overlap on later heartbeats", async ({ page }) => {
  const state = await liveWorkspace(page);
  await expect(pulseGame(page).getByLabel("CAR 0, ATL 3")).toBeVisible();
  state.hold = true;
  await page.clock.runFor(30_100);
  await expect.poll(() => state.checks).toBe(2);
  await page.clock.runFor(90_000);
  expect(state.checks).toBe(2);
  await expect(pulseGame(page).getByLabel("CAR 0, ATL 3")).toBeVisible();
  state.hold = false;
  state.release();
  await expect(pulseGame(page).getByLabel("CAR 7, ATL 3")).toBeVisible();
  await page.clock.runFor(30_100);
  await expect(pulseGame(page).getByLabel("CAR 14, ATL 3")).toBeVisible();
});

test("an empty schedule does not send score requests without a week", async ({ page }) => {
  const state = await liveWorkspace(page, { empty: true });
  await expect(page.locator("#command-games")).toContainText("No games loaded for this week");
  await page.clock.runFor(60_000);
  expect(state.checks).toBe(0);
});
