import assert from "node:assert/strict";
import test from "node:test";

import {
  filterNewsItems,
  formatSourceLabel,
  uniqueAlertsByTitle,
  weeklyCardProgress,
} from "../src/ui-display-state.ts";

test("internal source names become concise user-facing labels", () => {
  assert.equal(formatSourceLabel("yahoo_scrape"), "Yahoo");
  assert.equal(formatSourceLabel("yahoo_oauth"), "Yahoo");
  assert.equal(formatSourceLabel("nflverse.draft_model"), "NFLverse draft model");
});

test("dashboard alerts keep the newest occurrence of each headline", () => {
  const alerts = [
    { id: 3, title: "Quarterback update" },
    { id: 2, title: "Quarterback update" },
    { id: 1, title: "Injury report" },
  ];
  assert.deepEqual(uniqueAlertsByTitle(alerts).map((alert) => alert.id), [3, 1]);
});

test("news filtering searches headlines and summaries within a category", () => {
  const items = [
    { id: 1, title: "Quarterback returns", excerpt: "Cleared for Week 1", category: "injury", severity: "high", canonical_url: "#" },
    { id: 2, title: "Roster move", excerpt: "Veteran quarterback signed", category: "transaction", severity: "medium", canonical_url: "#" },
  ];
  assert.deepEqual(filterNewsItems(items, "quarterback", "injury").map((item) => item.id), [1]);
  assert.deepEqual(filterNewsItems(items, "quarterback", "all").map((item) => item.id), [1, 2]);
});

test("survivor progress never renders confidence-only null weights", () => {
  assert.equal(weeklyCardProgress("survivor", 1, 1, null), "1/1 selection");
  assert.equal(weeklyCardProgress("confidence", 2, 2, 2), "2/2 selections · 2/2 weights");
});
