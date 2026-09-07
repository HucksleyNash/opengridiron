import type { WeeklyPick, WeeklyPickDraft } from "../../types";

export function assignConfidenceWeight(
  picks: WeeklyPickDraft[],
  gameId: number,
  weight: number,
): WeeklyPickDraft[] {
  const current = picks.find((pick) => pick.game_id === gameId);
  if (!current) return picks;
  const other = picks.find((pick) => pick.game_id !== gameId && pick.confidence === weight);
  const oldWeight = current.confidence;
  return picks.map((pick) => {
    if (pick.game_id === gameId) return { ...pick, confidence: weight };
    if (other && pick.game_id === other.game_id) return { ...pick, confidence: oldWeight };
    return pick;
  });
}

export function reapplyUnlockedAttempts(
  poolType: "survivor" | "confidence",
  attempted: WeeklyPickDraft[],
  canonical: WeeklyPick[],
  lockedGameIds: Set<number>,
): WeeklyPickDraft[] {
  const locked = canonical.filter((pick) => pick.locked);
  const lockedIdentities = new Set(
    locked.map((pick) => poolType === "survivor" ? pick.slot : pick.game_id),
  );
  const eligibleAttempts = attempted.filter((pick) => {
    const identity = poolType === "survivor" ? pick.slot : pick.game_id;
    return !lockedIdentities.has(identity) && !lockedGameIds.has(pick.game_id);
  });
  return [
    ...locked.map((pick) => ({
      slot: pick.slot,
      game_id: pick.game_id || 0,
      team: pick.team,
      confidence: pick.confidence ?? null,
    })),
    ...eligibleAttempts,
  ];
}
