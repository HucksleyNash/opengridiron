import type { Game, PoolEntrySummary } from "../../types";
import type { InjuryRow } from "../injuries/types";
import type { WeeklyLineup } from "../leagues/league-display";

export const timestamp = (value: string) => new Date(/(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`).getTime();
export const teamCode = (value: string) => ({ LA: "LAR", JAC: "JAX", WSH: "WAS", WFT: "WAS" }[value.toUpperCase()] || value.toUpperCase());
export const isStarter = (slot?: string | null) => Boolean(slot && !["BN", "BENCH", "IR", "IR+", "NA"].includes(slot.toUpperCase()));
export const needsInjuryReview = (row: InjuryRow) => /questionable|doubtful|\bout\b|inactive|\bir\b|pup|suspend|\bsus\b|dnr/i.test(row.game_status);
export const ownMemberships = (row: InjuryRow) => row.memberships.filter((item) => item.is_mine);
export const gameForTeam = (games: Game[], team: string) => games.find((game) => [game.away_team, game.home_team].some((side) => teamCode(side) === teamCode(team)));

export function injuryPriority(row: InjuryRow, games: Game[], now: number) {
  const game = gameForTeam(games, row.team);
  const upcoming = game && timestamp(game.kickoff) > now;
  const starter = ownMemberships(row).some((item) => isStarter(item.slot));
  return (upcoming ? 0 : game ? 4 : 6) + (starter ? 0 : 2) + (/questionable/i.test(row.game_status) ? 1 : 0);
}

export function lineupSummary(lineup: WeeklyLineup) {
  const currentIds = new Set(lineup.forecasts.filter((player) => isStarter(player.current_slot)).map((player) => player.player_id));
  const starters = lineup.assignments.filter((item) => !currentIds.has(item.player_id));
  if (lineup.error) return { text: "Review source coverage", tone: "warning", changes: 0 };
  if (lineup.unfilled_slots.length) return { text: `${lineup.unfilled_slots.length} unfilled starter ${lineup.unfilled_slots.length === 1 ? "slot" : "slots"}`, tone: "urgent", changes: 0 };
  if (lineup.partial_total || lineup.projected_total == null) return { text: "Incomplete forecast coverage", tone: "warning", changes: 0 };
  return { text: starters.length ? `${starters.length} lineup ${starters.length === 1 ? "option" : "options"} to review` : "No modeled lineup changes", tone: "neutral", changes: starters.length };
}

export function poolEntryState(entry: PoolEntrySummary) {
  if (entry.card_state === "locked_incomplete") return { text: "Locked · incomplete", tone: "urgent", needsReview: true };
  if (entry.card_state === "needs_repair") return { text: "Review saved picks", tone: "warning", needsReview: true };
  if (entry.card_state === "draft") return { text: entry.missing_count > 0 ? `${entry.missing_count} ${entry.missing_count === 1 ? "pick" : "picks"} missing` : "Finish saved card", tone: "warning", needsReview: true };
  return { text: entry.card_state === "locked_complete" ? "Locked · complete here" : "Complete here", tone: "neutral", needsReview: false };
}

export function gamePhase(game: Game, now: number) {
  if (game.completed) return "final";
  return timestamp(game.kickoff) > now ? "upcoming" : "started";
}

export function pregameEstimate(game: Game) {
  if (!Number.isFinite(game.home_win_probability) || game.home_win_probability < 0 || game.home_win_probability > 1) return "Estimate unavailable";
  if (!["market", "manual", "team_strength", "model", "historical_team_strength"].includes(game.win_probability_kind)) return "Estimate unverified";
  const home = game.home_win_probability >= 0.5;
  return `${home ? game.home_team : game.away_team} ${Math.round((home ? game.home_win_probability : 1 - game.home_win_probability) * 100)}%`;
}

export function sourceState(status: string, retrievedAt: string | undefined, now: number) {
  if (["failed", "error", "unavailable"].includes(status)) return { label: "Unavailable", tone: "urgent" };
  if (!retrievedAt || !Number.isFinite(timestamp(retrievedAt))) return { label: "Not checked", tone: "warning" };
  if (status === "stale" || now - timestamp(retrievedAt) > 24 * 3_600_000) return { label: "Older data", tone: "warning" };
  return { label: "Retrieved", tone: "neutral" };
}
