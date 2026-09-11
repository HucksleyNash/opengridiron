import type { Player, ProjectionContext } from "../../types";
import type { Forecast } from "../league-analysis/types";

export function projectionPeriod(context?: ProjectionContext) {
  if (!context?.season || context.period === "unknown") return "Period unknown";
  if (context.period === "week") return `${context.season} · Week ${context.week}`;
  if (context.period === "rest_of_season") return `${context.season} · Rest of season${context.week ? ` from Week ${context.week}` : ""}`;
  return `${context.season} · Full season`;
}

export function projectionSummary(players: Player[]) {
  const periods = [...new Set(players.map((player) => projectionPeriod(player.projection)))];
  return periods.length > 1 ? "Mixed projection periods — review sources before comparing totals." : periods[0] === "Period unknown" || !periods.length
    ? "Stored points · projection period unknown. Not verified for weekly decisions." : `${periods[0]} projections`;
}

export function sourceTime(value?: string | null) {
  if (!value || !Number.isFinite(Date.parse(value))) return "Not supplied";
  return new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export function rosLabel(player: Player) {
  if (player.ros_value === null || player.projection?.ros_value_state === "missing") return "Not supplied";
  return `${points(player.ros_value)}${(!player.projection || player.projection.ros_value_state === "legacy_unknown") ? " · legacy input, unverified" : ""}`;
}

export type LineupMode = "floor" | "balanced" | "ceiling";
export type LineupRecommendation = {
  mode: LineupMode;
  assignments: { slot: string; player: Player; score: number | null }[];
  current_total: number | null;
  projected_total: number | null;
  projected_gain: number | null;
  unfilled_slots: string[];
  data_as_of: string;
};

export type WeeklyLineup = Omit<LineupRecommendation, "assignments"> & {
  season: number; week: number; source: string; partial_total: boolean; error: string | null;
  forecasts: Forecast[];
  assignments: { slot: string; player_id: number; score: number | null; action: string; reason: string | null }[];
};

export const LINEUP_MODES: Record<LineupMode, { label: string; description: string }> = {
  floor: { label: "Floor", description: "Maximize weekly lower estimates. Not a guaranteed minimum." },
  balanced: { label: "Balanced", description: "Maximize weekly projected points." },
  ceiling: { label: "Ceiling", description: "Maximize weekly upper estimates. Not a guaranteed maximum." },
};

export function weeklyPoints(forecast: Forecast | undefined, mode: LineupMode) {
  return forecast?.[mode === "balanced" ? "points" : mode] ?? null;
}

// Shared provenance is checked independently; matching periods do not imply matching sources.
export function sharedProjectionContext(players: Player[]) {
  const same = (read: (player: Player) => string) => {
    const values = [...new Set(players.map(read))];
    return values.length === 1 ? values[0] : null;
  };
  return {
    period: same((player) => projectionPeriod(player.projection)),
    source: same((player) => player.projection?.source || "Source not recorded"),
    updated: same((player) => player.projection?.source_updated_at || ""),
  };
}

export const WAIVER_ROLE_GROUPS: Record<string, string[]> = {
  FLEX: ["RB", "WR", "TE"], "W/R/T": ["RB", "WR", "TE"], "W/R": ["RB", "WR"], "W/T": ["WR", "TE"],
  SUPERFLEX: ["QB", "RB", "WR", "TE"], "Q/W/R/T": ["QB", "RB", "WR", "TE"], OP: ["QB", "RB", "WR", "TE"],
  DL: ["DL", "DE", "DT"], DB: ["DB", "CB", "S", "SS", "FS"], IDP: ["DL", "DE", "DT", "LB", "DB", "CB", "S", "SS", "FS"],
};

const SLOT_ORDER = ["QB", "RB", "WR", "TE", "FLEX", "W/R/T", "W/R", "W/T", "SUPERFLEX", "Q/W/R/T", "OP", "K", "DEF", "DL", "DE", "DT", "LB", "DB", "CB", "S", "SS", "FS"];
export const normalizePosition = (value: string) => ["DST", "D/ST"].includes(value.trim().toUpperCase()) ? "DEF" : value.trim().toUpperCase();
export const slotOrder = (slot: string) => {
  const index = SLOT_ORDER.indexOf(normalizePosition(slot));
  return index < 0 ? SLOT_ORDER.length : index;
};
export const points = (value: number | null | undefined) => typeof value === "number" && Number.isFinite(value)
  ? new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(value) : "Not available";
export const signedPoints = (value: number | null) => value === null ? "Not available" : `${value > 0 ? "+" : ""}${points(value)}`;
export const slotLabel = (slot?: string | null) => {
  if (!slot?.trim()) return "Unassigned";
  const normalized = slot.trim().toUpperCase();
  if (["BN", "BENCH"].includes(normalized)) return "Bench";
  if (["FLEX", "W/R/T"].includes(normalized)) return "FLEX";
  return normalizePosition(normalized);
};

export function rosterGroup(player: Player, slots: string[]): "Starters" | "Bench" | "Reserve" | "Unassigned" {
  const slot = normalizePosition(player.current_slot || "");
  if (["BN", "BENCH"].includes(slot)) return "Bench";
  if (["IR", "IR+", "NA"].includes(slot)) return "Reserve";
  return slot && slots.some((entry) => normalizePosition(entry) === slot) ? "Starters" : "Unassigned";
}

export function orderedRoster(players: Player[], slots: string[]) {
  const groups = ["Starters", "Bench", "Reserve", "Unassigned"];
  return [...players].sort((a, b) => groups.indexOf(rosterGroup(a, slots)) - groups.indexOf(rosterGroup(b, slots))
    || slotOrder(rosterGroup(a, slots) === "Starters" ? a.current_slot! : a.position) - slotOrder(rosterGroup(b, slots) === "Starters" ? b.current_slot! : b.position)
    || b.projected_points - a.projected_points || a.name.localeCompare(b.name));
}

// Compare membership, not repeated WR/RB slot indices. Reordering starters is not a start/bench change.
export function lineupChanges(roster: Player[], lineup: LineupRecommendation, slots: string[]) {
  const current = roster.filter((player) => rosterGroup(player, slots) === "Starters");
  const currentIds = new Set(current.map((player) => player.id));
  const recommendedIds = new Set(lineup.assignments.map(({ player }) => player.id));
  return {
    start: lineup.assignments.filter(({ player }) => !currentIds.has(player.id)),
    bench: current.filter((player) => !recommendedIds.has(player.id)),
  };
}

export function scoringLabel(key: string) {
  const defaulted = key.includes("yahoo_default");
  const label = key.replaceAll("_yahoo_default", "").replaceAll("_", " ").replace(/\btds\b/g, "touchdowns").replace(/\btd\b/g, "touchdown");
  return `${label.charAt(0).toUpperCase()}${label.slice(1)}${defaulted ? " (Yahoo default)" : ""}`;
}
