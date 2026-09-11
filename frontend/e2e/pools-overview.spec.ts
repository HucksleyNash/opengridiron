import { expect, test, type Page } from "@playwright/test";
import type { PoolOverviewItem } from "../src/types";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}

async function overviewFixture(page: Page, options: { failOverview?: boolean; missingSchedule?: boolean; slowEntry?: boolean } = {}) {
  const rules = { direction: "winner", basis: "straight_up", picks_per_week: 1, max_team_uses: 1, allowed_teams: [], blocked_teams: [], tie_result: "push", lock_mode: "game_start", confidence_weights: [], future_value_weight: 0.1 } as const;
  const pools = [1, 2].map((id) => ({
    id, name: id === 1 ? "Sunday Winner Pool" : "Second Pool", season: 2026, pool_type: "survivor",
    rules, entry_count: 0, entries: [], suggested_week: 1, inactive_entry_count: 0,
    schedule: { state: options.missingSchedule ? "missing" : id === 2 ? "stale" : "ready", source: "nflverse.schedule", last_success_at: null },
  })) as unknown as PoolOverviewItem[];
  const gate = deferred();
  const state = { failOverview: Boolean(options.failOverview), overviewRequests: 0, entries: [] as Array<{ pool: number; name: string }>, creates: 0, imports: 0 };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path.endsWith("/onboarding/status")) return json({ configured: true, auth_required: false, environment: "test", timezone: "America/Chicago", capabilities: { draft_suite: true } });
    if (path.endsWith("/system/health")) return json({ status: "ok" });
    if (path.endsWith("/pools/overview")) {
      state.overviewRequests++;
      return state.failOverview ? json({ message: "Pools are temporarily unavailable." }, 503) : json({ generated_at: "2026-09-09T12:00:00Z", pools });
    }
    const entryMatch = path.match(/\/pools\/(\d+)\/entries$/);
    if (entryMatch && route.request().method() === "POST") {
      const pool = Number(entryMatch[1]);
      const { name } = route.request().postDataJSON();
      state.entries.push({ pool, name });
      if (options.slowEntry && pool === 1 && state.entries.filter((entry) => entry.pool === 1).length === 1) {
        await gate.promise;
        return json({ message: "Connection interrupted." }, 503);
      }
      const entry = { id: 100 + state.entries.length, name, active: true, card_state: "draft", required_count: 1, selection_count: 0, weight_count: null, missing_count: 1 } as const;
      pools.find((item) => item.id === pool)!.entries.push(entry);
      return json({ ...entry, pool_id: pool }, 201);
    }
    if (path.endsWith("/pools") && route.request().method() === "POST") {
      state.creates++;
      return state.creates === 1 ? json({ message: "Please try again shortly." }, 503) : json({ id: 3, ...route.request().postDataJSON(), entry_count: 0 }, 201);
    }
    if (path.endsWith("/sync/nflverse/schedule")) {
      state.imports++;
      if (state.imports === 1) return json({ message: "Schedule source unavailable." }, 503);
      pools.forEach((pool) => { pool.schedule.state = "ready"; });
      return json({ imported: 16 });
    }
    return json([]);
  });
  return { state, releaseEntry: gate.resolve, pools };
}

