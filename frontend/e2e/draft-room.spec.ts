import { expect, Page, request as playwrightRequest, test } from "@playwright/test";

async function seedDraft(project: string) {
  const api = await playwrightRequest.newContext({ baseURL: "http://127.0.0.1:4173" });
  const fixtureResponse = await api.get("/api/v1/testing/draft-fixtures/small?now=2026-08-30T20:00:00Z");
  expect(fixtureResponse.ok()).toBeTruthy();
  const fixture = await fixtureResponse.json();
  expect(fixture.yahoo_observations.at(-1).result).toBe("proposal");

  const leagueResponse = await api.post("/api/v1/leagues", {
    data: {
      name: `Draft fixture ${project} ${Date.now()}`,
      season: 2026,
      scoring: { receptions: 1.0 },
      roster_slots: ["QB", "RB", "WR", "TE", "FLEX", "BENCH"],
    },
  });
  expect(leagueResponse.ok()).toBeTruthy();
  const league = await leagueResponse.json();
  const playerIds: number[] = [];
  const sourceIds: string[] = [];
  for (const player of fixture.players) {
    const sourceId = `fixture-${project}-${player.id}-${Date.now()}`;
    const response = await api.post(`/api/v1/leagues/${league.id}/players`, {
      data: {
        source_id: sourceId,
        name: player.name,
        pro_team: player.pro_team,
        position: player.position,
        projected_points: player.projected_points,
        floor: player.floor,
        ceiling: player.ceiling,
        ros_value: player.ros_value,
        risk: player.risk,
      },
    });
    expect(response.ok()).toBeTruthy();
    playerIds.push((await response.json()).id);
    sourceIds.push(sourceId);
  }
  return { api, league, playerIds, sourceIds, players: fixture.players };
}

async function recordFirstBoardPlayer(page: Page) {
  await page.locator(".draft-board-row").first().getByRole("button", { name: /Record|Confirm|Use replacement/ }).click();
}

test("Draft Room fixture completes a sequence-safe manual draft", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "desktop journey");
  const seeded = await seedDraft(testInfo.project.name);
  await page.goto(`/draft/${seeded.league.id}`);

  await expect(page.getByRole("heading", { name: "Draft Suite" })).toBeVisible();
  await expect(page.getByText("never submits a pick to Yahoo", { exact: false })).toBeVisible();
  await page.getByLabel("Teams").fill("8");
  await page.getByLabel("Rounds").fill("1");
  await page.getByLabel("Your slot").fill("3");
  await page.getByLabel("Opponent picks").selectOption("manual");
  await page.getByRole("button", { name: "Create draft room" }).click();
  await expect(page.getByText("Ready to draft")).toBeVisible();
  await page.getByRole("button", { name: /Enter the draft room/ }).click();

  await expect(page.getByRole("heading", { name: "Who should I take?" })).toBeVisible();
  await page.keyboard.press("/");
  await expect(page.getByLabel("Search available players")).toBeFocused();
  await page.keyboard.press("Escape");

  await recordFirstBoardPlayer(page);
  await expect(page.getByText("Pick recorded.")).toBeVisible();
  await recordFirstBoardPlayer(page);
  await expect(page.getByText("You’re on the clock")).toBeVisible();
  await page.locator(".draft-candidate").first().getByRole("button", { name: "Record pick" }).click();
  await expect(page.getByText("No more owner turns")).toBeVisible();

  const sessions = await seeded.api.get(`/api/v1/leagues/${seeded.league.id}/draft-sessions`);
  const session = (await sessions.json())[0];
  const boardBeforeExternal = await seeded.api.get(`/api/v1/draft-sessions/${session.id}/board`);
  const canonicalBoard = await boardBeforeExternal.json();
  const externalPlayer = canonicalBoard.available_players.at(-1);
  const externalPick = await seeded.api.post(`/api/v1/draft-sessions/${session.id}/events`, {
    data: {
      type: "pick_recorded",
      expected_sequence: session.current_sequence,
      idempotency_key: `external-${Date.now()}`,
      player_id: externalPlayer.id,
    },
  });
  expect(externalPick.ok()).toBeTruthy();

  await recordFirstBoardPlayer(page);
  await expect(page.getByText(/board changed.*refreshed/i)).toBeVisible();
  await expect(page.getByText("Confirm again")).toBeVisible();
  await page.locator(".draft-board-row.intent").getByRole("button", { name: "Record" }).click();
  await expect(page.getByText("Pick recorded.")).toBeVisible();

  await page.reload();
  await expect(page.getByText("Pick 6 · Round 1")).toBeVisible();
  await page.locator(".draft-pick-list").getByRole("button", { name: "Correct" }).first().click();
  await expect(page.getByText(/Correcting pick/)).toBeVisible();
  await page.locator(".draft-board-row").first().getByRole("button", { name: "Use replacement" }).click();
  await expect(page.getByText(/Correction recorded/)).toBeVisible();

  await page.getByRole("button", { name: /Pause/ }).click();
  await expect(page.getByRole("button", { name: /Resume/ })).toBeVisible();
  await page.getByRole("button", { name: /Resume/ }).click();
  await expect(page.getByRole("button", { name: /Pause/ })).toBeVisible();

  for (let completed = 5; completed < 8; completed += 1) {
    await recordFirstBoardPlayer(page);
  }
  await expect(page.getByText("Draft complete")).toBeVisible();
  await expect(page.getByRole("heading", { name: /is ready/ })).toBeVisible();
  await expect(page.getByText("No-hindsight replay")).toBeVisible();
  await seeded.api.dispose();
});

