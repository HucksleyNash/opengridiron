import assert from "node:assert/strict";
import test from "node:test";
import { createPlayerMatcher, isCompletePlayer, playerMatches } from "../src/features/leagues/player-links.ts";

const players = [
  { id: 1, league_id: 1, name: "Patrick Mahomes", pro_team: "KC", position: "QB" },
  { id: 2, league_id: 2, name: "Patrick Mahomes", pro_team: "KC", position: "QB" },
  { id: 3, league_id: 1, name: "C.J. Stroud", pro_team: "HOU", position: "QB" },
  { id: 4, league_id: 1, name: "De'Von Achane", pro_team: "MIA", position: "RB" },
  { id: 5, league_id: 1, name: "Rams", position: "DEF" },
];

test("mentions preserve text, punctuation and repeated full names without substring matches", () => {
  const match = createPlayerMatcher(players);
  const text = "Patrick Mahomes' outlook: PATRICK MAHOMES, C.J. Stroud and De'Von Achane. Not Patrick Mahomeson, XC.J. Stroud or CxJx Stroud. Rams.";
  const parts = match(text, 1);
  assert.equal(parts.map((part) => part.text).join(""), text);
  assert.deepEqual(parts.filter((part) => part.player).map((part) => part.player.id), [1, 1, 3, 4]);
  assert.equal(match("Patrick Mahomes", 1)[0].player.id, 1);
});

test("league context and explicit IDs never silently choose another league record", () => {
  assert.deepEqual(playerMatches({ name: "Patrick Mahomes" }, players, 2).map((p) => p.id), [2]);
  assert.equal(playerMatches({ name: "Patrick Mahomes", league_id: 9 }, players).length, 0);
  assert.equal(playerMatches({ name: "Patrick Mahomes", pro_team: "BUF" }, players).length, 0);
  assert.equal(createPlayerMatcher(players)("Patrick Mahomes")[0].player.id, undefined);
  assert.equal(createPlayerMatcher(players)("Patrick Mahomes", 9)[0].player.id, undefined);
});

test("a draft snapshot must load canonical information instead of fabricating roster status", () => {
  assert.equal(isCompletePlayer({ ...players[0], projected_points: 300, floor: 200, ceiling: 400, risk: 0.2 }), false);
  assert.equal(isCompletePlayer({ ...players[0], ownership: "FA", projected_points: 0, floor: 0, ceiling: 0, risk: 0, ros_value: null }), true);
});
