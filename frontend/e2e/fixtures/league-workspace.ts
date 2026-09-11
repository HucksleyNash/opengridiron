const teamName = "Deep in your endzone!";
const names = ["Dak Prescott", "De'Von Achane", "RJ Harvey", "Amon-Ra St. Brown", "Zay Flowers", "Trey McBride", "Kyle Pitts Sr.", "C.J. Stroud", "Harrison Mevis", "Lions", "David Montgomery", "Michael Pittman Jr.", "Courtland Sutton", "Jakobi Meyers", "Khalil Shakir"];
const slots = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "Q/W/R/T", "K", "DEF", "BN", "BN", "BN", "BN", "BN"];
const central = [15.1, 21.5, 15.8, 22.7, 18, 23.1, 16.4, 13.1, 9.9, 4.5, 9.1, 11.3, 14.3, 13.3, 12.8];
export const rosterFixture = names.map((name, index) => ({ id: index + 1, league_id: 2, name, position: index === 10 ? "RB" : index === 12 ? "WR" : index === 7 ? "QB" : slots[index] === "BN" ? "WR" : slots[index] === "FLEX" ? "TE" : slots[index], pro_team: ["DAL", "MIA", "DEN", "DET", "BAL", "ARI", "ATL", "HOU", "LAR", "DET", "HOU", "PIT", "DEN", "JAX", "BUF"][index], rostered_by: teamName, current_slot: slots[index], ownership: teamName, status: [4, 11, 13, 14].includes(index) ? "Questionable" : "Active", projected_points: index === 0 ? 535.8 : 200 + index, floor: 100 + index, ceiling: 300 + index, risk: 0.3, ros_value: null, projection: { source: "Yahoo Fantasy", period: "season", season: 2026, week: null, source_updated_at: null, received_at: "2026-09-09T10:00:00Z", scoring_basis: "source_points", ros_value_state: "missing" } }));
export const leagueFixture = { id: 2, name: "My Pals", season: 2026, source: "yahoo_scrape", yahoo_key: "fixture", my_team_name: teamName, team_names: [teamName], scoring: { passing_yards_yahoo_default: 0.02, receptions: 1.5 }, roster_slots: slots, player_count: 15 };
export function leagueResponse(path: string, params: URLSearchParams) {
  if (path.endsWith("/onboarding/status")) return { configured: true, auth_required: false, capabilities: { draft_suite: true } };
  if (path.endsWith("/system/health")) return { status: "ok" };
  if (path.endsWith("/leagues/2")) return leagueFixture;
  if (path.endsWith("/leagues")) return [leagueFixture];
  if (path.endsWith("/roster") || path.endsWith("/players/directory") || path.endsWith("/projection-leaders")) return rosterFixture;
  if (path.endsWith("/analysis-context")) return { teams: [teamName], suggested_week: 1, schedule_available: true };
  if (path.endsWith("/weekly-lineup")) {
    const mode = params.get("mode") || "balanced";
    const forecasts = rosterFixture.map((player, index) => ({ player_id: player.id, points: central[index], floor: index === 0 ? 7.2 : central[index] - 5, ceiling: central[index] + 5, locked: false, conditional: player.status !== "Active", reason: null, warnings: [] }));
    return { mode, season: 2026, week: Number(params.get("week") || 1), source: "Open Gridiron weekly model", partial_total: false, error: null, forecasts, assignments: rosterFixture.filter((_, index) => index < 10 && index !== 7 || index === 12).map((player) => ({ player_id: player.id, slot: player.id === 13 ? "Q/W/R/T" : player.current_slot, score: forecasts[player.id - 1][mode === "balanced" ? "points" : mode as "floor" | "ceiling"], action: player.id === 13 ? "Start" : "Keep", reason: null })), current_total: mode === "floor" ? 102.2 : 160.1, projected_total: mode === "floor" ? 103.4 : 161.3, projected_gain: 1.2, unfilled_slots: [], data_as_of: "2026-09-09T10:00:00Z" };
  }
  if (path.endsWith("/waivers/page")) return { items: [{ player_id: 50, rank: 1, ranking_basis: "source_points", expected_value: 394.1, confidence: 0, rationale: ["Roster gain unavailable: matching weekly roster inputs required."], player: { ...rosterFixture[0], id: 50, name: "Geno Smith", rostered_by: null, current_slot: null, ownership: "FA", projected_points: 394.1 } }], total: 1, available: 1, next_offset: null, facets: { teams: ["DAL"], statuses: ["Active"], positions: ["QB"] } };
  return [];
}
