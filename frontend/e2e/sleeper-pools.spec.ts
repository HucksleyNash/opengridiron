import { expect, test } from "@playwright/test";
import type { Pool, PoolOverviewItem, SleeperPoolInfo } from "../src/types";

test("Sleeper import, refresh recovery, and confirmed local deletion", async ({ page }) => {
  const payload = { url: "https://sleeper.com/leagues/1234567890123456789", username: "exampleowner" };
  const rules: Pool["rules"] = {
    direction: "winner", basis: "straight_up", picks_per_week: 1, max_team_uses: 1,
    allowed_teams: [], blocked_teams: [], tie_result: "eliminate", lock_mode: "game_start",
    confidence_weights: [], future_value_weight: 0.1,
  };
  let source: SleeperPoolInfo = {
    league_id: "1234567890123456789", url: payload.url, name: "Example survivor pool",
    season: 2026, status: "in_season", current_week: 1, capacity: 500,
    participant_count: 120, entry_count: 150, commissioners: ["Example commissioner"],
    username: payload.username, user_id: "111",
    entries: [{ roster_id: 30, name: "Example owner · Entry 30", eliminated: false }],
    rules, settings: { weekly_pick_limit: 1, num_picks_allowed_per_team: 1 },
    scoring_settings: {}, fetched_at: "2026-09-09T12:00:00Z",
    warnings: ["Sleeper picks and pick history are not imported. Submit your actual picks on Sleeper."],
    unsupported: [],
  };
  const poolId = 51;
  let imported = false;
  let previews = 0;
  let imports = 0;
  let refreshes = 0;
  let deletions = 0;
  const pool = (): Pool => ({
    id: poolId, name: source.name, season: source.season, pool_type: "survivor",
    entry_count: 1, rules, sleeper: source,
  });
  const overviewPool = (): PoolOverviewItem => ({
    ...pool(), suggested_week: 1, inactive_entry_count: 0,
    schedule: { state: "ready", source: "nflverse.schedule", last_success_at: source.fetched_at },
    entries: [{
      id: 61, name: "Example owner · Entry 30", active: true, card_state: "draft",
      required_count: 1, selection_count: 0, weight_count: null, missing_count: 1,
    }],
  });

  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    const json = (body: unknown, status = 200) => route.fulfill({
      status, contentType: "application/json", body: JSON.stringify(body),
    });
    if (path.endsWith("/onboarding/status")) return json({
      configured: true, auth_required: false, environment: "test",
      timezone: "America/Chicago", capabilities: { draft_suite: true },
    });
    if (path.endsWith("/system/health")) return json({ status: "ok" });
    if (path.endsWith("/pools/overview")) return json({
      generated_at: source.fetched_at, pools: imported ? [overviewPool()] : [],
    });
    if (path.endsWith("/integrations/sleeper/pools/preview") && method === "POST") {
      expect(route.request().postDataJSON()).toEqual(payload);
      previews += 1;
      return json(source);
    }
    if (path.endsWith("/integrations/sleeper/pools/import") && method === "POST") {
      expect(route.request().postDataJSON()).toEqual(payload);
      imports += 1;
      imported = true;
      return json(pool());
    }
    if (path.endsWith(`/pools/${poolId}/sleeper/refresh`) && method === "POST") {
      refreshes += 1;
      if (refreshes === 1) return json({
        error: "sleeper_unavailable", message: "Sleeper is unavailable. Saved data is unchanged.",
      }, 502);
      source = { ...source, name: "Updated survivor pool", fetched_at: "2026-09-09T12:05:00Z" };
      return json(pool());
    }
    if (path.endsWith(`/pools/${poolId}`) && method === "DELETE") {
      deletions += 1;
      imported = false;
      return route.fulfill({ status: 204 });
    }
    if (path.endsWith("/pools")) return json(imported ? [pool()] : []);
    return json([]);
  });

  await page.goto("/pools");
  await expect(page.getByRole("heading", { name: "No pools yet" })).toBeVisible();
  await page.getByRole("button", { name: "Pool settings", exact: true }).click();
  await page.getByLabel("Sleeper pool URL or league ID").fill(payload.url);
  await page.getByLabel("Sleeper username (optional)").fill(payload.username);
  await page.getByRole("button", { name: "Preview pool", exact: true }).click();
  await expect(page.getByRole("heading", { name: source.name, exact: true })).toBeVisible();
  await expect(page.getByText("150 entries · 120 participants · Capacity 500")).toBeVisible();
  await expect(page.getByText(source.warnings[0], { exact: true })).toBeVisible();
  await expect(page.locator(".pool-overview-card")).toHaveCount(0);
  expect(previews).toBe(1);
  expect(imports).toBe(0);

  await page.getByRole("button", { name: "Import pool", exact: true }).click();
  const card = page.locator(".pool-overview-card");
  await expect(card).toHaveCount(1);
  await expect(card.getByRole("heading", { name: "Example survivor pool", exact: true })).toBeVisible();
  await expect(card.getByRole("link", { name: /Example owner · Entry 30/ })).toBeVisible();
  expect(imports).toBe(1);
  await card.getByText("Sleeper pool details", { exact: true }).click();
  await card.getByRole("button", { name: "Refresh Sleeper details", exact: true }).click();
  await expect(card.getByRole("alert")).toHaveText("Sleeper is unavailable. Saved data is unchanged.");
  await expect(card.getByRole("heading", { name: "Example survivor pool", exact: true })).toBeVisible();
  await expect(card.getByRole("link", { name: /Example owner · Entry 30/ })).toBeVisible();

  await card.getByRole("button", { name: "Refresh Sleeper details", exact: true }).click();
  await expect(card.getByRole("heading", { name: "Updated survivor pool", exact: true })).toBeVisible();
  await expect(card.getByRole("status")).toHaveText("Sleeper details refreshed.");
  await expect(card.getByRole("alert")).toHaveCount(0);
  expect(refreshes).toBe(2);

  const deleteButton = card.getByRole("button", { name: "Delete Updated survivor pool", exact: true });
  await deleteButton.click();
  await expect(card.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
  await card.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(card.getByRole("button", { name: "Delete permanently", exact: true })).toHaveCount(0);
  await expect(deleteButton).toBeFocused();
  expect(deletions).toBe(0);

  await deleteButton.click();
  await expect(card.getByText("Your pool and picks on Sleeper will remain available.")).toBeVisible();
  await card.getByRole("button", { name: "Delete permanently", exact: true }).click();
  await expect(card).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "No pools yet" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "deleted from Open Gridiron" })).toHaveText("“Updated survivor pool” deleted from Open Gridiron.");
  await expect(page.getByRole("button", { name: "Pool settings", exact: true })).toBeFocused();
  expect(deletions).toBe(1);
});
