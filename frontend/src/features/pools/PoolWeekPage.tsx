import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowDown, ArrowLeft, ArrowUp, Check, ChevronLeft, ChevronRight, Sparkles } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import type { PoolWeek, PoolWeekGame, WeeklyCard, WeeklyPickDraft } from "../../types";
import {
  getPoolEntries,
  getPoolOverview,
  getPoolWeek,
  poolKeys,
  saveWeeklyCard,
} from "./api";
import { assignConfidenceWeight, reapplyUnlockedAttempts } from "./pool-card-state";
import { useLatestCardAutosave } from "./useLatestCardAutosave";
import { weeklyCardProgress } from "../../ui-display-state";
import { PoolAssistant } from "./PoolAssistant";
import { PoolIntelligence } from "./PoolIntelligence";

const toDraft = (card: WeeklyCard): WeeklyPickDraft[] => card.picks
  .filter((pick) => Boolean(pick.game_id))
  .map((pick) => ({
    ...(pick.slot ? { slot: pick.slot } : {}),
    game_id: pick.game_id || 0,
    team: pick.team,
    confidence: pick.confidence ?? null,
  }));

const kickoff = (value: string) => new Intl.DateTimeFormat(undefined, {
  weekday: "short",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
}).format(new Date(value));

const reasonLabel: Record<string, string> = {
  team_used: "Already used",
  selected_other_slot: "Selected in another slot",
  game_locked: "Game started",
  week_locked: "Week locked",
  blocked_team: "Blocked by pool rules",
  not_allowed: "Not allowed",
};

function findPick(picks: WeeklyPickDraft[], gameId: number) {
  return picks.find((pick) => pick.game_id === gameId);
}

function ConfidenceWorkspace({
  data,
  draft,
  edit,
  suggestionsEnabled,
}: {
  data: PoolWeek;
  draft: WeeklyPickDraft[];
  edit: (picks: WeeklyPickDraft[]) => void;
  suggestionsEnabled: boolean;
}) {
  const weights = data.pool.rules.confidence_weights.length
    ? [...data.pool.rules.confidence_weights].sort((a, b) => a - b)
    : Array.from({ length: data.games.length }, (_, index) => index + 1);

  const chooseTeam = (game: PoolWeekGame, team: string) => {
    const current = findPick(draft, game.id);
    const next = draft.filter((pick) => pick.game_id !== game.id);
    next.push({ game_id: game.id, team, confidence: current?.confidence ?? null });
    edit(next);
  };

  const chooseWeight = (gameId: number, weight: number) => {
    edit(assignConfidenceWeight(draft, gameId, weight));
  };

  const moveWeight = (gameId: number, direction: -1 | 1) => {
    const current = findPick(draft, gameId);
    if (!current?.confidence) return;
    const index = weights.indexOf(current.confidence);
    const target = weights[index + direction];
    if (target) chooseWeight(gameId, target);
  };

  const suggested = () => edit(data.games
    .filter((game) => game.suggested_team && game.suggested_confidence)
    .map((game) => ({
      game_id: game.id,
      team: game.suggested_team || "",
      confidence: game.suggested_confidence || null,
    })));

  const reviewMissing = () => document.querySelector<HTMLElement>(".confidence-game.incomplete")?.focus();

  return <section className="pool-workspace">
    <div className="workspace-actions">
      <div><span className="eyebrow">Full weekly card</span><h2>{data.card.selection_count} of {data.card.required_count} games picked</h2></div>
      <div><button className="ghost" onClick={reviewMissing}>Review missing</button><button className="primary" onClick={suggested} disabled={!suggestionsEnabled || data.games.some((game) => game.locked) || !data.games.every((game) => game.suggested_team && game.suggested_confidence)}><Sparkles size={15} />Use suggested card</button></div>
    </div>
    <div className="confidence-list">
      {data.games.map((game) => {
        const pick = findPick(draft, game.id);
        const incomplete = !pick?.team || !pick.confidence;
        return <article className={`confidence-game ${incomplete ? "incomplete" : "complete"}`} key={game.id} tabIndex={incomplete ? -1 : undefined}>
          <div className="matchup-meta"><span>{kickoff(game.kickoff)}</span>{game.locked && <strong>Locked</strong>}</div>
          <div className="confidence-controls">
            <div className="team-choice-pair" role="group" aria-label={`${game.away_team} at ${game.home_team}`}>
              {[game.away_team, game.home_team].map((team) => {
                const recommendation = game.recommendations.find((item) => item.team === team);
                return <button key={team} className={pick?.team === team ? "selected" : ""} aria-pressed={pick?.team === team} disabled={game.locked} onClick={() => chooseTeam(game, team)}>
                  <span>{team}</span>{recommendation && <small>{Math.round(recommendation.probability * 100)}%</small>}
                </button>;
              })}
            </div>
            <label className="confidence-weight"><span>Confidence</span><select aria-label={`Confidence for ${game.away_team} at ${game.home_team}`} value={pick?.confidence || ""} disabled={!pick || game.locked} onChange={(event) => chooseWeight(game.id, Number(event.target.value))}><option value="">—</option>{weights.map((weight) => <option value={weight} key={weight}>{weight}</option>)}</select></label>
            <div className="weight-movers"><button aria-label={`Decrease confidence for ${pick?.team || game.away_team}`} disabled={!pick?.confidence || game.locked || pick.confidence === weights[0]} onClick={() => moveWeight(game.id, -1)}><ArrowDown size={14} /></button><button aria-label={`Increase confidence for ${pick?.team || game.away_team}`} disabled={!pick?.confidence || game.locked || pick.confidence === weights.at(-1)} onClick={() => moveWeight(game.id, 1)}><ArrowUp size={14} /></button></div>
          </div>
        </article>;
      })}
    </div>
  </section>;
}

