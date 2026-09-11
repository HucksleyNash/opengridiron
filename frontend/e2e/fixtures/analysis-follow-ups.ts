import type { Page } from "@playwright/test";

export async function mockFollowUps(page: Page) {
  const requests: Record<string, unknown>[] = [];
  const runs: Record<string, unknown>[] = [];
  let failure: "http" | "provider" | undefined;
  let pending: Promise<void> | undefined;
  await page.route("**/api/v1/analysis", async (route) => {
    const body = route.request().postDataJSON();
    requests.push(body);
    const id = 700 + requests.length - 1;
    if (pending) await pending;
    if (failure) {
      const kind = failure;
      failure = undefined;
      return route.fulfill({ status: kind === "http" ? 503 : 200, json: kind === "http"
        ? { detail: "Analyst temporarily unavailable" }
        : { run_id: id, status: "failed", error: "Analyst timed out" } });
    }
    const output = {
      summary: `Follow-up answer ${id}`,
      recommendations: ["Compare the saved alternatives."],
      risks: ["The role could change."],
      missing_information: ["Final injury report."],
      citations: ["https://example.com/evidence", "javascript:alert(1)"],
    };
    const result = { run_id: id, status: "completed", provider: "Test analyst", model: "test", output, parent_run_id: body.parent_run_id ?? null, league_report_id: body.league_report_id ?? null };
    runs.unshift({ ...result, id, question: body.question, task: "chat", created_at: new Date().toISOString() });
    return route.fulfill({ json: result });
  });
  return {
    requests, runs,
    failNext: (kind: "http" | "provider") => { failure = kind; },
    hold: () => {
      let release!: () => void;
      pending = new Promise<void>((resolve) => { release = resolve; });
      return () => { pending = undefined; release(); };
    },
  };
}
