import { expect, Page, test } from "@playwright/test";

async function mockCommandCenterRefresh(page: Page, postedPaths: string[]) {
  let newsFetched = false;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown) => route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    if (request.method() === "POST") postedPaths.push(path);
    if (path.endsWith("/onboarding/status")) return json({ configured: true, auth_required: false, environment: "test", timezone: "America/Chicago", capabilities: { draft_suite: true } });
    if (path.endsWith("/system/health")) return json({ status: "ok" });
    if (path.endsWith("/dashboard")) return json({
      leagues: [],
      pools: [],
      alerts: [],
      snapshots: [],
      news_sources: newsFetched ? [{ id: 1, name: "NFL News", enabled: true, official: true, last_fetched_at: "2099-09-01T12:00:00Z" }] : [],
      analysis_runs: [],
    });
    if (path.endsWith("/games")) return json([]);
    if (path.endsWith("/data-sources")) return json([
      { key: "sleeper", name: "Sleeper player status" },
      { key: "ffc", name: "Fantasy Football Calculator ADP" },
    ]);
    if (/\/data-sources\/(sleeper|ffc)\/fetch$/.test(path)) return json({ status: "available", row_count: 200 });
    if (path.endsWith("/news/sources")) return json([
      { id: 1, name: "NFL News", enabled: true },
      { id: 2, name: "NFL Injuries", enabled: true },
      { id: 3, name: "Disabled test source", enabled: false },
    ]);
    if (path.endsWith("/integrations/yahoo/scraper/sync")) {
      await new Promise((resolve) => setTimeout(resolve, 750));
      return json({ players: 320, ranges_modeled: 280, nflverse_matched: 275 });
    }
    if (/\/news\/sources\/\d+\/fetch$/.test(path)) {
      await new Promise((resolve) => setTimeout(resolve, 750));
      newsFetched = true;
      return json({ status: "ok", created: 4 });
    }
    return json({ detail: `Unhandled mock route: ${path}` });
  });
}

test("Refresh sources updates Yahoo, NFLverse models, and every enabled news source", async ({ page }) => {
  const postedPaths: string[] = [];
  await mockCommandCenterRefresh(page, postedPaths);
  await page.goto("/");

  const refresh = page.getByRole("button", { name: "Refresh sources" });
  await refresh.click();
  await expect(refresh).toBeDisabled();
  await expect(refresh).toContainText("Refreshing");
  await expect(page.getByRole("status")).toHaveText("Refreshed Yahoo roster/market, NFLverse draft models, 2 news sources, Sleeper player status, and Fantasy Football Calculator ADP.");
  await expect(page.locator(".command-source-row", { hasText: "NFL News" })).toContainText("0s");

  expect(postedPaths).toEqual(expect.arrayContaining([
    "/api/v1/integrations/yahoo/scraper/sync",
    "/api/v1/news/sources/1/fetch",
    "/api/v1/news/sources/2/fetch",
    "/api/v1/data-sources/sleeper/fetch",
    "/api/v1/data-sources/ffc/fetch",
  ]));
  expect(postedPaths).not.toContain("/api/v1/news/sources/3/fetch");
  await expect(page).toHaveURL("/");
});