test("entry validation, independent pending requests, and announced failure recovery", async ({ page }) => {
  const fixture = await overviewFixture(page, { slowEntry: true });
  await page.goto("/pools");
  const first = page.getByRole("form", { name: "Add entry to Sunday Winner Pool" });
  const second = page.getByRole("form", { name: "Add entry to Second Pool" });
  const input = first.getByRole("textbox", { name: "Entry name for Sunday Winner Pool" });
  await expect(input).toHaveAttribute("maxlength", "160");
  await input.fill("   ");
  await first.getByRole("button", { name: "Add entry" }).click();
  await expect(page.getByRole("alert")).toContainText("non-space character");
  await expect(input).toHaveAttribute("aria-invalid", "true");
  await expect(input).toBeFocused();
  expect(fixture.state.entries).toHaveLength(0);

  await input.fill("Sunday entry");
  await first.getByRole("button", { name: "Add entry" }).click();
  await expect(first.getByRole("button", { name: "Adding…" })).toBeDisabled();
  await expect(second.getByRole("button", { name: "Add entry" })).toBeEnabled();
  await second.getByRole("button", { name: "Add entry" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Entry “Main entry” added." })).toBeVisible();
  fixture.releaseEntry();
  await expect(page.getByRole("alert")).toContainText("Could not add this entry. Connection interrupted.");
  await expect(input).toHaveValue("Sunday entry");
  await first.getByRole("button", { name: "Add entry" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Entry “Sunday entry” added." })).toBeVisible();
  await expect(input).toHaveValue("");
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(fixture.state.entries).toEqual([{ pool: 1, name: "Sunday entry" }, { pool: 2, name: "Main entry" }, { pool: 1, name: "Sunday entry" }]);
});

test("overview and manual pool errors provide announced recovery", async ({ page }) => {
  const fixture = await overviewFixture(page, { failOverview: true });
  await page.goto("/pools");
  await expect(page.getByRole("status").filter({ hasText: "Loading your pools" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Could not load your pools", { timeout: 15000 });
  fixture.state.failOverview = false;
  await page.getByRole("button", { name: "Retry loading pools" }).click();
  await expect(page.getByRole("heading", { name: "Sunday Winner Pool" })).toBeVisible();
  await page.getByRole("button", { name: "Pool settings", exact: true }).click();
  const name = page.getByRole("textbox", { name: "Name (required)", exact: true });
  await name.fill("  ");
  await page.getByRole("button", { name: "Add pool", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("non-space character");
  expect(fixture.state.creates).toBe(0);
  await name.fill("New pool");
  await page.getByRole("button", { name: "Add pool", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Could not add this pool");
  await expect(name).toHaveValue("New pool");
  await page.getByRole("button", { name: "Add pool", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Pool “New pool” added." })).toBeVisible();
  await expect(name).toHaveValue("");
});

test("missing schedule imports once per season and can recover", async ({ page }) => {
  const fixture = await overviewFixture(page, { missingSchedule: true });
  await page.goto("/pools");
  await expect(page.getByRole("alert").first()).toContainText("Could not import the 2026 NFL schedule");
  expect(fixture.state.imports).toBe(1);
  await expect(page.getByText("Schedule missing", { exact: true })).toHaveCount(2);
  await page.getByRole("button", { name: "Retry", exact: true }).first().click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("status").filter({ hasText: "2026 NFL schedule imported." })).toHaveCount(2);
  expect(fixture.state.imports).toBe(2);
});

test("readable labels and controls retain contrast and narrow-screen reflow", async ({ page }) => {
  const fixture = await overviewFixture(page);
  fixture.pools[0].name = "VeryLongPoolNameWithoutSpaces".repeat(5);
  await page.goto("/pools");
  await page.getByRole("button", { name: "Pool settings", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Name (required)", exact: true })).toBeVisible();
  for (const width of [page.viewportSize()!.width, 320]) {
    await page.setViewportSize({ width, height: 900 });
    const metrics = await page.evaluate(() => {
      const input = document.querySelector<HTMLInputElement>(".pool-add-entry input")!;
      const style = getComputedStyle(input);
      const luminance = (rgb: string) => {
        const channels = rgb.match(/\d+/g)!.slice(0, 3).map(Number).map((v) => v / 255).map((v) => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
        return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
      };
      const contrast = (a: string, b: string) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05);
      return {
        width: innerWidth, scroll: document.documentElement.scrollWidth, labels: input.labels!.length,
        inputSize: parseFloat(style.fontSize), borderContrast: contrast(style.borderColor, style.backgroundColor),
        placeholderContrast: contrast(getComputedStyle(input, "::placeholder").color, style.backgroundColor),
        labelSizes: Array.from(document.querySelectorAll(".pool-format,.pool-week-number span,.schedule-badge,.pool-overview-page .field")).map((e) => parseFloat(getComputedStyle(e).fontSize)),
        stale: getComputedStyle(document.querySelector(".schedule-badge.stale")!).color,
        warning: getComputedStyle(document.documentElement).getPropertyValue("--yellow").trim(),
      };
    });
    expect(metrics.scroll).toBeLessThanOrEqual(metrics.width);
    expect(metrics.labels).toBe(1);
    expect(metrics.inputSize).toBe(16);
    expect(metrics.labelSizes.every((size) => size >= 11)).toBe(true);
    expect(metrics.borderContrast).toBeGreaterThanOrEqual(3);
    expect(metrics.placeholderContrast).toBeGreaterThanOrEqual(4.5);
    expect(metrics.stale).toBe("rgb(246, 185, 74)");
  }
});