test("Draft Room automatically advances mock opponents and stops for the owner", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "desktop automatic-mock journey");
  const seeded = await seedDraft(`${testInfo.project.name}-automatic`);
  const csv = [
    "source_id,overall_rank,position",
    ...seeded.sourceIds.map((sourceId, index) => `${sourceId},${index + 1},${seeded.players[index].position}`),
  ].join("\n");
  const previewResponse = await seeded.api.post(
    `/api/v1/leagues/${seeded.league.id}/draft-inputs/preview?input_type=ranking`,
    {
      multipart: {
        file: {
          name: "yahoo-ranking.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(csv),
        },
      },
    },
  );
  expect(previewResponse.ok()).toBeTruthy();
  const preview = await previewResponse.json();
  const commitResponse = await seeded.api.post(
    `/api/v1/leagues/${seeded.league.id}/draft-inputs/previews/${preview.preview_id}/commit`,
    { data: { content_hash: preview.content_hash, acknowledge_warnings: true } },
  );
  expect(commitResponse.ok()).toBeTruthy();

  await page.goto(`/draft/${seeded.league.id}`);
  await page.getByLabel("Teams").fill("8");
  await page.getByLabel("Rounds").fill("1");
  await page.getByLabel("Your slot").fill("3");
  await expect(page.getByLabel("Opponent picks")).toHaveValue("automatic");
  await page.getByRole("button", { name: "Create draft room" }).click();
  await expect(page.getByText("Automatic opponents ready")).toBeVisible();
  await expect(page.getByRole("button", { name: /Enter the draft room/ })).toBeEnabled();
  await page.getByRole("button", { name: /Enter the draft room/ }).click();

  await expect(page.getByText("You’re on the clock")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator(".draft-pick-list > div")).toHaveCount(2);
  await expect(page.locator(".draft-pick-list").getByText("Team 1", { exact: false })).toBeVisible();
  await expect(page.locator(".draft-pick-list").getByText("Team 2", { exact: false })).toBeVisible();
  await page.locator(".draft-candidate").first().getByRole("button", { name: "Record pick" }).click();
  await expect(page.locator(".draft-pick-list > div")).toHaveCount(4, { timeout: 10_000 });
  await seeded.api.dispose();
});

test("Live Yahoo source promotes approval mode to safe auto-approve", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "desktop live-source journey");
  const seeded = await seedDraft(`${testInfo.project.name}-yahoo-modes`);
  await page.route("**/api/v1/draft-sessions/*/sync/yahoo", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ready",
        counts: { applied: 0, confirmed: 0, proposed: 0 },
      }),
    });
  });

  await page.goto(`/draft/${seeded.league.id}`);
  await page.getByLabel("Session type").selectOption("live");
  await page.getByLabel("Teams").fill("8");
  await page.getByLabel("Rounds").fill("1");
  await page.getByLabel("Your slot").fill("3");
  await page.getByRole("button", { name: "Create draft room" }).click();
  await page.getByRole("button", { name: /Enter the draft room/ }).click();

  await expect(page.getByRole("heading", { name: "Manual only" })).toBeVisible();
  await page.getByRole("button", { name: "Use scraper with approval" }).click();
  await expect(page.getByRole("heading", { name: "Yahoo approval" })).toBeVisible();

  await page.getByRole("button", { name: "Enable auto-approve" }).click();
  await expect(page.getByRole("group", { name: "Confirm Yahoo auto-approve" })).toBeVisible();
  await page.getByRole("button", { name: "Auto-approve safe picks" }).click();
  await expect(page.getByRole("heading", { name: "Yahoo auto-approve" })).toBeVisible();
  await expect(page.getByText("Clean next-in-sequence Yahoo picks", { exact: false })).toBeVisible();

  await page.getByRole("button", { name: "Require approval" }).click();
  await expect(page.getByRole("heading", { name: "Yahoo approval" })).toBeVisible();
  await seeded.api.dispose();
});

