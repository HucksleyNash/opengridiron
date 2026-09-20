import { expect, test, type Page } from "@playwright/test";
import { commandFixture, fixtureNow } from "./fixtures/command-center";

async function workspace(page: Page, overrides: Record<string, unknown> = {}) {
  const mutations: string[] = [];
  await page.clock.setFixedTime(new Date(fixtureNow));
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (route.request().method() !== "GET") mutations.push(path);
    const value = path in overrides ? overrides[path] : commandFixture[path];
    await route.fulfill({ status: value === null ? 503 : value === undefined ? 404 : 200, contentType: "application/json", body: JSON.stringify(value ?? { detail: "Fixture source unavailable" }) });
  });
  await page.goto("/");
  return mutations;
}

test("owner reminders, injury relevance and local pick readiness lead the workspace", async ({ page }) => {
  const mutations = await workspace(page);
  const reminders = page.locator("#command-reminders");
  await expect(page.locator(".command-next-review")).toContainText("Brock Bowers · Doubtful");
  await expect(page.locator(".command-next-review")).toHaveAttribute("href", "/injuries?mine=true&player=player-11");
  await expect(reminders).toContainText("1 lineup option to review");
  await expect(reminders).toContainText("+0.1 vs current");
  await expect(reminders.locator(".command-league-summary .urgent")).toHaveCount(0);
  await expect(page.locator(".command-pool-row.warning")).toHaveCount(0);
  await expect(page.locator("#command-pools").locator("..")).toContainText("Complete here");
  await expect(page.getByRole("link", { name: /Entries to review/ })).toContainText("02");
  await expect(page.locator(".command-injury-row").first()).toContainText("Brock Bowers");
  await expect(page.locator(".command-alert").first()).toContainText("On your rosters");
  await expect(page.locator(".command-alert").first()).not.toContainText("League announces");
  await expect(page.locator(".command-alert")).toHaveCount(5);
  await page.getByRole("button", { name: "Show all 6 recent headlines" }).click();
  await expect(page.locator(".command-alert")).toHaveCount(6);
  await expect(page.locator(".command-pool-row").first()).toHaveAttribute("href", "/pools/1/weeks/2?entry_id=1");
  await expect(page.locator(".command-briefing")).toHaveAttribute("href", "/analysis?parent_run_id=10");
  expect(mutations).toEqual(["/sync/live-scores"]);
});

test("Game Pulse stays secondary, keeps the full slate, and separates pregame beliefs from results", async ({ page }) => {
  await workspace(page);
  await expect(page.locator(".command-game")).toHaveCount(16);
  await expect(page.locator(".command-game").first()).toContainText("JAX");
  const final = page.locator(".command-game.final");
  await expect(final).toContainText("31 – 41");
  await expect(final).not.toContainText("68%");
  await expect(page.locator(".dashboard-tape")).not.toContainText("68%");
  await expect(page.locator(".command-game.upcoming").first()).toContainText("Pregame");
  await expect(page.locator(".command-game.started").first().locator("time")).toContainText("12:00 PM");
  await expect(page.locator(".command-score-check")).toContainText("Individual score times aren’t supplied");
  await expect(page.locator(".command-source-row").first()).toContainText("Retrieved");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const main = await page.locator(".command-center-wire").boundingBox();
  const games = await page.locator(".command-center-intel").boundingBox();
  if (page.viewportSize()!.width > 1100) expect(main!.width).toBeGreaterThan(games!.width);
  else expect(games!.y).toBeGreaterThan(main!.y);
});

test("source failures stay local, offer recovery, and never masquerade as zero alerts", async ({ page }) => {
  await workspace(page, { "/injuries": null, "/leagues/1/weekly-lineup": null, "/pools/overview": null });
  await expect(page.locator("#command-injuries")).toContainText("Availability has not been verified");
  await expect(page.locator("#command-injuries").getByRole("button", { name: "Try again" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Roster injury watch/ })).toContainText("—");
  await expect(page.getByRole("link", { name: /Entries to review/ })).toContainText("—");
  await expect(page.getByRole("button", { name: "Retry lineup" })).toBeVisible();
  await expect(page.locator(".command-alert")).toHaveCount(5);
  await expect(page.locator(".command-game")).toHaveCount(16);
});

test("news keeps full-size player actions outside readable article text", async ({ page }) => {
  await workspace(page);
  const news = page.locator(".command-alert").filter({ hasText: "Brock Bowers doubtful" });
  await expect(news.getByRole("button", { name: "View Brock Bowers synopsis" })).toBeVisible();
  await expect(news.locator("h3 button, p button, a button, button a")).toHaveCount(0);
  const button = await news.getByRole("button", { name: "View Brock Bowers synopsis" }).boundingBox();
  expect(button!.height).toBeGreaterThanOrEqual(44);
  expect(await news.locator("p").evaluate((element) => getComputedStyle(element).fontSize)).toBe("16px");
});
