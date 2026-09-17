import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { leagueResponse } from "./fixtures/league-workspace";

async function createLeague(request: APIRequestContext, name: string, second = false) {
  const response = await request.post("/api/v1/leagues", { data: { name, season: 2026, scoring: { receptions: 1 }, roster_slots: ["RB", "WR", "BN"] } });
  expect(response.status()).toBe(201);
  const league = await response.json();
  const player = (name: string, position: string, projected_points: number, rostered_by: string | null = null, extra = {}) => ({
    name, position, projected_points, pro_team: "CHI", status: "Active", ownership: rostered_by ? "TEAM" : "FA", rostered_by, current_slot: rostered_by ? position : null,
    projection: { source: "Market test projections", period: "week", season: 2026, week: 1, scoring_basis: "league_rules", scoring: { receptions: 1 } }, ...extra,
  });
  const players = second ? [
    player("Shared Runner", "RB", 25, "Owner"), player("Second League Runner", "RB", 40),
  ] : [
    player("Owner Runner", "RB", 5, "Owner"), player("Owner Receiver", "WR", 35, "Owner"),
    player("Rival Runner", "RB", 35, "Rival"), player("Rival Receiver", "WR", 5, "Rival"),
    player("Shared Runner", "RB", 25), player("Available Receiver", "WR", 20, null, { ownership: "W" }),
    player("Depth Runner", "RB", 18), player("Depth Receiver", "WR", 12), player("Low Runner", "RB", 6),
    player("Rostered Star", "RB", 99, "Rival"), player("Unavailable Runner", "RB", 100, null, { status: "Out" }),
  ];
  const imported = await request.post(`/api/v1/leagues/${league.id}/players/import`, { multipart: { file: { name: "players.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(players)) } } });
  expect(imported.ok()).toBe(true);
  expect((await request.put(`/api/v1/leagues/${league.id}/my-team`, { data: { my_team_name: "Owner" } })).ok()).toBe(true);
  return league;
}

async function openLeague(page: Page, name: string) {
  await page.getByRole("link", { name: "Leagues", exact: true }).click();
  await page.locator(".league-card").filter({ hasText: name }).click();
}

test("player markets use each league's availability, selected team and refreshed roster", async ({ page, request }) => {
  const suffix = `${test.info().project.name}-${Date.now()}`;
  const first = await createLeague(request, `Market Alpha ${suffix}`);
  const second = await createLeague(request, `Market Bravo ${suffix}`, true);
  // Keep this availability test independent of external weekly forecast sources.
  await page.route("**/api/v1/leagues/*/weekly-lineup?*", (route) => route.fulfill({ json: { season: 2026, week: 1, forecasts: [], assignments: [], unfilled_slots: [], current_total: null, projected_total: null, projected_gain: null, partial_total: true } }));
  await page.goto(`/leagues/${first.id}`);
  const market = page.getByRole("region", { name: "Player market", exact: true });
  const rows = market.locator("tbody tr");
  await expect(rows).toHaveCount(4);
  await expect(rows.first()).toContainText("Shared Runner");
  await expect(rows.first()).toContainText("+20.0");
  await expect(market).not.toContainText("Rostered Star");
  await expect(market).not.toContainText("Unavailable Runner");
  await expect(market).not.toContainText("Second League Runner");

  const team = page.getByRole("group", { name: "Team and week" }).getByRole("combobox", { name: "Fantasy team", exact: true });
  await team.selectOption("Rival");
  await expect(rows.first()).toContainText("Available Receiver");
  await expect(rows.first()).toContainText("+15.0");
  await expect(rows.first()).toContainText("Waivers");
  await page.getByRole("combobox", { name: "NFL week", exact: true }).selectOption("2");
  await market.getByRole("link", { name: "View all available players" }).click();
  await expect(page.getByRole("tab", { name: "Waivers", exact: true })).toHaveAttribute("aria-selected", "true");
  expect(new URL(page.url()).searchParams.get("team")).toBe("Rival");
  expect(new URL(page.url()).searchParams.get("week")).toBe("2");
  await expect(page.locator(".league-waiver-table tbody tr").first()).toContainText("Available Receiver");

  await openLeague(page, second.name);
  await expect(market).toContainText(second.name);
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Second League Runner");
  await expect(market).not.toContainText("Shared Runner");
  await openLeague(page, first.name);
  await expect(rows.first()).toContainText("Shared Runner");
  await expect(market).not.toContainText("Second League Runner");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await mkdir("../.impeccable/review/player-market", { recursive: true });
  await page.screenshot({ path: `../.impeccable/review/player-market/${test.info().project.name}-page.png`, fullPage: true });
  await market.screenshot({ path: `../.impeccable/review/player-market/${test.info().project.name}-market.png` });

  // Importing an ownership change must invalidate the cached top four.
  const tools = page.locator("#league-data-tools");
  if (await tools.getAttribute("open") === null) await tools.locator(":scope > summary").click();
  await tools.getByText("Sync & imports", { exact: true }).click();
  await page.getByLabel("Player data file").setInputFiles({ name: "ownership.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify([{ name: "Shared Runner", pro_team: "CHI", position: "RB", ownership: "TEAM", rostered_by: "Rival", current_slot: "BN" }])) });
  await page.getByRole("button", { name: "Import CSV / JSON", exact: true }).click();
  await expect(page.getByText("Import complete:", { exact: false })).toBeVisible();
  await expect(market).not.toContainText("Shared Runner");
  await expect(rows.first()).toContainText("Depth Runner");
});

test("player market loading, errors and empty leagues have distinct states", async ({ page }) => {
  let fail = true;
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/waivers/page")) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      return route.fulfill(fail ? { status: 503, json: { detail: "Market unavailable" } } : { json: { items: [], total: 0, available: 0, next_offset: null, facets: { teams: [], positions: [], statuses: [] } } });
    }
    return route.fulfill({ json: leagueResponse(url.pathname, url.searchParams) });
  });
  await page.goto("/leagues/2");
  const market = page.getByRole("region", { name: "Player market", exact: true });
  await expect(market.getByRole("status")).toHaveText("Ranking available players…");
  await expect(market.getByRole("alert")).toContainText("Could not load this league’s player market");
  await expect(market).not.toContainText("No available players");
  fail = false;
  await market.getByRole("button", { name: "Try again" }).click();
  await expect(market).toContainText("No available players in My Pals");
  await expect(market.locator("table")).toHaveCount(0);
});
