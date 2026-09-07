import assert from "node:assert/strict";
import test from "node:test";

import {
  assignConfidenceWeight,
  reapplyUnlockedAttempts,
} from "../src/features/pools/pool-card-state.ts";

test("confidence weight changes swap atomically instead of creating a duplicate", () => {
  const picks = [
    { game_id: 1, team: "CHI", confidence: 1 },
    { game_id: 2, team: "KC", confidence: 2 },
  ];
  assert.deepEqual(assignConfidenceWeight(picks, 1, 2), [
    { game_id: 1, team: "CHI", confidence: 2 },
    { game_id: 2, team: "KC", confidence: 1 },
  ]);
});

test("conflict reapply preserves canonical locks and drops attempts on newly locked games", () => {
  const attempted = [
    { slot: 1, game_id: 1, team: "GB", confidence: null },
    { slot: 2, game_id: 2, team: "KC", confidence: null },
    { slot: 3, game_id: 3, team: "DAL", confidence: null },
  ];
  const canonical = [
    { id: 10, slot: 1, game_id: 1, team: "CHI", locked: true },
  ];
  assert.deepEqual(
    reapplyUnlockedAttempts("survivor", attempted, canonical, new Set([2])),
    [
      { slot: 1, game_id: 1, team: "CHI", confidence: null },
      { slot: 3, game_id: 3, team: "DAL", confidence: null },
    ],
  );
});
