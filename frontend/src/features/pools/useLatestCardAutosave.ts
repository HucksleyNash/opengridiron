import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../api";
import type { WeeklyCard, WeeklyPickDraft } from "../../types";

export type AutosaveState = "idle" | "waiting" | "saving" | "saved" | "error" | "conflict";

type Conflict = {
  attempted: WeeklyPickDraft[];
  canonical: WeeklyCard;
};

type Options = {
  cardKey: string;
  version: number;
  initialPicks: WeeklyPickDraft[];
  enabled: boolean;
  save: (version: number, picks: WeeklyPickDraft[]) => Promise<{ card: WeeklyCard }>;
  onSaved: (card: WeeklyCard) => void;
  onConflict: (conflict: Conflict) => void;
};

const clone = (picks: WeeklyPickDraft[]) => picks.map((pick) => ({ ...pick }));

export function useLatestCardAutosave({
  cardKey,
  version,
  initialPicks,
  enabled,
  save,
  onSaved,
  onConflict,
}: Options) {
  const [state, setState] = useState<AutosaveState>("idle");
  const [error, setError] = useState<Error | null>(null);
  const versionRef = useRef(version);
  const latestRef = useRef(clone(initialPicks));
  const dirtyRef = useRef(false);
  const inFlightRef = useRef(false);
  const timerRef = useRef<number | undefined>(undefined);
  const generationRef = useRef(0);
  const saveRef = useRef(save);
  const onSavedRef = useRef(onSaved);
  const onConflictRef = useRef(onConflict);
  const pumpRef = useRef<() => void>(() => undefined);

  saveRef.current = save;
  onSavedRef.current = onSaved;
  onConflictRef.current = onConflict;

  const pump = useCallback(async () => {
    if (!enabled || inFlightRef.current || !dirtyRef.current) return;
    dirtyRef.current = false;
    inFlightRef.current = true;
    const generation = generationRef.current;
    const sending = clone(latestRef.current);
    let continueImmediately = true;
    setState("saving");
    setError(null);
    try {
      const result = await saveRef.current(versionRef.current, sending);
      if (generation !== generationRef.current) return;
      versionRef.current = result.card.version;
      onSavedRef.current(result.card);
      setState(dirtyRef.current ? "waiting" : "saved");
    } catch (caught) {
      if (generation !== generationRef.current) return;
      if (caught instanceof ApiError && caught.status === 409 && caught.payload.card) {
        const canonical = caught.payload.card as WeeklyCard;
        versionRef.current = canonical.version;
        latestRef.current = canonical.picks.map((pick) => ({
          slot: pick.slot,
          game_id: pick.game_id || 0,
          team: pick.team,
          confidence: pick.confidence ?? null,
        }));
        dirtyRef.current = false;
        setState("conflict");
        onConflictRef.current({ attempted: sending, canonical });
      } else {
        dirtyRef.current = true;
        continueImmediately = false;
        setState("error");
        setError(caught instanceof Error ? caught : new Error("Autosave failed."));
      }
    } finally {
      if (generation === generationRef.current) {
        inFlightRef.current = false;
        if (dirtyRef.current && continueImmediately) queueMicrotask(() => pumpRef.current());
      }
    }
  }, [enabled]);
  pumpRef.current = () => void pump();

  useEffect(() => {
    generationRef.current += 1;
    window.clearTimeout(timerRef.current);
    versionRef.current = version;
    latestRef.current = clone(initialPicks);
    dirtyRef.current = false;
    inFlightRef.current = false;
    setState("idle");
    setError(null);
  }, [cardKey]);

  useEffect(() => {
    if (dirtyRef.current || inFlightRef.current) return;
    versionRef.current = version;
    latestRef.current = clone(initialPicks);
  }, [version]);

  useEffect(() => () => {
    generationRef.current += 1;
    window.clearTimeout(timerRef.current);
  }, []);

  const queue = useCallback((picks: WeeklyPickDraft[]) => {
    latestRef.current = clone(picks);
    dirtyRef.current = true;
    setState("waiting");
    setError(null);
    window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => pumpRef.current(), 350);
  }, []);

  const retry = useCallback(() => {
    if (!dirtyRef.current) return;
    setState("waiting");
    void pumpRef.current();
  }, []);

  return { state, error, queue, retry, hasPendingChanges: () => dirtyRef.current || inFlightRef.current };
}