function SurvivorWorkspace({
  data,
  draft,
  edit,
}: {
  data: PoolWeek;
  draft: WeeklyPickDraft[];
  edit: (picks: WeeklyPickDraft[]) => void;
}) {
  const [activeSlot, setActiveSlot] = useState(1);
  const slots = data.survivor_slots || [];
  const slot = slots.find((item) => item.slot === activeSlot) || slots[0];
  const current = draft.find((pick) => pick.slot === slot?.slot);
  const top = useMemo(() => data.games
    .filter((game) => !game.locked)
    .flatMap((game) => game.recommendations.map((recommendation) => ({ ...recommendation, gameId: game.id })))
    .filter((recommendation) => slot?.choices.some((choice) => choice.game_id === recommendation.gameId && choice.team === recommendation.team && choice.eligible))
    .sort((a, b) => b.score - a.score)[0], [data.games, slot]);

  const choose = (gameId: number, team: string) => {
    if (!slot) return;
    edit([
      ...draft.filter((pick) => pick.slot !== slot.slot),
      { slot: slot.slot, game_id: gameId, team, confidence: null },
    ]);
  };

  return <section className="pool-workspace">
    <div className="workspace-actions">
      <div><span className="eyebrow">{data.pool.rules.direction === "loser" ? "Eliminator" : "Survivor"}</span><h2>Choose {data.card.required_count === 1 ? "one" : data.card.required_count} {data.pool.rules.direction === "loser" ? "loser" : "winner"}{data.card.required_count > 1 ? "s" : ""}</h2></div>
      {slots.length > 1 && <div className="slot-tabs" role="tablist">{slots.map((item) => <button role="tab" aria-selected={item.slot === slot?.slot} className={item.slot === slot?.slot ? "active" : ""} onClick={() => setActiveSlot(item.slot)} key={item.slot}>Pick {item.slot}{draft.some((pick) => pick.slot === item.slot) && <Check size={13} />}</button>)}</div>}
    </div>
    <div className="matchup-grid">
      {data.games.map((game) => <article className="matchup-card" key={game.id}>
        <div className="matchup-meta"><span>{kickoff(game.kickoff)}</span>{game.locked && <strong>Locked</strong>}</div>
        {[game.away_team, game.home_team].map((team) => {
          const choice = slot?.choices.find((item) => item.game_id === game.id && item.team === team);
          const recommendation = game.recommendations.find((item) => item.team === team);
          const selected = current?.game_id === game.id && current.team === team;
          const best = top?.gameId === game.id && top.team === team;
          return <button className={`matchup-team ${selected ? "selected" : ""}`} aria-pressed={selected} disabled={!choice?.eligible} onClick={() => choose(game.id, team)} key={team}>
            <span><strong>{team}</strong>{best && <small className="best-pick"><Sparkles size={12} />Best value</small>}</span>
            <span>{recommendation ? <><b>{Math.round(recommendation.probability * 100)}%</b><small>{recommendation.rationale[1]}</small></> : <small>Probability unavailable</small>}</span>
            {!choice?.eligible && choice?.reason && <em>{reasonLabel[choice.reason] || choice.reason.replaceAll("_", " ")}</em>}
          </button>;
        })}
      </article>)}
    </div>
  </section>;
}

