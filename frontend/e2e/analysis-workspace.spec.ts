import { expect, test, type Page } from "@playwright/test";
import { mockAnalysisLibrary, showAnalysisHistory } from "./fixtures/analysis-library";

async function workspace(page: Page) {
  const runs: Record<string, any>[] = Array.from({ length: 112 }, (_, index) => ({
    id: index + 1, task: "chat", question: `Saved decision ${index + 1}`, model: "test", provider: "Fixture analyst", status: index === 9 ? "failed" : "completed",
    created_at: new Date(Date.UTC(2026, 8, 1, 0, index)).toISOString(),
    context: { league_id: 1, league_name: "My league", team_name: "A very long fantasy team name that should remain readable", week: index % 2 + 1 },
    output: { summary: index === 0 ? "This old answer mentions the needle-player." : "Review the saved evidence before choosing the lineup. ".repeat(8), recommendations: Array.from({ length: 12 }, (_, i) => `Recommendation ${i + 1}: compare the available evidence and preserve uncertainty.`), risks: ["Availability can change before kickoff."], missing_information: ["Final injury report."], citations: ["https://www.nfl.com/news/report"] },
  }));
  runs.push({ ...runs[1], id: 113, parent_run_id: 2, question: "First branch", created_at: "2026-09-09T13:00:00Z" }, { ...runs[1], id: 114, parent_run_id: 2, question: "Second branch", created_at: "2026-09-09T14:00:00Z" });
  const requests: Record<string, any>[] = [];
  await page.route("**/api/v1/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/onboarding/status")) return route.fulfill({ json: { configured: true, auth_required: false, capabilities: { draft_suite: true } } });
    if (path.endsWith("/system/health")) return route.fulfill({ json: { status: "ok" } });
    if (path.endsWith("/providers")) return route.fulfill({ json: [{ id: 1, name: "Fixture analyst", model: "test", enabled: true, task_defaults: ["chat"] }, { id: 2, name: "Disabled analyst", enabled: false, model: "test", task_defaults: [] }] });
    if (path.endsWith("/leagues")) return route.fulfill({ json: [{ id: 1, name: "My league", team_names: ["My team"] }] });
    if (path.endsWith("/pools")) return route.fulfill({ json: [{ id: 1, name: "Survivor pool" }] });
    if (path.endsWith("/pools/1/entries")) return route.fulfill({ json: [{ id: 1, name: "Entry one", active: true }, { id: 2, name: "Entry two", active: true }] });
    if (path.endsWith("/analysis") && route.request().method() === "POST") {
      const body = route.request().postDataJSON(); requests.push(body);
      const id = 115 + requests.length;
      const result = { run_id: id, status: "completed", provider: "Fixture analyst", model: "test", output: runs[0].output, context: body };
      runs.push({ ...result, id, question: body.question, task: "chat", created_at: "2026-09-09T15:00:00Z", parent_run_id: body.parent_run_id });
      return route.fulfill({ json: result });
    }
    return route.fulfill({ json: [] });
  });
  await mockAnalysisLibrary(page, () => runs);
  return requests;
}

test("search finds an old full-answer match and filters retain the selected reader", async ({ page }) => {
  await workspace(page); await page.goto("/analysis"); await showAnalysisHistory(page);
  await page.getByRole("searchbox", { name: "Search analyses" }).fill("needle-player");
  await expect(page.locator(".analysis-library-row")).toHaveCount(1);
  await page.getByRole("button", { name: "Open saved analysis: Saved decision 1", exact: true }).click();
  await expect(page.locator("#analysis-reader-title")).toBeFocused();
  await expect(page.locator(".analysis-reader")).toContainText("needle-player");
  await expect(page).toHaveURL(/parent_run_id=1/);
  await page.reload(); await expect(page.locator(".analysis-reader")).toContainText("needle-player");
  await showAnalysisHistory(page);
  await expect(page.getByRole("searchbox")).toHaveValue("needle-player");
  await page.getByRole("searchbox").fill("no such saved analysis");
  await expect(page.getByText("No conversations match these filters.")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page.locator(".analysis-library-row")).toHaveCount(25);
  await page.getByText("Filter history", { exact: true }).click();
  await page.getByRole("combobox", { name: "Status", exact: true }).selectOption("failed");
  await expect(page.locator(".analysis-library-row")).toHaveCount(1);
  await expect(page.locator(".analysis-library-row")).toContainText("Failed");
});

