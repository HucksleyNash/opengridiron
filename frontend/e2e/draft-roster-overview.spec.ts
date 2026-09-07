import { expect, request as playwrightRequest, test } from "@playwright/test";

test("Mock draft keeps the owner roster above recommendations with bye weeks", async ({ page }, testInfo) => {
  test.skip(!["chromium", "mobile"].includes(testInfo.project.name), "desktop and mobile roster hierarchy check");
  const api = await playwrightRequest.newContext({ baseURL: "http://127.0.0.1:4173" });
  const token = `${Date.now()}-${testInfo.workerIndex}`;
  const team = `R${Date.now().toString(36).slice(-3)}`.toUpperCase();
  const opponent = `X${Date.now().toString(36).slice(-3)}`.toUpperCase();
  const leagueResponse = await api.post("/api/v1/leagues", {
    data: {
      name: `Roster overview ${token}`,
      season: 2097,
      scoring: { receptions: 1.0 },
      roster_slots: ["QB", "RB", "WR", "TE", "FLEX", "BENCH"],
    },
  });
  expect(leagueResponse.ok()).toBeTruthy();
  const league = await leagueResponse.json();

  const playerIds: number[] = [];
  for (let index = 0; index < 12; index += 1) {
    const position = ["RB", "WR", "QB", "TE"][index % 4];
    const response = await api.post(`/api/v1/leagues/${league.id}/players`, {
      data: {
        source_id: `roster-overview-${token}-${index}`,
        name: index === 0 ? "Bye Week Runner" : `Roster Candidate ${index + 1}`,
        pro_team: team,
        position,
        projected_points: 300 - index,
        floor: 240 - index,
        ceiling: 340 - index,
        risk: 0.2,
      },
    });
    expect(response.ok()).toBeTruthy();
    playerIds.push((await response.json()).id);
  }

  for (const week of Array.from({ length: 18 }, (_, index) => index + 1).filter((week) => week !== 10)) {
    const response = await api.post("/api/v1/games", {
      data: {
        season: 2097,
        week,
        away_team: team,
        home_team: opponent,
        kickoff: "2097-09-01T17:00:00Z",
      },
    });
    expect(response.ok()).toBeTruthy();
  }

  const sessionResponse = await api.post(`/api/v1/leagues/${league.id}/draft-sessions`, {
    data: {
      kind: "mock",
      team_count: 8,
      round_count: 2,
      owner_team_slot: 1,
      owner_team_name: "Open Gridiron",
    },
  });
  expect(sessionResponse.ok()).toBeTruthy();
  const session = await sessionResponse.json();
  const startResponse = await api.post(`/api/v1/draft-sessions/${session.id}/actions/start`, {
    data: { expected_sequence: 0, idempotency_key: `start-${token}` },
  });
  expect(startResponse.ok()).toBeTruthy();
  const started = (await startResponse.json()).session;
  const recommendationResponse = await api.get(`/api/v1/draft-sessions/${session.id}/recommendations`);
  expect(recommendationResponse.ok()).toBeTruthy();
  const recommendations = await recommendationResponse.json();
  const pickResponse = await api.post(`/api/v1/draft-sessions/${session.id}/events`, {
    data: {
      type: "pick_recorded",
      expected_sequence: started.current_sequence,
      idempotency_key: `pick-${token}`,
      player_id: playerIds[0],
      recommendation_snapshot_id: recommendations.snapshot_id,
    },
  });
  expect(pickResponse.ok()).toBeTruthy();

  await page.goto(`/draft/${league.id}`);
  const roster = page.locator(".draft-roster-overview");
  await expect(roster.getByRole("heading", { name: "Open Gridiron" })).toBeVisible();
  await expect(roster.getByText("Bye Week Runner")).toBeVisible();
  await expect(roster.getByText("BYE 10")).toBeVisible();
  await expect(roster.getByText("1 RB")).toBeVisible();
  await expect(page.locator(".draft-candidate").first().getByText("BYE 10")).toBeVisible();
  await expect(page.locator(".draft-board-row").first().getByText("BYE 10")).toBeVisible();
  await expect(page.locator(".draft-roster-panel")).toHaveCount(0);
  expect(await page.evaluate(() => {
    const rosterElement = document.querySelector(".draft-roster-overview");
    const decisionElement = document.querySelector(".draft-decision-zone");
    return Boolean(rosterElement && decisionElement && (rosterElement.compareDocumentPosition(decisionElement) & Node.DOCUMENT_POSITION_FOLLOWING));
  })).toBeTruthy();
  await page.screenshot({ path: `/private/tmp/draft-roster-after-${testInfo.project.name}.png`, fullPage: true });
  await api.dispose();
});
