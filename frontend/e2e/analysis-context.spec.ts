import { expect, test, type Page } from "@playwright/test";

async function mockContext(page: Page) {
  const requests: { method: string; path: string; body: Record<string, unknown> }[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let provider = { id: 3, name: "Context analyst", provider_type: "openai", model: "fixture-model", enabled: true, task_defaults: ["chat"], has_api_key: true, base_url: null };
  const league = { id: 1, name: "Context league", season: 2026, source: "manual", team_names: ["My Team"], my_team_name: "My Team", roster_slots: ["RB"], scoring: {}, player_count: 1 };
  const runs: Record<string, unknown>[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    const json = (body: unknown) => route.fulfill({ json: body });
    if (method !== "GET") requests.push({ method, path, body: route.request().postDataJSON() });
    if (path.endsWith("/onboarding/status")) return json({ configured: true, auth_required: false, environment: "test", capabilities: { draft_suite: true } });
    if (path.endsWith("/system/health")) return json({ status: "ok" });
    if (path.endsWith("/providers/3") && method === "PATCH") {
      provider = { ...provider, ...route.request().postDataJSON() };
      return json(provider);
    }
    if (path.endsWith("/providers")) return json([provider]);
    if (path.endsWith("/leagues")) return json([league]);
    if (path.endsWith("/integrations/yahoo/scraper/status")) return json({ configured: false, has_cookie: false, league_urls: [] });
    if (path.endsWith("/notifications/config")) return json({ enabled: false });
    if (path.endsWith("/analysis/runs")) return json(runs);
    if (path.endsWith("/analysis") && method === "POST") {
      const id = 100 + runs.length;
      const output = { summary: `Saved answer ${id}`, recommendations: ["Review the saved evidence"], risks: [], missing_information: [], citations: [] };
      runs.unshift({ id, task: "chat", question: route.request().postDataJSON().question, provider: provider.name, model: provider.model, status: "completed", output, created_at: "2026-09-04T12:00:00Z", completed_at: "2026-09-04T12:00:01Z" });
      return json({ run_id: id, status: "completed", provider: provider.name, model: provider.model, output });
    }
    return json([]);
  });
  return { requests, errors };
}

test("report follow-ups retain frozen scope, continue the parent, and clear lineage for a new conversation", async ({ page }) => {
  const mock = await mockContext(page);
  await page.goto("/analysis?league_id=1&league_report_id=42&team_name=My+Team&week=2");
  await expect(page.getByRole("status").filter({ hasText: "Saved report #42" })).toContainText("My Team · Week 2");
  await expect(page.getByRole("combobox", { name: "League context", exact: true })).toHaveValue("1");
  await expect(page.getByRole("combobox", { name: "League context", exact: true })).toBeDisabled();
  await expect(page.getByRole("spinbutton", { name: "Week", exact: true })).toHaveValue("2");
  const question = page.getByPlaceholder("Which lineup decision has the biggest evidence-backed edge this week?");
  await question.fill("Explain this saved lineup");
  await page.getByRole("button", { name: "Analyze", exact: true }).click();
  await expect(page.locator(".analysis-answer")).toContainText("Saved answer 100");
  expect(mock.requests[0].body).toMatchObject({ league_id: 1, league_report_id: 42, team_name: "My Team", week: 2 });
  expect(mock.requests[0].body).not.toHaveProperty("parent_run_id");
  await question.fill("What is the main risk?");
  await page.getByRole("button", { name: "Analyze", exact: true }).click();
  await expect(page.locator(".analysis-answer")).toContainText("Saved answer 101");
  expect(mock.requests[1].body).toMatchObject({ parent_run_id: 100, league_report_id: 42, league_id: 1, team_name: "My Team", week: 2 });
  await page.getByRole("button", { name: "Start a new conversation", exact: true }).click();
  await expect(page).toHaveURL(/\/analysis$/);
  await expect(page.getByRole("combobox", { name: "League context", exact: true })).toBeEnabled();
  await question.fill("Start from new evidence");
  await page.getByRole("button", { name: "Analyze", exact: true }).click();
  await expect(page.locator(".analysis-answer")).toContainText("Saved answer 102");
  for (const key of ["parent_run_id", "league_report_id", "league_id", "team_name", "week", "pool_id", "draft_session_id"]) expect(mock.requests[2].body).not.toHaveProperty(key);
  expect(mock.errors).toEqual([]);
});

test("editing provider defaults sends PATCH and preserves its stored key", async ({ page }) => {
  const mock = await mockContext(page);
  await page.goto("/settings");
  const editor = page.locator(".provider-row").filter({ hasText: "Context analyst" }).locator("details");
  await editor.locator("summary").click();
  await editor.getByRole("textbox", { name: "Model", exact: true }).fill("fixture-next-model");
  await editor.getByRole("checkbox", { name: "Analyst chat", exact: true }).uncheck();
  await editor.getByRole("checkbox", { name: "Automatic news review", exact: true }).check();
  await editor.getByRole("checkbox", { name: "League, draft and pool recommendations", exact: true }).check();
  await editor.getByRole("button", { name: "Save provider", exact: true }).click();
  await expect(editor.getByRole("status")).toHaveText("Provider saved.");
  const patch = mock.requests.find((request) => request.method === "PATCH");
  expect(patch?.path).toBe("/api/v1/providers/3");
  expect(patch?.body.model).toBe("fixture-next-model");
  expect([...(patch?.body.task_defaults as string[])].sort()).toEqual(["news", "recommendation"]);
  expect(patch?.body).not.toHaveProperty("api_key");
  expect(mock.requests.filter((request) => request.method !== "PATCH")).toEqual([]);
  expect(mock.errors).toEqual([]);
});
