import { expect, test } from "@playwright/test";
import { leagueResponse } from "./fixtures/league-workspace";

const cases = [
  { zone: "America/Chicago", kickoff: "2026-09-14T00:20:00+00:00", expected: "Sun, Sep 13, 7:20 PM CDT" },
  { zone: "America/Los_Angeles", kickoff: "2026-09-14T00:20:00Z", expected: "Sun, Sep 13, 5:20 PM PDT" },
  { zone: "Asia/Tokyo", kickoff: "2026-09-14T00:20:00Z", expected: "Mon, Sep 14, 9:20 AM GMT+9" },
  { zone: "Asia/Kolkata", kickoff: "2026-09-14T00:20:00Z", expected: "Mon, Sep 14, 5:50 AM GMT+5:30" },
  { zone: "America/Chicago", kickoff: "2026-11-02T01:20:00Z", expected: "Sun, Nov 1, 7:20 PM CST" },
];

for (const { zone, kickoff, expected } of cases) {
  test.describe(`${zone} ${kickoff}`, () => {
    test.use({ timezoneId: zone, locale: "en-US" });
    test("roster game times use the device zone in both views and survive withheld recommendations", async ({ page }, testInfo) => {
      await page.route("**/api/v1/**", async (route) => {
        const url = new URL(route.request().url());
        const body = leagueResponse(url.pathname, url.searchParams);
        if (url.pathname.endsWith("/weekly-lineup") && "forecasts" in body) {
          const nextWeek = url.searchParams.get("week") === "2";
          Object.assign(body, { error: "Lineup recommendations withheld for stale inputs", assignments: [] });
          body.forecasts.forEach((forecast, index) => Object.assign(forecast, {
            kickoff: index === 2 ? "invalid" : index === 3 ? "2026-09-14T00:20:00" : index === 4 ? null : nextWeek ? "2026-09-21T00:20:00Z" : kickoff,
            opponent: "PHI", bye: index === 1,
          }));
        }
        await route.fulfill({ json: body });
      });
      await page.goto("/leagues/2");
      const table = page.locator(".league-unified-table");
      await expect(table.locator("tbody tr")).toHaveCount(15);
      const row = table.getByRole("row").filter({ hasText: "Dak Prescott" });
      await expect(row.locator(".league-roster-game")).toHaveText(`vs PHI · ${expected}`);
      await expect(row.locator("time")).toHaveAttribute("datetime", kickoff);
      await expect(row).toContainText("Recommendation unavailable");
      await expect(table.getByRole("row").filter({ hasText: "De'Von Achane" }).locator(".league-roster-game")).toHaveText("Bye week");
      for (const name of ["RJ Harvey", "Amon-Ra St. Brown", "Zay Flowers"]) {
        await expect(table.getByRole("row").filter({ hasText: name }).locator(".league-roster-game")).toHaveText("vs PHI · Game time unavailable");
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      if (zone === "America/Chicago" && kickoff.includes("09-14")) {
        await page.locator("#current-roster").screenshot({ path: testInfo.outputPath("roster.png") });
      }
      await page.getByRole("button", { name: "Source projections", exact: true }).click();
      const sourceRow = page.locator(".league-source-table").getByRole("row").filter({ hasText: "Dak Prescott" });
      await expect(sourceRow.locator(".league-roster-game")).toHaveText(`vs PHI · ${expected}`);
      await page.getByRole("combobox", { name: "NFL week", exact: true }).selectOption("2");
      await expect(sourceRow.locator("time")).toHaveAttribute("datetime", "2026-09-21T00:20:00Z");
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
  });
}

test("missing schedule data does not claim a bye", async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    await route.fulfill({ json: leagueResponse(url.pathname, url.searchParams) });
  });
  await page.goto("/leagues/2");
  await expect(page.locator(".league-unified-table .league-roster-game").first()).toHaveText("Game time unavailable");
  await expect(page.locator(".league-unified-table")).not.toContainText("Bye week");
});
