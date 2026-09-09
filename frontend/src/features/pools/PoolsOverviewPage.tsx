import { FormEvent, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, ChevronRight, Clock3, Plus, RefreshCw, Trophy } from "lucide-react";
import { Link } from "react-router-dom";
import type { CardState, Pool } from "../../types";
import { SleeperPoolDetails, SleeperPoolImport } from "./SleeperPoolImport";
import { DeletePoolControl } from "./DeletePoolControl";
import {
  createPool,
  createPoolEntry,
  getPoolOverview,
  importSchedule,
  poolKeys,
} from "./api";

const statusLabel: Record<CardState, string> = {
  draft: "Picks needed",
  complete: "Week complete",
  locked_incomplete: "Locked · incomplete",
  locked_complete: "Locked · complete",
  needs_repair: "Needs repair",
};

function ScheduleBadge({ state }: { state: "ready" | "stale" | "missing" }) {
  return <span className={`schedule-badge ${state}`}>
    {state === "ready" ? <Check size={13} /> : state === "stale" ? <Clock3 size={13} /> : <RefreshCw size={13} />}
    {state === "ready" ? "Schedule ready" : state === "stale" ? "Refresh recommended" : "Importing schedule"}
  </span>;
}

export function PoolsOverviewPage() {
  const queryClient = useQueryClient();
  const overview = useQuery({ queryKey: poolKeys.overview, queryFn: getPoolOverview });
  const attemptedImports = useRef(new Set<number>());
  const [importing, setImporting] = useState<Set<number>>(new Set());
  const [importErrors, setImportErrors] = useState<Record<number, Error>>({});
  const [entryNames, setEntryNames] = useState<Record<number, string>>({});
  const [showSettings, setShowSettings] = useState(false);
  const settingsButton = useRef<HTMLButtonElement>(null);
  const [deletedPoolName, setDeletedPoolName] = useState("");
  const [poolDraft, setPoolDraft] = useState({
    name: "",
    pool_type: "survivor" as "survivor" | "confidence",
    direction: "winner" as "winner" | "loser",
    basis: "straight_up" as "straight_up" | "against_spread",
  });

  const runImport = async (season: number, trigger: "missing" | "retry") => {
    setImporting((current) => new Set(current).add(season));
    setImportErrors((current) => {
      const next = { ...current };
      delete next[season];
      return next;
    });
    try {
      await importSchedule(season, trigger);
      await queryClient.invalidateQueries({ queryKey: poolKeys.overview });
    } catch (caught) {
      setImportErrors((current) => ({
        ...current,
        [season]: caught instanceof Error ? caught : new Error("Schedule import failed."),
      }));
    } finally {
      setImporting((current) => {
        const next = new Set(current);
        next.delete(season);
        return next;
      });
    }
  };

  useEffect(() => {
    const missingSeasons = new Set(
      overview.data?.pools
        .filter((pool) => pool.schedule.state === "missing")
        .map((pool) => pool.season) || [],
    );
    for (const season of missingSeasons) {
      if (attemptedImports.current.has(season)) continue;
      attemptedImports.current.add(season);
      void runImport(season, "missing");
    }
  }, [overview.data]);

  const addEntry = useMutation({
    mutationFn: ({ poolId, name }: { poolId: number; name: string }) => createPoolEntry(poolId, name),
    onSuccess: async (_entry, variables) => {
      setEntryNames((current) => ({ ...current, [variables.poolId]: "" }));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: poolKeys.overview }),
        queryClient.invalidateQueries({ queryKey: poolKeys.entries(variables.poolId) }),
      ]);
    },
  });

  const addPool = useMutation({
    mutationFn: () => createPool({
      name: poolDraft.name,
      pool_type: poolDraft.pool_type,
      season: new Date().getFullYear(),
      rules: {
        direction: poolDraft.direction,
        basis: poolDraft.basis,
        picks_per_week: 1,
        max_team_uses: 1,
        allowed_teams: [],
        blocked_teams: [],
        tie_result: "push",
        lock_mode: "game_start",
        confidence_weights: [],
        future_value_weight: 0.1,
      },
    } as Omit<Pool, "id" | "entry_count">),
    onSuccess: async () => {
      setPoolDraft((current) => ({ ...current, name: "" }));
      await queryClient.invalidateQueries({ queryKey: poolKeys.overview });
    },
  });

  const submitEntry = (event: FormEvent, poolId: number) => {
    event.preventDefault();
    const name = (entryNames[poolId] || "Main entry").trim();
    if (name) addEntry.mutate({ poolId, name });
  };

  return <>
    <header className="page-header pool-page-header">
      <div><span className="eyebrow">Winner · loser · confidence</span><h1>Pool week</h1><p>Open this week, finish every entry, and let the schedule stay current automatically.</p></div>
      <button ref={settingsButton} className="ghost" aria-expanded={showSettings} onClick={() => setShowSettings((value) => !value)}><Plus size={16} />Pool settings</button>
    </header>

    {deletedPoolName && <p role="status">“{deletedPoolName}” deleted from Open Gridiron.</p>}

    {showSettings && <section className="panel pool-settings-panel">
      <SleeperPoolImport />
      <div className="panel-title"><div><span className="eyebrow">Setup</span><h2>Add a pool</h2></div></div>
      <form className="pool-settings-form" onSubmit={(event) => { event.preventDefault(); addPool.mutate(); }}>
        <label className="field"><span>Name</span><input value={poolDraft.name} onChange={(event) => setPoolDraft({ ...poolDraft, name: event.target.value })} required /></label>
        <label className="field"><span>Format</span><select value={poolDraft.pool_type} onChange={(event) => setPoolDraft({ ...poolDraft, pool_type: event.target.value as "survivor" | "confidence" })}><option value="survivor">Winner / loser</option><option value="confidence">Confidence</option></select></label>
        <label className="field"><span>Action</span><select value={poolDraft.direction} onChange={(event) => setPoolDraft({ ...poolDraft, direction: event.target.value as "winner" | "loser" })}><option value="winner">Pick winners</option><option value="loser">Pick losers</option></select></label>
        <label className="field"><span>Scoring</span><select value={poolDraft.basis} onChange={(event) => setPoolDraft({ ...poolDraft, basis: event.target.value as "straight_up" | "against_spread" })}><option value="straight_up">Straight up</option><option value="against_spread">Against the spread</option></select></label>
        <button className="primary" disabled={addPool.isPending}>{addPool.isPending ? "Adding…" : "Add pool"}</button>
      </form>
      {addPool.error && <div className="error-panel">{addPool.error.message}</div>}
    </section>}

    {overview.isLoading && <div className="pool-loading"><span /><p>Loading your pools and this week’s schedule…</p></div>}
    {overview.error && <div className="error-panel">{overview.error.message}</div>}
    {overview.data && !overview.data.pools.length && <section className="panel pool-empty"><Trophy size={30} /><h2>No pools yet</h2><p>Add your winner, loser, or confidence pool rules to start the weekly flow.</p><button className="primary" onClick={() => setShowSettings(true)}>Add your first pool</button></section>}

    <div className="pool-overview-grid">
      {overview.data?.pools.map((pool) => {
        const importError = importErrors[pool.season];
        const isImporting = importing.has(pool.season);
        return <article className="pool-overview-card" key={pool.id}>
          <div className="pool-card-heading">
            <div><span className="pool-format">{pool.pool_type === "confidence" ? "Confidence" : pool.rules.direction === "loser" ? "Loser pool" : "Winner pool"}</span><h2>{pool.name}</h2><p>{pool.rules.basis.replaceAll("_", " ")} · {pool.season}</p></div>
            <div className="pool-week-number"><span>Week</span><strong>{pool.suggested_week}</strong></div>
          </div>
          <ScheduleBadge state={pool.schedule.state} />
          {pool.sleeper && <SleeperPoolDetails poolId={pool.id} info={pool.sleeper} />}
          {isImporting && <p className="schedule-message"><RefreshCw className="spin" size={14} /> Loading the {pool.season} NFL schedule…</p>}
          {importError && <div className="schedule-error"><AlertTriangle size={15} /><span>{importError.message}</span><button onClick={() => void runImport(pool.season, "retry")}>Retry</button></div>}
          <div className="pool-entry-list">
            {pool.entries.map((entry) => <Link className="pool-entry-row" key={entry.id} to={`/pools/${pool.id}/weeks/${pool.suggested_week}?entry_id=${entry.id}`}>
              <span className={`entry-state-dot ${entry.card_state}`} />
              <span><strong>{entry.name}</strong><small>{statusLabel[entry.card_state]}{entry.missing_count ? ` · ${entry.missing_count} missing` : ""}</small></span>
              <ChevronRight size={18} />
            </Link>)}
            {!pool.entries.length && <div className="pool-no-entry"><p>Add an entry to start making picks.</p></div>}
          </div>
          <form className="pool-add-entry" onSubmit={(event) => submitEntry(event, pool.id)}>
            <input aria-label={`New entry for ${pool.name}`} value={entryNames[pool.id] ?? ""} onChange={(event) => setEntryNames((current) => ({ ...current, [pool.id]: event.target.value }))} placeholder="Main entry" />
            <button disabled={addEntry.isPending}><Plus size={14} />Add entry</button>
          </form>
          {showSettings && <DeletePoolControl pool={pool} onDeleted={(name) => {
            setDeletedPoolName(name);
            settingsButton.current?.focus();
          }} />}
        </article>;
      })}
    </div>
  </>;
}