test("pagination reaches records beyond 100 without changing an open answer", async ({ page }) => {
  await workspace(page); await page.goto("/analysis?parent_run_id=1"); await showAnalysisHistory(page);
  for (let pageNumber = 2; pageNumber <= 5; pageNumber++) {
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await expect(page.locator(".analysis-pagination")).toContainText(`Page ${pageNumber}`);
    await expect(page.locator(".analysis-library-count")).not.toContainText("Loading");
  }
  await expect(page.getByRole("button", { name: "Open saved analysis: Saved decision 1", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Next", exact: true })).toBeDisabled();
  await expect(page).toHaveURL(/parent_run_id=1/);
});

test("saved branches restore their ancestor without merging other replies", async ({ page }) => {
  await workspace(page); await page.goto("/analysis?parent_run_id=113");
  await expect(page.locator(".analysis-turn")).toHaveCount(2);
  await expect(page.locator(".analysis-conversation")).toContainText("First branch");
  await expect(page.locator(".analysis-conversation")).not.toContainText("Second branch");
  await page.getByText("Other replies in this conversation (1)", { exact: true }).click();
  await page.getByRole("button", { name: /Second branch/ }).click();
  await expect(page.locator(".analysis-conversation")).toContainText("Second branch");
  await expect(page.locator(".analysis-conversation")).not.toContainText("First branch");
  await expect(page.locator("textarea")).toHaveCount(1);
});

test("pool entry is explicit, invalid weeks cannot submit, and disabled providers are excluded", async ({ page }) => {
  const requests = await workspace(page); await page.goto("/analysis");
  await page.getByRole("combobox", { name: "Context", exact: true }).selectOption("pool:1");
  await page.getByRole("textbox", { name: "Question", exact: true }).fill("Review entry two");
  await expect(page.getByRole("button", { name: "Analyze", exact: true })).toBeDisabled();
  await page.getByRole("combobox", { name: "Pool entry", exact: true }).selectOption("2");
  await page.getByRole("spinbutton", { name: "Week", exact: true }).fill("19");
  await page.getByRole("button", { name: "Analyze", exact: true }).click();
  expect(requests).toHaveLength(0);
  await page.getByRole("spinbutton", { name: "Week", exact: true }).fill("2");
  await page.locator(".analysis-provider-settings summary").click();
  await expect(page.getByRole("combobox", { name: "AI analyst", exact: true }).locator("option")).toHaveCount(2);
  await page.getByRole("button", { name: "Analyze", exact: true }).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0]).toMatchObject({ pool_id: 1, pool_entry_id: 2, week: 2 });
});

test("missing saved links show a recoverable error instead of an empty answer", async ({ page }) => {
  await workspace(page); await page.goto("/analysis?parent_run_id=99999");
  await expect(page.locator(".analysis-reader [role=alert]")).toContainText("unavailable");
  await expect(page.locator("#analysis-reader-title")).toBeFocused();
  await page.getByRole("button", { name: "New analysis", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Question", exact: true })).toBeVisible();
});

test("mobile history keeps readable rows and restores the previous document position", async ({ page }, info) => {
  test.skip(info.project.name !== "mobile", "Checks narrow mobile history scrolling");
  await page.setViewportSize({ width: 320, height: 720 });
  await workspace(page); await page.goto("/analysis"); await showAnalysisHistory(page);
  const row = page.getByRole("button", { name: "Open saved analysis: Saved decision 108", exact: true });
  await row.scrollIntoViewIfNeeded();
  const before = await page.evaluate(() => window.scrollY);
  await row.click();
  await expect(page.locator("#analysis-reader-title")).toBeFocused();
  await page.getByRole("button", { name: "Back to history", exact: true }).click();
  await expect(row).toBeFocused();
  expect(Math.abs(await page.evaluate(() => window.scrollY) - before)).toBeLessThan(3);
  const widths = await row.evaluate(element => ({ row: element.clientWidth, title: element.querySelector("strong")!.clientWidth }));
  expect(widths.title).toBeGreaterThan(240);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
});