test("Draft Room mobile companion does not overflow", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "mobile-only layout assertion");
  const seeded = await seedDraft(testInfo.project.name);
  const sessionResponse = await seeded.api.post(`/api/v1/leagues/${seeded.league.id}/draft-sessions`, {
    data: {
      kind: "mock",
      team_count: 8,
      round_count: 1,
      owner_team_slot: 3,
      owner_team_name: "Open Gridiron",
    },
  });
  const session = await sessionResponse.json();
  await seeded.api.post(`/api/v1/draft-sessions/${session.id}/actions/start`, {
    data: { expected_sequence: 0, idempotency_key: `mobile-start-${Date.now()}` },
  });
  await page.goto(`/draft/${seeded.league.id}`);
  await expect(page.getByRole("heading", { name: "Who should I take?" })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await expect(page.locator(".draft-live-strip")).toBeVisible();
  await expect(page.locator(".draft-candidate")).toHaveCount(3);
  await seeded.api.dispose();
});

test("Draft Room tablet keeps the decision cockpit readable", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "tablet", "tablet-only layout assertion");
  const seeded = await seedDraft(testInfo.project.name);
  const sessionResponse = await seeded.api.post(`/api/v1/leagues/${seeded.league.id}/draft-sessions`, {
    data: {
      kind: "mock",
      team_count: 8,
      round_count: 1,
      owner_team_slot: 3,
      owner_team_name: "Open Gridiron",
    },
  });
  const session = await sessionResponse.json();
  await seeded.api.post(`/api/v1/draft-sessions/${session.id}/actions/start`, {
    data: { expected_sequence: 0, idempotency_key: `tablet-start-${Date.now()}` },
  });
  await page.goto(`/draft/${seeded.league.id}`);
  await expect(page.getByRole("heading", { name: "Who should I take?" })).toBeVisible();
  await expect(page.locator(".draft-candidate")).toHaveCount(3);

  const layout = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    candidateWidths: [...document.querySelectorAll<HTMLElement>(".draft-candidate")].map((candidate) => candidate.getBoundingClientRect().width),
  }));
  expect(layout.overflow).toBeLessThanOrEqual(1);
  expect(Math.min(...layout.candidateWidths)).toBeGreaterThan(240);
  await expect(page.locator(".draft-context-rail")).toBeVisible();
  await seeded.api.dispose();
});

test("Draft page archives and restores stale sessions", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "desktop cleanup journey");
  const seeded = await seedDraft(`${testInfo.project.name}-cleanup`);
  const sessions: Array<{ id: number }> = [];
  for (let index = 0; index < 2; index += 1) {
    const response = await seeded.api.post(`/api/v1/leagues/${seeded.league.id}/draft-sessions`, {
      data: {
        kind: "mock",
        team_count: 8,
        round_count: 1,
        owner_team_slot: 3,
        owner_team_name: "Open Gridiron",
      },
    });
    expect(response.ok()).toBeTruthy();
    sessions.push(await response.json());
  }

  await page.goto(`/draft/${seeded.league.id}`);
  await page.getByRole("button", { name: /Manage/ }).click();
  await expect(page.getByRole("region", { name: "Manage draft sessions" })).toBeVisible();
  await expect(page.locator(".draft-session-switcher button").filter({ hasText: "Mock · ready" })).toHaveCount(2);

  await page.getByRole("button", { name: `Archive draft ${sessions[0].id}` }).click();
  await expect(page.getByRole("button", { name: `Restore draft ${sessions[0].id}` })).toBeVisible();
  await expect(page.locator(".draft-session-switcher button").filter({ hasText: "Mock · ready" })).toHaveCount(1);

  await page.getByRole("button", { name: `Restore draft ${sessions[0].id}` }).click();
  await expect(page.getByRole("button", { name: `Archive draft ${sessions[0].id}` })).toBeVisible();
  await expect(page.locator(".draft-session-switcher button").filter({ hasText: "Mock · ready" })).toHaveCount(2);
  await seeded.api.dispose();
});
