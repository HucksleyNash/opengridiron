import { expect, test, type Page } from "@playwright/test";
import { mockFollowUps } from "./fixtures/analysis-follow-ups";

const output = {
  summary: "Review the current roster before the next pick.",
  recommendations: ["Prioritize the available running back."],
  risks: ["Players can be selected while this review runs."],
  missing_information: [],
  citations: [],
};

async function draftReview(page: Page) {
  const api = page.request;
  const leagueResponse = await api.post("/api/v1/leagues", {
    data: { name: `Decision review ${Date.now()}`, season: 2094, roster_slots: ["QB"] },
  });
  expect(leagueResponse.ok()).toBeTruthy();
  const league = await leagueResponse.json();
  for (let index = 0; index < 8; index += 1) {
    const response = await api.post(`/api/v1/leagues/${league.id}/players`, {
      data: { name: `Review Player ${index}`, pro_team: "CHI", position: "QB", projected_points: 300 - index },
    });
    expect(response.ok()).toBeTruthy();
  }
  const sessions = [];
  for (let index = 0; index < 2; index += 1) {
    const response = await api.post(`/api/v1/leagues/${league.id}/draft-sessions`, {
      data: { kind: "mock", opponent_mode: "manual", team_count: 8, round_count: 1, owner_team_slot: 1 },
    });
    expect(response.ok()).toBeTruthy();
    const session = await response.json();
    const started = await api.post(`/api/v1/draft-sessions/${session.id}/actions/start`, {
      data: { expected_sequence: 0, idempotency_key: `review-start-${session.id}` },
    });
    expect(started.ok()).toBeTruthy();
    sessions.push((await started.json()).session);
  }
  const runs: Record<string, unknown>[] = [];
  await page.route("**/api/v1/providers", (route) => route.fulfill({ json: [
    { id: 1, name: "Default analyst", model: "fixture", enabled: true, task_defaults: ["recommendation"] },
    { id: 2, name: "Chosen analyst", model: "fixture", enabled: true, task_defaults: [] },
  ] }));
  await page.route("**/api/v1/analysis/runs", (route) => route.fulfill({ json: runs }));
  await page.goto(`/draft/${league.id}?session=${sessions[0].id}`);
  const panel = page.getByRole("region", { name: "AI decision review" });
  await expect(panel.getByRole("button", { name: "Explain these choices" })).toBeEnabled();
  return { panel, runs, league, sessions };
}

test("draft updates preserve a pending review, its answer, and selected analyst", async ({ page }) => {
  const { panel, league, sessions } = await draftReview(page);
  let release!: () => void;
  const responseReady = new Promise<void>((resolve) => { release = resolve; });
  const requests: Record<string, unknown>[] = [];
  await page.route("**/api/v1/analysis", async (route) => {
    requests.push(route.request().postDataJSON());
    await responseReady;
    await route.fulfill({ json: { run_id: 101, status: "completed", provider: "Chosen analyst", model: "fixture", output } });
  });
  await panel.getByRole("combobox", { name: "Analyst", exact: true }).selectOption("2");
  await panel.getByRole("button", { name: "Explain these choices" }).click();
  await expect(panel.getByRole("button", { name: "Reviewing evidence…" })).toBeDisabled();
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(page.getByRole("button", { name: "Resume", exact: true })).toBeVisible();
  try {
    await expect(panel.getByRole("button", { name: "Reviewing evidence…" })).toBeDisabled();
    await expect(panel.getByRole("combobox", { name: "Analyst", exact: true })).toHaveValue("2");
  } finally {
    release();
  }
  await expect(panel).toContainText(output.summary);
  await expect(panel.getByRole("status")).toContainText("The draft changed");
  await expect(panel.getByRole("link", { name: "Open in Analyst desk" })).toHaveAttribute("href", "/analysis?parent_run_id=101");
  await page.getByRole("button", { name: "Resume", exact: true }).click();
  await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  await expect(panel).toContainText(output.summary);
  expect(requests).toEqual([{ task: "recommendation", question: expect.any(String), league_id: league.id, draft_session_id: sessions[0].id, provider_id: 2 }]);
  await panel.getByRole("button", { name: "Explain these choices" }).click();
  await expect.poll(() => requests.length).toBe(2);
  await expect(panel.getByRole("status").filter({ hasText: "The draft changed" })).toHaveCount(0);
});

