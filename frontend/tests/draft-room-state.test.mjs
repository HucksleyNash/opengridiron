import assert from "node:assert/strict";
import test from "node:test";

import {
  preferredAnalysisDraftSession,
  resolveDraftLeague,
} from "../src/draft-room-state.ts";

const leagues = [
  { id: 2, name: "My Pals" },
  { id: 1, name: "Newbanantasy" },
];

test("an explicit draft route resolves that league instead of the first league", () => {
  assert.equal(resolveDraftLeague("1", leagues)?.id, 1);
});

test("the global draft route deliberately resolves the first available league", () => {
  assert.equal(resolveDraftLeague(undefined, leagues)?.id, 2);
});

test("an invalid explicit league does not silently fall back to another league", () => {
  assert.equal(resolveDraftLeague("999", leagues), undefined);
});

test("an empty league list has no active draft league", () => {
  assert.equal(resolveDraftLeague(undefined, []), undefined);
});

test("analysis defaults to the live draft even when a newer completed session exists", () => {
  const sessions = [
    { id: 7, status: "COMPLETE" },
    { id: 6, status: "LIVE" },
    { id: 5, status: "PAUSED" },
  ];

  assert.equal(preferredAnalysisDraftSession(sessions)?.id, 6);
});

test("analysis falls back through paused, ready, setup, and completed sessions", () => {
  assert.equal(
    preferredAnalysisDraftSession([
      { id: 8, status: "ABANDONED" },
      { id: 5, status: "PAUSED" },
      { id: 4, status: "READY" },
    ])?.id,
    5,
  );
  assert.equal(
    preferredAnalysisDraftSession([
      { id: 4, status: "READY" },
      { id: 3, status: "SETUP" },
    ])?.id,
    4,
  );
  assert.equal(
    preferredAnalysisDraftSession([{ id: 7, status: "COMPLETE" }])?.id,
    7,
  );
});

test("analysis ignores archived and abandoned draft sessions", () => {
  assert.equal(
    preferredAnalysisDraftSession([
      { id: 9, status: "LIVE", archived_at: "2026-09-01T00:00:00Z" },
      { id: 8, status: "ABANDONED" },
    ]),
    undefined,
  );
});
