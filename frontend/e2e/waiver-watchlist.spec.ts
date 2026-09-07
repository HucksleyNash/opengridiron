import { expect, Page, test } from "@playwright/test";

const positions = [...Array<string>(25).fill("QB"), "RB", "WR", "TE", "K", "DEF", "D/ST", "LB", "DB", "DL"];
const players = positions.map((position, index) => ({
  id: index + 1, league_id: 1, name: index === 28 ? "Late Round Kicker" : `Candidate ${index + 1}`,
  pro_team: index % 2 ? "BUF" : "CHI", position, status: index === 28 ? "Questionable" : "Active",
  ownership: index >= 28 ? (index % 2 ? "WAIVERS" : "W") : "FA", rostered_by: null,
  projected_points: 100 - index, floor: 80 - index, ceiling: 120 - index, ros_value: 200 - index, risk: 0.2,
}));
const waivers = players.map((player, index) => ({
  player_id: player.id, rank: index + 1, subject: `${player.name} (${player.position}, ${player.pro_team})`,
  expected_value: 100 - index, confidence: 0.8, rationale: ["Stored projection"], data_as_of: "2026-09-03T12:00:00Z",
}));

async function mockLeague(page: Page, options: { empty?: boolean; fail?: boolean; delay?: boolean } = {}) {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path.endsWith("/onboarding/status")) return json({ configured: true, auth_required: false, environment: "test", timezone: "America/Chicago", capabilities: { draft_suite: true } });
    if (path.endsWith("/system/health")) return json({ status: "ok" });
    if (path.endsWith("/draft-sessions")) return json([]);
    if (path.endsWith("/roster")) return json([]);
    if (path.endsWith("/projection-leaders")) return json(players.slice(0, 10));
    if (path.endsWith("/waivers/page")) {
      if (options.delay) await new Promise((resolve) => setTimeout(resolve, 500));
      if (options.fail) return json({ detail: "Waiver service unavailable" }, 503);
      const normalize = (position: string) => ["D/ST", "DST"].includes(position) ? "DEF" : position;
      const role = url.searchParams.get("role");
      const team = url.searchParams.get("team");
      const status = url.searchParams.get("status");
      const availability = url.searchParams.get("availability");
      const terms = (url.searchParams.get("search") || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
      const rows = (options.empty ? [] : waivers).map((item) => ({ ...item, player: players[item.player_id - 1] })).filter(({ player }) => {
        const onWaivers = ["W", "WAIVERS"].includes(player.ownership);
        return (!role || (role === "FLEX" ? ["RB", "WR", "TE"] : [role]).includes(normalize(player.position)))
          && (!team || player.pro_team === team) && (!status || player.status === status)
          && (!availability || (availability === "waivers" ? onWaivers : !onWaivers))
          && terms.every((term) => `${player.name} ${player.pro_team} ${player.position} ${normalize(player.position)}`.toLowerCase().includes(term));
      });
      const offset = Number(url.searchParams.get("offset") || 0), limit = Number(url.searchParams.get("limit") || 10);
      return json({ items: rows.slice(offset, offset + limit), total: rows.length, available: options.empty ? 0 : players.length, next_offset: offset + limit < rows.length ? offset + limit : null,
        facets: { teams: ["CHI", "BUF"], statuses: ["Active", "Questionable"], positions: [...new Set(positions)] } });
    }
    if (path.endsWith("/leagues/1") || path.endsWith("/leagues/2")) return json({ id: path.endsWith("/2") ? 2 : 1, name: "Waiver coverage", season: 2026, source: "manual", scoring: {}, roster_slots: ["QB", "RB", "WR", "TE", "K", "DEF", "FLEX", "LB"], player_count: players.length });
    return json([]);
  });
}

test("all waiver roles are reachable and filters combine with search", async ({ page }) => {
  await mockLeague(page);
  await page.goto("/leagues/1");
  const watchlist = page.getByRole("region", { name: "Waiver watchlist" });
  const rows = watchlist.locator("tbody tr");
  await expect(rows).toHaveCount(10);
  await expect(watchlist.getByRole("status")).toHaveText("Showing 10 of 34 matching players · 34 available");
  await watchlist.getByRole("button", { name: "Show more players" }).click();
  await expect(rows).toHaveCount(20);
  await watchlist.getByRole("button", { name: "Show more players" }).click();
  await watchlist.getByRole("button", { name: "Show more players" }).click();
  await expect(rows).toHaveCount(34);

  const role = watchlist.getByLabel("Position / role");
  for (const position of ["QB", "RB", "WR", "TE", "K", "DEF", "LB", "DB", "DL", "FLEX"]) {
    await role.selectOption(position);
    await expect(rows).toHaveCount(position === "QB" ? 10 : position === "DEF" ? 2 : position === "FLEX" ? 3 : 1);
  }
  await role.selectOption("K");
  await watchlist.getByRole("combobox", { name: "NFL team", exact: true }).selectOption("CHI");
  await watchlist.getByRole("combobox", { name: "Availability", exact: true }).selectOption("waivers");
  await watchlist.getByLabel("Player status").selectOption("Questionable");
  await watchlist.getByLabel("Search players").fill("  lAtE   KICKER  ");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Late Round Kicker");
  await expect(rows.first().locator("td").first()).toContainText("29");
  await watchlist.getByRole("combobox", { name: "Availability", exact: true }).selectOption("free-agent");
  await expect(watchlist.getByText("No players match your filters.")).toBeVisible();
  await watchlist.getByRole("button", { name: "Clear filters" }).click();
  await expect(rows).toHaveCount(10);
  await expect(role).toHaveValue("");
  await expect(watchlist.getByLabel("Search players")).toHaveValue("");

  await watchlist.getByLabel("Search players").fill("BUF LB");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Candidate 32");
  await expect(watchlist.getByRole("button", { name: "Show more players" })).toHaveCount(0);
  await watchlist.getByRole("button", { name: "Clear filters" }).click();

  const layout = await watchlist.evaluate((element) => ({ width: element.getBoundingClientRect().width, viewport: window.innerWidth, documentWidth: document.documentElement.scrollWidth }));
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewport);
  for (const control of await watchlist.locator("input, select, button").all()) {
    expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  }
  await watchlist.screenshot({ path: `/private/tmp/waiver-watchlist-${test.info().project.name}.png` });
});

test("empty and loading waiver states are explicit", async ({ page }) => {
  await mockLeague(page, { empty: true, delay: true });
  await page.goto("/leagues/1");
  const watchlist = page.getByRole("region", { name: "Waiver watchlist" });
  await expect(watchlist.getByText("Loading waiver watchlist")).toBeVisible();
  await expect(watchlist.getByText(/^No available players\./)).toBeVisible();
  await expect(watchlist.getByRole("button", { name: "Show more players" })).toHaveCount(0);
});

test("waiver errors can be retried", async ({ page }) => {
  const options = { fail: true };
  await mockLeague(page, options);
  await page.goto("/leagues/1");
  const watchlist = page.getByRole("region", { name: "Waiver watchlist" });
  await expect(watchlist.getByText("Waiver service unavailable")).toBeVisible({ timeout: 15_000 });
  options.fail = false;
  await watchlist.getByRole("button", { name: "Retry watchlist" }).click();
  await expect(watchlist.locator("tbody tr")).toHaveCount(10);
});