export function PoolWeekPage() {
  const { poolId, week } = useParams();
  const [search] = useSearchParams();
  return <PoolWeekWorkspace key={`${poolId}:${week}:${search.get("entry_id")}`} />;
}

function PoolWeekWorkspace() {
  const { poolId: poolIdRaw, week: weekRaw } = useParams();
  const poolId = Number(poolIdRaw);
  const week = Number(weekRaw);
  const [search, setSearch] = useSearchParams();
  const queryClient = useQueryClient();
  const overview = useQuery({ queryKey: poolKeys.overview, queryFn: getPoolOverview });
  const poolSummary = overview.data?.pools.find((pool) => pool.id === poolId);
  const season = poolSummary?.season || 0;
  const entries = useQuery({ queryKey: poolKeys.entries(poolId), queryFn: () => getPoolEntries(poolId), enabled: Number.isFinite(poolId) && poolId > 0 });
  const requestedEntryId = Number(search.get("entry_id"));
  const entryId = entries.data?.some((entry) => entry.id === requestedEntryId) ? requestedEntryId : entries.data?.[0]?.id || 0;
  const queryKey = poolKeys.week(poolId, entryId, season, week);
  const workspace = useQuery({
    queryKey,
    queryFn: () => getPoolWeek(poolId, entryId, week),
    enabled: Boolean(poolId && week && entryId),
    refetchOnWindowFocus: false, // The source check refreshes the card after pending saves finish.
  });
  const [draft, setDraft] = useState<WeeklyPickDraft[]>([]);
  const [conflict, setConflict] = useState<{ attempted: WeeklyPickDraft[]; canonical: WeeklyCard } | null>(null);
  const [applyingAI, setApplyingAI] = useState(false);
  const [sourcesReady, setSourcesReady] = useState(false);

  useEffect(() => {
    if (entryId && entryId !== requestedEntryId) setSearch({ entry_id: String(entryId) }, { replace: true });
  }, [entryId, requestedEntryId, setSearch]);

  useEffect(() => {
    if (workspace.data && !autosave.hasPendingChanges()) setDraft(toDraft(workspace.data.card));
  }, [poolId, entryId, week, workspace.data?.card.version]);

  const autosave = useLatestCardAutosave({
    cardKey: `${poolId}:${entryId}:${week}`,
    version: workspace.data?.card.version || 0,
    initialPicks: workspace.data ? toDraft(workspace.data.card) : [],
    enabled: Boolean(workspace.data && !workspace.data.entry.read_only && !workspace.data.configuration_errors.length),
    save: (version, picks) => saveWeeklyCard(entryId, week, version, picks),
    onSaved: (card) => {
      queryClient.setQueryData<PoolWeek>(queryKey, (current) => current ? { ...current, card } : current);
      void queryClient.invalidateQueries({ queryKey: poolKeys.overview });
      setConflict(null);
    },
    onConflict: (value) => {
      setConflict(value);
      setDraft(toDraft(value.canonical));
      queryClient.setQueryData<PoolWeek>(queryKey, (current) => current ? { ...current, card: value.canonical } : current);
    },
  });

  const edit = (next: WeeklyPickDraft[]) => {
    const ordered = [...next].sort((left, right) => (left.slot || left.game_id) - (right.slot || right.game_id));
    setDraft(ordered);
    setConflict(null);
    autosave.queue(ordered);
  };

  const reapply = () => {
    if (!conflict || !workspace.data) return;
    const merged = reapplyUnlockedAttempts(
      workspace.data.pool.pool_type,
      conflict.attempted,
      conflict.canonical.picks,
      new Set(workspace.data.games.filter((game) => game.locked).map((game) => game.id)),
    );
    setConflict(null);
    edit(merged);
  };

  if (overview.isLoading || entries.isLoading || (entryId && workspace.isLoading)) return <div className="pool-loading"><span /><p>Loading the pool week…</p></div>;
  if ((overview.error && !overview.data) || (entries.error && !entries.data) || (workspace.error && !workspace.data)) return <><Link className="pool-back" to="/pools"><ArrowLeft size={15} />All pools</Link><div className="error-panel">{(overview.error || entries.error || workspace.error)?.message}</div></>;
  if (!poolSummary) return <><Link className="pool-back" to="/pools"><ArrowLeft size={15} />All pools</Link><div className="error-panel">That pool could not be found.</div></>;
  if (!entries.data?.length) return <><Link className="pool-back" to="/pools"><ArrowLeft size={15} />All pools</Link><section className="panel pool-empty"><h2>{poolSummary.name} has no entries</h2><p>Add an entry from the pool overview before making picks.</p><Link className="button primary" to="/pools">Add an entry</Link></section></>;
  if (!workspace.data) return null;
  const data = workspace.data;
  const status = autosave.state === "saving" ? "Saving…" : autosave.state === "waiting" ? "Changes queued" : autosave.state === "error" ? "Not saved" : autosave.state === "conflict" ? "Card changed" : data.card.state === "locked_complete" || data.card.state === "locked_incomplete" ? "Locked" : data.card.missing_count ? `Missing ${data.card.missing_count}` : "Saved automatically · Week complete";

  return <>
    <Link className="pool-back" to="/pools"><ArrowLeft size={15} />All pools</Link>
    <header className="page-header pool-week-header">
      <div><span className="eyebrow">{data.pool.pool_type === "confidence" ? "Confidence pool" : data.pool.rules.direction === "loser" ? "Loser pool" : "Winner pool"}</span><h1>{data.pool.name}</h1><p>{data.pool.rules.basis.replaceAll("_", " ")} · {data.pool.season} season</p></div>
      <div className="week-nav"><Link aria-label="Previous week" to={`/pools/${poolId}/weeks/${Math.max(1, week - 1)}?entry_id=${entryId}`}><ChevronLeft size={18} /></Link><span>Week <strong>{week}</strong></span><Link aria-label="Next week" to={`/pools/${poolId}/weeks/${Math.min(30, week + 1)}?entry_id=${entryId}`}><ChevronRight size={18} /></Link></div>
    </header>
    <div className="entry-tabs" role="tablist" aria-label="Pool entries">{entries.data.map((entry) => {
      const summary = poolSummary.entries.find((item) => item.id === entry.id);
      return <button role="tab" aria-selected={entry.id === entryId} className={entry.id === entryId ? "active" : ""} onClick={() => setSearch({ entry_id: String(entry.id) })} key={entry.id}><span>{entry.name}</span><small>{summary?.missing_count ? `${summary.missing_count} missing` : summary ? "Complete" : "Entry"}</small></button>;
    })}</div>

    <PoolAssistant key={`${poolId}:${entryId}:${week}`} data={data} busy={["waiting", "saving", "error", "conflict"].includes(autosave.state)} onApplying={setApplyingAI} onReadiness={setSourcesReady} />
    {workspace.error && <p role="alert">The updated card could not load. Cached games and picks are shown. {workspace.error.message}</p>}
    {data.configuration_errors.length > 0 && <div className="error-panel">Pool settings need attention: {data.configuration_errors.map((item) => item.code.replaceAll("_", " ")).join(", ")}.</div>}
    {data.card.findings.length > 0 && <div className="repair-panel"><AlertTriangle size={17} /><div><strong>This card needs repair</strong><p>{data.card.findings.map((item) => item.code.replaceAll("_", " ")).join(" · ")}</p></div></div>}
    {conflict && <div className="conflict-panel"><AlertTriangle size={17} /><div><strong>Another tab changed this card.</strong><p>The latest saved card is shown. You can reapply only changes that are still unlocked.</p></div><button onClick={reapply}>Reapply unlocked changes</button></div>}
    {autosave.error && <div className="schedule-error prominent"><AlertTriangle size={16} /><span>{autosave.error.message}</span><button onClick={autosave.retry}>Retry save</button></div>}

    <fieldset disabled={applyingAI || data.entry.read_only || Boolean(data.configuration_errors.length)} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
    {!data.games.length ? <section className="panel pool-empty"><h2>No games in Week {week}</h2><p>Check source coverage above or choose another week.</p></section> : data.pool.pool_type === "confidence" ? <ConfidenceWorkspace data={data} draft={draft} edit={edit} suggestionsEnabled={sourcesReady && !workspace.isFetching} /> : <SurvivorWorkspace data={data} draft={draft} edit={edit} />}
    </fieldset>
    <div className={`autosave-bar ${data.card.state}`}><span className="status-dot" /><strong>{status}</strong><span>{weeklyCardProgress(data.pool.pool_type, data.card.selection_count, data.card.required_count, data.card.weight_count)}</span></div>
    <PoolIntelligence data={data} />

  </>;
}
