import { expect, Page, test } from "@playwright/test";

async function mockPriorityWire(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    if (path.endsWith("/onboarding/status")) return json({ configured: true, auth_required: false, environment: "test", timezone: "America/Chicago", capabilities: { draft_suite: true } });
    if (path.endsWith("/system/health")) return json({ status: "ok" });
    if (path.endsWith("/dashboard")) return json({
      leagues: [],
      pools: [],
      alerts: [
        { id: 1, title: "Starter ruled out for opener", message: "The official status changed after practice.", severity: "urgent", url: "https://www.nfl.com/news/starter-ruled-out", read: false, created_at: "2099-09-01T12:00:00Z" },
        { id: 2, title: "Internal projection updated", message: "Model-only movement has no external source.", severity: "medium", read: false, created_at: "2099-09-01T11:00:00Z" },
      ],
      snapshots: [],
      analysis_runs: [],
    });
    if (path.endsWith("/games")) return json([]);
    return json({ detail: `Unhandled mock route: ${path}` });
  });
}

test("priority wire opens sourced alerts while keeping internal alerts noninteractive", async ({ page }) => {
  await mockPriorityWire(page);
  await page.goto("/");

  const sourcedAlert = page.getByRole("link", { name: "Open article: Starter ruled out for opener" });
  await expect(sourcedAlert).toHaveAttribute("href", "https://www.nfl.com/news/starter-ruled-out");
  await expect(sourcedAlert).toHaveAttribute("target", "_blank");
  await sourcedAlert.focus();
  await expect(sourcedAlert).toBeFocused();

  const internalAlert = page.locator("article.command-alert", { hasText: "Internal projection updated" });
  await expect(internalAlert).toBeVisible();
  await expect(internalAlert.locator("a")).toHaveCount(0);
});
