import { expect, request as playwrightRequest, test } from "@playwright/test";

test("A reopened full draft can be finished again", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "desktop state-transition regression");
  const api = await playwrightRequest.newContext({ baseURL: "http://127.0.0.1:4173" });
  const token = `${Date.now()}-${testInfo.workerIndex}`;
  const leagueResponse = await api.post("/api/v1/leagues", {
    data: {
      name: `Reopen regression ${token}`,
      season: 2096,
      roster_slots: ["QB"],
    },
  });
  expect(leagueResponse.ok()).toBeTruthy();
  const league = await leagueResponse.json();

  const playerIds: number[] = [];
  for (let index = 0; index < 8; index += 1) {
    const response = await api.post(`/api/v1/leagues/${league.id}/players`, {
      data: {
        source_id: `reopen-${token}-${index}`,
        name: `Reopen Player ${index + 1}`,
        pro_team: "CHI",
        position: "QB",
        projected_points: 300 - index,
        floor: 240 - index,
        ceiling: 340 - index,
        risk: 0.2,
      },
    });
    expect(response.ok()).toBeTruthy();
    playerIds.push((await response.json()).id);
  }

  const sessionResponse = await api.post(`/api/v1/leagues/${league.id}/draft-sessions`, {
    data: {
      kind: "mock",
      opponent_mode: "manual",
      team_count: 8,
      round_count: 1,
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
  let currentSequence = (await startResponse.json()).session.current_sequence;
  const recommendationResponse = await api.get(`/api/v1/draft-sessions/${session.id}/recommendations`);
  expect(recommendationResponse.ok()).toBeTruthy();
  const recommendationSnapshotId = (await recommendationResponse.json()).snapshot_id;

  for (let index = 0; index < playerIds.length; index += 1) {
    const response = await api.post(`/api/v1/draft-sessions/${session.id}/events`, {
      data: {
        type: "pick_recorded",
        expected_sequence: currentSequence,
        idempotency_key: `pick-${token}-${index}`,
        player_id: playerIds[index],
        recommendation_snapshot_id: index === 0 ? recommendationSnapshotId : undefined,
      },
    });
    expect(response.ok()).toBeTruthy();
    currentSequence = (await response.json()).session.current_sequence;
  }

  await page.goto(`/draft/${league.id}`);
  await expect(page.getByRole("heading", { name: /is ready/ })).toBeVisible();
  await page.getByRole("button", { name: "Reopen draft" }).click();
  await expect(page.getByRole("heading", { name: "Who should I take?" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Finish draft" })).toBeVisible();
  await page.getByRole("button", { name: "Finish draft" }).click();
  await expect(page.getByRole("heading", { name: /is ready/ })).toBeVisible();

  const finalSession = await api.get(`/api/v1/draft-sessions/${session.id}`);
  expect((await finalSession.json()).status).toBe("COMPLETE");
  await api.dispose();
});
