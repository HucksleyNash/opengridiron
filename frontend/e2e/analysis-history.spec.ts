import { mockAnalysisLibrary, showAnalysisHistory } from "./fixtures/analysis-library";
import { expect, Page, test } from "@playwright/test";

const savedOutput = {
  summary: "Player A has the safer workload.",
  recommendations: ["Start Player A"],
  risks: ["Monitor the late injury report"],
  missing_information: ["Final inactive list"],
  citations: ["https://example.com/injury-report"],
};

async function mockAnalyst(page: Page) {
  const runs = [{
    id: 41,
    task: "chat",
    question: "Should I start Player A or Player B?",
    provider: "Codex CLI",
    model: "gpt-history",
    status: "completed",
    output: savedOutput,
    error: null,
    input_tokens: 120,
    output_tokens: 48,
    created_at: "2026-09-01T14:30:00Z",
    completed_at: "2026-09-01T14:30:04Z",
  }];

  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
    if (path.endsWith("/onboarding/status")) return json({ configured: true, auth_required: false, environment: "test", timezone: "America/Chicago", capabilities: { draft_suite: true } });
    if (path.endsWith("/system/health")) return json({ status: "ok" });
    if (path.endsWith("/providers")) return json([{ id: 3, name: "Codex CLI", provider_type: "codex", model: "gpt-history", enabled: true, task_defaults: ["chat"], has_api_key: false }]);
    if (path.endsWith("/leagues")) return json([]);
    if (path.endsWith("/pools")) return json([]);
    if (path.endsWith("/analysis/runs")) return json(runs);
    if (path.endsWith("/analysis") && route.request().method() === "POST") {
      const payload = route.request().postDataJSON();
      const output = {
        summary: "The new analysis was saved.",
        recommendations: ["Use the new recommendation"],
        risks: [],
        missing_information: [],
        citations: [],
      };
      runs.unshift({
        id: 42,
        task: "chat",
        question: payload.question,
        provider: "Codex CLI",
        model: "gpt-history",
        status: "completed",
        output,
        error: null,
        input_tokens: 90,
        output_tokens: 30,
        created_at: "2026-09-01T15:00:00Z",
        completed_at: "2026-09-01T15:00:03Z",
      });
      return json({ run_id: 42, provider: "Codex CLI", model: "gpt-history", status: "completed", output, error: null });
    }
    return json({ detail: `Unhandled mock route: ${path}` }, 404);
  });
  await mockAnalysisLibrary(page, () => runs);
}

test("saved analyst results can be reviewed after returning to the page", async ({ page }) => {
  await mockAnalyst(page);
  await page.goto("/analysis");

  await showAnalysisHistory(page);
  await expect(page.getByRole("heading", { name: "Analysis history" })).toBeVisible();
  await page.getByRole("button", { name: /Should I start Player A or Player B/ }).click();
  await expect(page.locator(".analysis-question")).toContainText("Should I start Player A or Player B?");
  await expect(page.locator(".analysis-answer")).toContainText("Player A has the safer workload.");
  await expect(page.locator(".analysis-answer")).toContainText("Final inactive list");
});

test("a new analysis appears in saved history automatically", async ({ page }) => {
  await mockAnalyst(page);
  await page.goto("/analysis");

  await page.getByPlaceholder("Which lineup decision has the biggest evidence-backed edge this week?").fill("What changed since the last analysis?");
  await page.getByRole("button", { name: "Analyze", exact: true }).click();

  await expect(page.locator(".analysis-answer")).toContainText("The new analysis was saved.");
  await showAnalysisHistory(page);
  await expect(page.getByRole("button", { name: /What changed since the last analysis/ })).toBeVisible();
  await expect(page.locator(".analysis-library-count")).toContainText("2 saved");
});