test("saved reviews restore after reload and remain scoped to their draft", async ({ page }) => {
  const { panel, runs, league, sessions } = await draftReview(page);
  await page.route("**/api/v1/analysis", async (route) => {
    const body = route.request().postDataJSON();
    runs.unshift({ id: 102, task: body.task, question: body.question, context: { league_id: league.id, draft_session_id: sessions[0].id }, status: "completed", provider: "Default analyst", model: "fixture", output, created_at: "2026-09-06T12:00:00Z" });
    await route.fulfill({ json: { run_id: 102, status: "completed", provider: "Default analyst", model: "fixture", output } });
  });
  await panel.getByRole("button", { name: "Explain these choices" }).click();
  await expect(panel).toContainText(output.summary);
  await page.reload();
  await expect(panel).toContainText(output.summary);
  await expect(panel).toContainText("Saved review");
  await expect(panel.getByRole("link", { name: "Open in Analyst desk" })).toHaveAttribute("href", "/analysis?parent_run_id=102");
  const followups = await mockFollowUps(page);
  const thread = panel.getByRole("region", { name: "Follow-up questions" });
  await thread.getByRole("textbox").fill("Why that position?");
  await thread.getByRole("button", { name: "Ask follow-up" }).click();
  await expect(thread).toContainText("Follow-up answer 700");
  expect(followups.requests[0]).toEqual({ task: "chat", question: "Why that position?", parent_run_id: 102, provider_id: 1 });
  await page.locator(".draft-session-switcher > button:not(.active)").filter({ hasText: "Mock · live" }).click();
  await expect(panel).not.toContainText(output.summary);
  await page.locator(".draft-session-switcher > button:not(.active)").filter({ hasText: "Mock · live" }).click();
  await expect(panel).toContainText(output.summary);
});

test("returning to a running review waits for its saved result without submitting again", async ({ page }) => {
  const { panel, runs, league, sessions } = await draftReview(page);
  let requests = 0;
  await page.route("**/api/v1/analysis", async (route) => {
    requests += 1;
    const body = route.request().postDataJSON();
    runs.unshift({ id: 105, task: body.task, question: body.question, context: { league_id: league.id, draft_session_id: sessions[0].id }, status: "running", provider: "Default analyst", model: "fixture", created_at: "2026-09-06T12:00:00Z" });
    await route.fulfill({ json: { run_id: 105, status: "running" } });
  });
  await panel.getByRole("button", { name: "Explain these choices" }).click();
  await expect.poll(() => runs.length).toBe(1);
  await page.reload();
  await expect(panel.getByRole("button", { name: "Reviewing evidence…" })).toBeDisabled();
  runs[0] = { ...runs[0], status: "completed", output };
  await expect(panel).toContainText(output.summary, { timeout: 10_000 });
  await expect(panel.getByRole("button", { name: "Explain these choices" })).toBeEnabled();
  expect(requests).toBe(1);
});

test("provider failures remain visible after a draft update and can be retried", async ({ page }) => {
  const { panel } = await draftReview(page);
  let attempts = 0;
  await page.route("**/api/v1/analysis", (route) => {
    attempts += 1;
    return route.fulfill({ json: attempts === 1
      ? { run_id: 103, status: "failed", error: "Analyst timed out. Try again." }
      : { run_id: 104, status: "completed", provider: "Default analyst", model: "fixture", output },
    });
  });
  await panel.getByRole("button", { name: "Explain these choices" }).click();
  await expect(panel.getByRole("alert")).toContainText("Analyst timed out");
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(page.getByRole("button", { name: "Resume", exact: true })).toBeVisible();
  await expect(panel.getByRole("alert")).toContainText("Analyst timed out");
  await panel.getByRole("button", { name: "Explain these choices" }).click();
  await expect(panel).toContainText(output.summary);
  await expect(panel.getByRole("alert")).toHaveCount(0);
});
