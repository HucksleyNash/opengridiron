import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import { api, post } from "../../api";
import type { AnalysisResult, PoolWeek, Provider, WeeklyCard, WeeklyPickDraft } from "../../types";
import { poolKeys } from "./api";
import { AnalysisFollowUp } from "../analysis/AnalysisFollowUp";

type Freshness = {
  status: "ready" | "partial" | "running";
  checked_at: string | null;
  sources: { name: string; status: "refreshed" | "unavailable" | "manual"; checked_at: string | null; detail?: string }[];
};
type Proposal = {
  run_id: number;
  output: NonNullable<AnalysisResult["output"]> & { picks: WeeklyPickDraft[] } | null;
  can_apply: boolean;
  reason: string | null;
  freshness: Freshness;
  version: number;
};

export function PoolAssistant({ data, busy, onApplying, onReadiness }: {
  data: PoolWeek;
  busy: boolean;
  onApplying: (value: boolean) => void;
  onReadiness: (value: boolean) => void;
}) {
  const client = useQueryClient();
  const [providerId, setProviderId] = useState<number>();
  const [saved, setSaved] = useState(false);
  const [refreshError, setRefreshError] = useState<Error | null>(null);
  const [forcing, setForcing] = useState(false);
  const context = `/pools/${data.pool.id}/weeks/${data.week.number}`;
  const entry = `entry_id=${data.entry.id}`;
  const refreshKey = ["pool-check-in", data.pool.id, data.entry.id, data.week.number];
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api<Provider[]>("/providers") });
  const enabled = (providers.data || []).filter((provider) => provider.enabled);
  const checkIn = useQuery({
    queryKey: refreshKey,
    queryFn: () => post<Freshness>(`${context}/check-in?${entry}`),
    staleTime: 0,
    refetchOnMount: "always",
    refetchOnWindowFocus: "always",
    refetchInterval: (query) => query.state.data?.status === "running" ? 2000 : 300_000,
    retry: false,
    enabled: !forcing,
  });
  const lastRefreshed = useRef(0);
  const refreshWorkspace = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["pool-week", data.pool.id] }),
      client.invalidateQueries({ queryKey: poolKeys.overview }),
      client.invalidateQueries({ queryKey: ["pool-standings", data.pool.id] }),
      client.invalidateQueries({ queryKey: ["pool-strategy", data.pool.id] }),
    ]);
  };
  const analyze = useMutation({
    mutationFn: () => post<Proposal>(`${context}/analysis?${entry}`, { provider_id: providerId }),
    onMutate: () => setSaved(false),
    onSuccess: (result) => client.setQueryData(refreshKey, result.freshness),
  });
  const apply = useMutation({
    mutationFn: () => post<{ card: WeeklyCard }>(`${context}/analysis/apply?${entry}`, { run_id: analyze.data!.run_id }),
    onMutate: () => onApplying(true),
    onSuccess: async ({ card }) => {
      client.setQueryData<PoolWeek>(poolKeys.week(data.pool.id, data.entry.id, data.pool.season, data.week.number), (current) => current ? { ...current, card } : current);
      setSaved(true);
      await refreshWorkspace();
    },
    onSettled: () => onApplying(false),
  });
  useEffect(() => {
    setRefreshError(null);
  }, [checkIn.dataUpdatedAt]);
  useEffect(() => {
    // A source update must not replace a local card while its save is pending.
    if (!busy && !apply.isPending && checkIn.data?.status !== "running" && checkIn.dataUpdatedAt > lastRefreshed.current) {
      lastRefreshed.current = checkIn.dataUpdatedAt;
      void refreshWorkspace();
    }
  }, [checkIn.dataUpdatedAt, busy, apply.isPending]);

  const refresh = async () => {
    setForcing(true);
    setRefreshError(null);
    try {
      await client.cancelQueries({ queryKey: refreshKey });
      const result = await post<Freshness>(`${context}/check-in?${entry}&force=true`);
      client.setQueryData(refreshKey, result);
    } catch (error) {
      setRefreshError(error instanceof Error ? error : new Error("Source refresh failed."));
    } finally {
      setForcing(false);
    }
  };
  const refreshing = forcing || checkIn.isFetching || checkIn.data?.status === "running";
  const sourceError = refreshError || checkIn.error;
  useEffect(() => {
    onReadiness(!refreshing && !sourceError && checkIn.data?.status === "ready");
    return () => onReadiness(false);
  }, [refreshing, sourceError, checkIn.data?.status, onReadiness]);
  const changed = analyze.data && analyze.data.version !== data.card.version;
  const unavailable = data.entry.read_only || Boolean(data.configuration_errors.length) || !data.games.some((game) => !game.locked);
  const proposal = analyze.data;

  return <section className="panel decision-analyst" aria-label="Pool data and AI analysis">
    <div className="workspace-actions">
      <div><span className="eyebrow">Pool check-in</span><h2>Current evidence and AI picks</h2></div>
      <button className="ghost" disabled={refreshing || analyze.isPending || apply.isPending} onClick={() => void refresh()}><RefreshCw size={15} className={refreshing ? "spin" : ""} />{refreshing ? "Refreshing sources…" : "Refresh data"}</button>
    </div>
    <p role="status">{refreshing ? "Checking schedule, odds, results, and enabled news and injury sources…" : sourceError ? "Source check failed. Cached games and picks are shown." : checkIn.data?.status === "partial" ? "Some sources could not refresh. Review coverage before making picks." : checkIn.data?.checked_at ? `Sources checked ${new Date(checkIn.data.checked_at).toLocaleString()}.` : "Waiting for the source check…"}</p>
    <p>Checks automatically when you open this pool, return to this tab, and every five minutes while visible. Checks within one minute share the latest result.</p>
    {sourceError && <p role="alert">{sourceError.message}</p>}
    <details><summary>Source freshness and coverage</summary>
      <ul>{checkIn.data?.sources.map((source) => <li key={source.name}><strong>{source.name}</strong> · {source.status === "refreshed" ? "Checked" : source.status === "manual" ? "Manual updates required" : "Unavailable"}{source.checked_at ? ` · ${new Date(source.checked_at).toLocaleString()}` : ""}{source.detail && <p>{source.detail}</p>}</li>)}</ul>
      <p>Source checks retrieve the latest published data. nflverse odds are not a live market feed; article dates and missing probability coverage still matter.</p>
    </details>
    <label className="field">Analyst<select value={providerId || ""} disabled={analyze.isPending || apply.isPending} onChange={(event) => setProviderId(Number(event.target.value) || undefined)}><option value="">Recommendation default</option>{enabled.map((provider) => <option key={provider.id} value={provider.id}>{provider.name} · {provider.model}</option>)}</select></label>
    <button className="primary" disabled={!enabled.length || busy || refreshing || analyze.isPending || apply.isPending} onClick={() => { apply.reset(); analyze.mutate(); }}><Sparkles size={15} />{analyze.isPending ? "Analyzing current evidence…" : "Analyze picks"}</button>
    <p>Review a proposed card, then set and save picks for this entry. Locked picks stay fixed. Saved picks are recorded in Open Gridiron.</p>
    {!enabled.length && <p><Link to="/settings">Configure an AI provider</Link> to enable pool analysis.</p>}
    {providers.error && <p role="alert">{providers.error.message}</p>}
    {busy && <p>Finish saving your card before using AI picks.</p>}
    {analyze.error && <p role="alert">{analyze.error.message}</p>}
    {proposal?.reason && <p role="alert">{proposal.reason}</p>}
    {proposal?.output && <div aria-live="polite">
      <h3>{proposal.output.summary}</h3>
      <ul>{proposal.output.recommendations.map((text, index) => <li key={index}>{text}</li>)}</ul>
      <p>{proposal.output.risks.join(" ")}</p>
      {proposal.output.missing_information.length > 0 && <p>Missing evidence: {proposal.output.missing_information.join(" ")}</p>}
      {proposal.output.citations.length > 0 && <ul aria-label="Analysis sources">{proposal.output.citations.map((citation, index) => <li key={index}>{/^https?:\/\//i.test(citation) ? <a href={citation} target="_blank" rel="noreferrer">{citation}</a> : citation}</li>)}</ul>}
      {proposal.output.picks.length > 0 && <div className="table-wrap"><table><caption>Proposed picks · {data.entry.name} · Week {data.week.number}</caption><thead><tr><th>Game</th><th>Pick</th><th>{data.pool.pool_type === "confidence" ? "Confidence" : "Slot"}</th></tr></thead><tbody>{proposal.output.picks.map((pick, index) => {
        const game = data.games.find((item) => item.id === pick.game_id);
        return <tr key={index}><td>{game ? `${game.away_team} at ${game.home_team}` : "Unavailable game"}{game?.locked ? " · Locked" : ""}</td><td>{pick.team}</td><td>{data.pool.pool_type === "confidence" ? pick.confidence : pick.slot}</td></tr>;
      })}</tbody></table></div>}
      {saved ? <p role="status">AI picks saved for this entry.</p> : <>
        {changed && <p>The saved card changed. Analyze again before setting picks.</p>}
        <button className="primary" disabled={!proposal.can_apply || unavailable || Boolean(changed) || busy || refreshing || analyze.isPending || apply.isPending || checkIn.data?.status !== "ready" || Boolean(sourceError)} onClick={() => apply.mutate()}>{apply.isPending ? "Checking and saving…" : "Set AI picks"}</button>
      </>}
      <p><Link to={`/analysis?parent_run_id=${proposal.run_id}`}>Open in Analyst desk</Link></p>
      <AnalysisFollowUp key={proposal.run_id} source={{ parent_run_id: proposal.run_id }} providerId={providerId} disabled={analyze.isPending} />
    </div>}
    {apply.error && <p role="alert">{apply.error.message}</p>}
  </section>;
}
