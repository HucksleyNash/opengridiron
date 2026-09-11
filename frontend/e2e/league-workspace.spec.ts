import { expect, test } from "@playwright/test";
import { leagueResponse } from "./fixtures/league-workspace";

test("one roster, consistent objectives, source columns, and deferred workspace queries", async ({ page }) => {
  const requests: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url()); requests.push(url.pathname + url.search);
    await route.fulfill({ json: leagueResponse(url.pathname, url.searchParams) });
  });
  await page.goto("/leagues/2");
  const table = page.locator(".league-unified-table");
  await expect(table.locator("tbody tr")).toHaveCount(15);
  await expect(page.getByText("1 proposed swap", { exact: true })).toBeVisible();
  await expect(table.getByRole("button", { name: "View Dak Prescott synopsis" })).toHaveCount(1);
  expect(requests.some((url) => url.includes("waivers/page"))).toBe(false);
  await page.getByRole("button", { name: "Floor", exact: true }).click();
  await expect(table.getByRole("row").filter({ hasText: "Dak Prescott" })).toContainText("7.2");
  await expect(table).toContainText("Floor pts");
  await page.getByRole("button", { name: "Changes only", exact: true }).click();
  await expect(table.locator("tbody tr")).toHaveCount(2);
  await expect(table).toContainText("Courtland Sutton");
  await expect(table).toContainText("C.J. Stroud");
  await page.getByRole("button", { name: "Show full roster" }).click();
  await page.getByRole("button", { name: "Source projections", exact: true }).click();
  await expect(table).toHaveCount(0);
  await expect(page.locator(".league-source-table tbody tr")).toHaveCount(15);
  await expect(page.locator(".league-source-table")).toContainText("535.8");
  await expect(page.locator(".league-source-context")).toContainText("Full season");
  await page.getByRole("tab", { name: "Analysis", exact: true }).click();
  await expect(page.getByText("Run league analysis to save your first report.", { exact: false })).toBeVisible();
  const weeklyCount = requests.filter((url) => url.includes("weekly-lineup")).length;
  await page.getByRole("combobox", { name: "NFL week", exact: true }).selectOption("2");
  await expect(page.getByText("No saved report for Deep in your endzone! · Week 2", { exact: false })).toBeVisible();
  expect(requests.filter((url) => url.includes("weekly-lineup")).length).toBe(weeklyCount);
  await page.getByRole("tab", { name: "Waivers", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Available players" })).toBeVisible();
  await expect(page.locator(".league-waiver-table")).toContainText("Season pts");
  await expect(page.locator(".league-waiver-table")).toContainText("394.1");
  await expect(page.getByText("Weekly lineup impact unavailable", { exact: false })).toBeVisible();
  await page.getByRole("tab", { name: "Roster", exact: true }).click();
  await page.getByRole("button", { name: "Weekly lineup", exact: true }).click();
  await expect(table).toContainText("Wk 2");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("zero lineup gains are explicitly gains; failed weekly forecasts retain current slots", async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/weekly-lineup")) return route.fulfill({ status: 503, json: { detail: "Forecast unavailable" } });
    const body = leagueResponse(url.pathname, url.searchParams);
    if (url.pathname.endsWith("/waivers/page") && "items" in body) { body.items[0].ranking_basis = "weekly_lineup_gain"; body.items[0].expected_value = 0; }
    await route.fulfill({ json: body });
  });
  await page.goto("/leagues/2");
  await expect(page.getByRole("alert").filter({ hasText: "Could not calculate the lineup" })).toBeVisible({ timeout: 15000 });
  await expect(page.locator(".league-unified-table tbody tr")).toHaveCount(15);
  await expect(page.locator(".league-unified-table")).toContainText("Recommendation unavailable");
  await expect(page.getByRole("button", { name: "Changes only" })).toBeDisabled();
  await page.getByRole("tab", { name: "Waivers", exact: true }).click();
  await expect(page.locator(".league-waiver-table")).toContainText("Modeled lineup gain");
  await expect(page.locator(".league-waiver-table tbody tr")).toContainText("0.0");
});
