import assert from "node:assert/strict";
import test from "node:test";

import { ApiError } from "../src/api.ts";
import { syncYahooAtLatestSequence } from "../src/features/draft/live/yahoo-sync-state.ts";

test("every Yahoo check loads the latest sequence instead of reusing the poll's first render", async () => {
  const syncedSequences = [];
  const observedSessions = [];

  const result = await syncYahooAtLatestSequence({
    loadSession: async () => ({ current_sequence: 42 }),
    sync: async (expectedSequence) => {
      syncedSequences.push(expectedSequence);
      return { status: "ok" };
    },
    onSessionChange: (session) => observedSessions.push(session.current_sequence),
  });

  assert.deepEqual(syncedSequences, [42]);
  assert.deepEqual(observedSessions, [42]);
  assert.deepEqual(result, { status: "ok" });
});

test("a sequence race refreshes once and retries Yahoo with the new canonical sequence", async () => {
  const availableSequences = [51, 52];
  const syncedSequences = [];
  const observedSessions = [];

  const result = await syncYahooAtLatestSequence({
    loadSession: async () => ({ current_sequence: availableSequences.shift() }),
    sync: async (expectedSequence) => {
      syncedSequences.push(expectedSequence);
      if (syncedSequences.length === 1) {
        throw new ApiError(409, "The draft changed.", {
          error: "draft_conflict",
          current_sequence: 52,
        });
      }
      return { status: "ok" };
    },
    onSessionChange: (session) => observedSessions.push(session.current_sequence),
  });

  assert.deepEqual(syncedSequences, [51, 52]);
  assert.deepEqual(observedSessions, [51, 52]);
  assert.deepEqual(result, { status: "ok" });
});

test("non-conflict Yahoo failures are surfaced without retrying", async () => {
  let syncCalls = 0;

  await assert.rejects(
    syncYahooAtLatestSequence({
      loadSession: async () => ({ current_sequence: 61 }),
      sync: async () => {
        syncCalls += 1;
        throw new ApiError(503, "Yahoo is cooling down.", {
          error: "yahoo_rate_limited",
        });
      },
      onSessionChange: () => {},
    }),
    (error) => error instanceof ApiError && error.code === "yahoo_rate_limited",
  );

  assert.equal(syncCalls, 1);
});
