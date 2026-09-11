import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, post } from "../../api";
import type { AnalysisResult, AnalysisRun, Provider } from "../../types";
import { AnalysisFollowUp } from "./AnalysisFollowUp";

export function DecisionAnalyst({ context, question, revision }: { context: { league_id?: number; draft_session_id?: number; pool_id?: number; pool_entry_id?: number; week?: number }; question: string; revision?: number }) {
  const [providerId, setProviderId] = useState<number>();
  const queryClient = useQueryClient();
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api<Provider[]>("/providers") });
  const matchesContext = (run: AnalysisRun) => run.task === "recommendation"
    && run.question === question
    && Object.entries(context).every(([key, value]) => value === undefined || run.context?.[key] === value);
  const history = useQuery({
    queryKey: ["analysis-runs"],
    queryFn: () => api<AnalysisRun[]>("/analysis/runs"),
    select: (runs) => runs.find(matchesContext),
    refetchInterval: (query) => query.state.data?.find(matchesContext)?.status === "running" ? 3_000 : false,
  });
  const analyze = useMutation({
    mutationFn: (_requested: { revision?: number }) => post<AnalysisResult>("/analysis", { task: "recommendation", question, ...context, provider_id: providerId }),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["analysis-runs"] }); },
  });
  const saved = analyze.isIdle ? history.data : undefined;
  const result = analyze.data || saved;
  const runId = analyze.data?.run_id ?? saved?.id;
  const pending = analyze.isPending || saved?.status === "running";
  const draftChanged = (analyze.isPending || Boolean(analyze.data?.output))
    && revision !== undefined && analyze.variables?.revision !== revision;
  const enabled = (providers.data || []).filter((provider) => provider.enabled);
  return <section className="panel decision-analyst" aria-label="AI decision review">
    <h2>AI decision review</h2>
    <p>Review the calculated choices, source evidence, and uncertainty with your configured analyst.</p>
    <label className="field">Analyst<select value={providerId || ""} onChange={(event) => setProviderId(Number(event.target.value) || undefined)}><option value="">Recommendation default</option>{enabled.map((provider) => <option key={provider.id} value={provider.id}>{provider.name} · {provider.model}</option>)}</select></label>
    <button type="button" disabled={!enabled.length || pending || history.isLoading} onClick={() => analyze.mutate({ revision })}>{pending ? "Reviewing evidence…" : "Explain these choices"}</button>
    {pending && <p role="status">Your analyst is reviewing the captured evidence. The answer will appear here when ready and is saved in the Analyst desk.</p>}
    {draftChanged && <p role="status">The draft changed after this review started. This review uses the earlier board; run it again for the latest choices.</p>}
    {providers.isLoading && <p role="status">Loading analysts…</p>}
    {providers.error && <p role="alert">Could not load analysts: {providers.error.message} <button type="button" onClick={() => void providers.refetch()}>Try again</button></p>}
    {!providers.isLoading && !providers.error && !enabled.length && <p><Link to="/settings">Configure an AI provider</Link> to enable explanations.</p>}
    {history.error && <p role="alert">Could not load saved reviews: {history.error.message} <button type="button" onClick={() => void history.refetch()}>Retry saved reviews</button></p>}
    {analyze.error && <p role="alert">{analyze.error.message}</p>}
    {result?.error && <p role="alert">{result.error}</p>}
    {result?.output && <div aria-live="polite">
      <p>{saved ? `Saved review · ${new Date(saved.created_at).toLocaleString()}. The board may have changed; run another review for the latest choices.` : "Review complete."} {result.provider} · {result.model}</p>
      <h3>{result.output.summary}</h3><ul>{result.output.recommendations.map((item, index) => <li key={index}>{item}</li>)}</ul><p>{result.output.risks.join(" ")}</p>
      {result.output.missing_information.length > 0 && <p>Missing evidence: {result.output.missing_information.join(" ")}</p>}
      <Link to={`/analysis?parent_run_id=${runId}`}>Open in Analyst desk</Link>
      {runId && <AnalysisFollowUp key={runId} source={{ parent_run_id: runId }} providerId={providerId} disabled={pending} />}
    </div>}
  </section>;
}
