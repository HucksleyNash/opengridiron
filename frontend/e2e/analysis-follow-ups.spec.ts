import { expect, test, type Page } from "@playwright/test";
import { mockFollowUps } from "./fixtures/analysis-follow-ups";
import { mockAnalysisLibrary, showAnalysisHistory } from "./fixtures/analysis-library";

async function analystDesk(page: Page) {
  const followups = await mockFollowUps(page);
  const output = { summary: "The saved recommendation", recommendations: [], risks: [], missing_information: [], citations: [] };
  const saved = [41, 42].map((id) => ({ id, task: "chat", question: `Original question ${id}`, provider: "Test analyst", model: "test", output, status: "completed", created_at: "2026-09-09T12:00:00Z", context: { league_id: 1, league_name: "My league", team_name: "My team", week: 2 } }));
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/analysis")) return route.fallback();
    if (path.endsWith("/onboarding/status")) return route.fulfill({ json: { configured: true, auth_required: false, capabilities: { draft_suite: true } } });
    if (path.endsWith("/system/health")) return route.fulfill({ json: { status: "ok" } });
    if (path.endsWith("/providers")) return route.fulfill({ json: [{ id: 3, name: "Test analyst", model: "test", enabled: true, task_defaults: ["chat"] }] });
    if (path.endsWith("/analysis/runs")) return route.fulfill({ json: [...followups.runs, ...saved] });
    return route.fulfill({ json: [] });
  });
  await mockAnalysisLibrary(page, () => [...followups.runs, ...saved]);
  return followups;
}

test("one composer saves and restores all turns, with safe sources and focus at the answer", async ({ page }) => {
  const mock = await analystDesk(page);
  await page.goto("/analysis");
  await page.getByLabel("Question", { exact: true }).fill("Compare my options");
  await page.getByRole("button", { name: "Analyze", exact: true }).click();
  const question = page.getByRole("textbox", { name: "Your follow-up question" });
  await expect(question).toBeVisible();
  await expect(page.locator("textarea")).toHaveCount(1);
  await expect(question).toHaveCSS("font-size", "16px");
  await question.fill("   ");
  await expect(page.getByRole("button", { name: "Ask follow-up" })).toBeDisabled();
  for (const [index, text] of ["  Why this choice?  ", "What would change that?"].entries()) {
    await question.fill(text);
    await page.getByRole("button", { name: "Ask follow-up" }).click();
    await expect(question).toHaveValue("");
    await expect(page.locator(`#analysis-turn-${701 + index}`)).toBeFocused();
  }
  expect(mock.requests.slice(1)).toEqual([
    { task: "chat", question: "Why this choice?", parent_run_id: 700, provider_id: 3 },
    { task: "chat", question: "What would change that?", parent_run_id: 701, provider_id: 3 },
  ]);
  await page.reload();
  for (const id of [700, 701, 702]) await expect(page.locator(".analysis-answer")).toContainText(`Follow-up answer ${id}`);
  await expect(page.locator('a[href^="javascript:"]')).toHaveCount(0);
  await showAnalysisHistory(page);
  await expect(page.locator(".analysis-library-count")).toContainText("3 conversations · 5 saved answers");
});

for (const failure of ["http", "provider"] as const) {
  test(`${failure} failures preserve the conversation and retry from the last successful answer`, async ({ page }) => {
    const mock = await analystDesk(page);
    await page.goto("/analysis?parent_run_id=41");
    const question = page.getByRole("textbox", { name: "Your follow-up question" });
    await question.fill("Why?");
    await page.getByRole("button", { name: "Ask follow-up" }).click();
    await expect(page.locator(".analysis-answer")).toContainText("Follow-up answer 700");
    mock.failNext(failure);
    const release = mock.hold();
    await question.fill("And the risk?");
    await page.getByRole("button", { name: "Ask follow-up" }).click();
    try {
      await expect(page.getByRole("button", { name: "Analyzing…", exact: true })).toBeDisabled();
      await expect(question).toHaveAttribute("readonly", "");
      await expect(page.locator(".analysis-request-status")).toContainText("reviewing your question");
    } finally { release(); }
    await expect(page.locator(".analysis-composer [role=alert]")).toContainText(failure === "http" ? "temporarily unavailable" : "timed out");
    await expect(question).toHaveValue("And the risk?");
    await expect(page.locator(".analysis-answer")).toContainText("Follow-up answer 700");
    await page.getByRole("button", { name: "Ask follow-up" }).click();
    await expect(page.locator(".analysis-answer")).toContainText("Follow-up answer 702");
    expect(mock.requests.slice(1).map((body) => body.parent_run_id)).toEqual([700, 700]);
    await page.reload();
    await expect(page.locator(".analysis-answer")).toContainText("Follow-up answer 702");
  });
}

test("switching saved analyses resets the follow-up draft, shows context and focuses the reader", async ({ page }) => {
  const mock = await analystDesk(page);
  await page.goto("/analysis?parent_run_id=41");
  const question = page.getByRole("textbox", { name: "Your follow-up question" });
  await question.fill("Unsaved question about 41");
  await showAnalysisHistory(page);
  await page.getByRole("button", { name: "Open saved analysis: Original question 42", exact: true }).click();
  await expect(page.locator("#analysis-reader-title")).toBeFocused();
  await expect(question).toHaveValue("");
  await expect(page.locator(".analysis-context-header")).toContainText("My league · My team · Week 2");
  await question.fill("Question about 42");
  await page.getByRole("button", { name: "Ask follow-up", exact: true }).click();
  await expect(page.locator(".analysis-answer")).toContainText("Follow-up answer 700");
  expect(mock.requests[0].parent_run_id).toBe(42);
});

test("late responses cannot hijack a different analysis selected with browser navigation", async ({ page }) => {
  const mock = await analystDesk(page);
  await page.goto("/analysis?parent_run_id=42");
  await showAnalysisHistory(page);
  await page.getByRole("button", { name: "Open saved analysis: Original question 41", exact: true }).click();
  await expect(page.locator("#analysis-reader-title")).toHaveText("Original question 41");
  const release = mock.hold();
  await page.getByRole("textbox", { name: "Your follow-up question" }).fill("Slow question about 41");
  await page.getByRole("button", { name: "Ask follow-up" }).click();
  await expect.poll(() => mock.requests.length).toBe(1);
  await page.goBack();
  release();
  if (await page.getByRole("button", { name: "Return to answer" }).isVisible()) await page.getByRole("button", { name: "Return to answer" }).click();
  await expect(page).toHaveURL(/parent_run_id=42/);
  await expect(page.locator(".analysis-answer")).toContainText("Original question 42");
  await expect(page.locator(".analysis-answer")).not.toContainText("Follow-up answer 700");
});

test("pending requests prevent duplicate submissions and local context switches", async ({ page }) => {
  const mock = await analystDesk(page);
  await page.goto("/analysis?parent_run_id=41");
  const release = mock.hold();
  await page.getByRole("textbox", { name: "Your follow-up question" }).fill("Continue question 41");
  await page.getByRole("button", { name: "Ask follow-up", exact: true }).click();
  try {
    await expect(page.getByRole("button", { name: "New analysis", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Analyzing…", exact: true })).toBeDisabled();
    expect(mock.requests).toHaveLength(1);
  } finally { release(); }
  await expect(page.locator(".analysis-answer")).toContainText("Follow-up answer 700");
  await expect(page.getByRole("button", { name: "New analysis", exact: true })).toBeEnabled();
});
