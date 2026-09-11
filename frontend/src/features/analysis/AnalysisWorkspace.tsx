import { useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronLeft, ChevronRight, Plus, Search } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { api, post } from "../../api";
import type { AnalysisOutput, AnalysisResult, AnalysisRun, League, Pool, PoolEntry, Provider } from "../../types";
import { PlayerMentions } from "../leagues/PlayerDetails";
import "./analysis-workspace.css";

type Scope = { league_id?: number; league_name?: string; pool_id?: number; pool_name?: string; pool_entry_id?: number; pool_entry_name?: string; draft_session_id?: number; team_name?: string; week?: number };
type Summary = { id: number; root_id: number; latest_id: number; title: string; preview: string; context: Scope; status: string; created_at: string; updated_at: string; reply_count: number };
type Library = { items: Summary[]; total: number; run_count: number; offset: number; limit: number; has_more: boolean };
type Conversation = { selected_id: number; root_id: number; title: string; context: Scope; turns: AnalysisRun[]; branches: { id: number; title: string; status: string; updated_at: string }[]; lineage_incomplete: boolean };
type Draft = { id: number; kind: string; status: string; team_count: number; owner_team_slot: number };
const FILTER_KEYS = ["q", "scope", "filter_week", "status", "page"];
const originalQuestion = "Which lineup decision has the biggest evidence-backed edge this week?";
const positive = (value: string | null) => value && /^\d+$/.test(value) && Number(value) > 0 ? Number(value) : undefined;
const timestamp = (value: string) => new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const stateLabel = (value: string) => ({ completed: "Completed", failed: "Failed", running: "Running", queued: "Queued" }[value] || value);

function contextLabel(context: Scope) {
  const name = context.league_name || context.pool_name || (context.league_id ? `League #${context.league_id}` : context.pool_id ? `Pool #${context.pool_id}` : "Context not recorded");
  return [name, context.team_name || context.pool_entry_name || (context.pool_entry_id ? `Entry #${context.pool_entry_id}` : null), context.week ? `Week ${context.week}` : null, context.draft_session_id ? `Draft #${context.draft_session_id}` : null].filter(Boolean).join(" · ");
}

function Output({ output, id }: { output: AnalysisOutput; id: number }) {
  const sections = [output.recommendations.length ? "recommendations" : null, output.risks.length || output.missing_information.length ? "caveats" : null, output.citations.length ? "sources" : null].filter(Boolean) as string[];
  return <>
    <p className="analysis-summary"><PlayerMentions text={output.summary} /></p>
    {sections.length > 1 && <nav className="analysis-section-links" aria-label={`Answer ${id} sections`}>{sections.map(section => <a key={section} href={`#answer-${id}-${section}`}>{section === "caveats" ? "Risks & missing evidence" : section === "sources" ? "Sources" : "Recommendations"}</a>)}</nav>}
    {!!output.recommendations.length && <section id={`answer-${id}-recommendations`}><h3>Recommendations</h3><ol>{output.recommendations.map((text, index) => <li key={index}><PlayerMentions text={text} /></li>)}</ol></section>}
    {!!(output.risks.length || output.missing_information.length) && <section id={`answer-${id}-caveats`}>
      {!!output.risks.length && <><h3>Risks</h3><ul>{output.risks.map((text, index) => <li key={index}><PlayerMentions text={text} /></li>)}</ul></>}
      {!!output.missing_information.length && <><h3>Missing evidence</h3><ul>{output.missing_information.map((text, index) => <li key={index}><PlayerMentions text={text} /></li>)}</ul></>}
    </section>}
    {!!output.citations.length && <section id={`answer-${id}-sources`}><h3>Sources</h3><ul className="analysis-sources">{output.citations.map((source, index) => {
      let url: URL | undefined;
      try { const candidate = new URL(source); if (["https:", "http:"].includes(candidate.protocol)) url = candidate; } catch { /* Keep non-URL citations as text. */ }
      return <li key={index}>{url ? <a href={url.href} target="_blank" rel="noreferrer">{url.hostname.replace(/^www\./, "")} · source {index + 1}<span className="sr-only"> (opens in a new tab)</span></a> : source}</li>;
    })}</ul></section>}
  </>;
}

export default function AnalysisWorkspace() {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const selectedId = positive(params.get("parent_run_id"));
  const reportId = positive(params.get("league_report_id"));
  const view = params.get("view") === "history" ? "history" : "reader";
  const page = positive(params.get("page")) || 1;
  const [search, setSearch] = useState(params.get("q") || "");
  const [question, setQuestion] = useState("");
  const [providerId, setProviderId] = useState<number>();
  const [scope, setScope] = useState(params.get("league_id") ? `league:${params.get("league_id")}` : params.get("pool_id") ? `pool:${params.get("pool_id")}` : "");
  const [entryId, setEntryId] = useState(params.get("pool_entry_id") || "");
  const [team, setTeam] = useState(params.get("team_name") || "");
  const [week, setWeek] = useState(params.get("week") || "");
  const [draftId, setDraftId] = useState(params.get("draft_session_id") || "");
  const [notice, setNotice] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [navigationTick, setNavigationTick] = useState(0);
  const readerHeading = useRef<HTMLHeadingElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const libraryHeading = useRef<HTMLHeadingElement>(null);
  const rowRefs = useRef(new Map<number, HTMLButtonElement>());
  const libraryScroll = useRef<HTMLDivElement>(null);
  const libraryPosition = useRef({ inner: 0, document: 0 });
  const restoreLibrary = useRef(false);
  const lastOpenedRoot = useRef<number | undefined>(undefined);
  const answerToFocus = useRef<number | undefined>(undefined);
  const focusReader = useRef(Boolean(selectedId));
  const submitting = useRef(false);
  const mounted = useRef(true);
  const identity = `${selectedId || "new"}:${reportId || ""}`;
  const activeIdentity = useRef(identity);
  activeIdentity.current = identity;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api<Provider[]>("/providers") });
  const leagues = useQuery({ queryKey: ["leagues"], queryFn: () => api<League[]>("/leagues") });
  const pools = useQuery({ queryKey: ["pools"], queryFn: () => api<Pool[]>("/pools") });
  const libraryParams = new URLSearchParams({ offset: String((page - 1) * 25), limit: "25" });
  for (const key of ["q", "scope", "status"]) if (params.get(key)) libraryParams.set(key, params.get(key)!);
  if (params.get("filter_week")) libraryParams.set("week", params.get("filter_week")!);
  const library = useQuery({ queryKey: ["analysis-library", libraryParams.toString()], queryFn: ({ signal }) => api<Library>(`/analysis/library?${libraryParams}`, { signal }) });
  const detail = useQuery({ queryKey: ["analysis-conversation", selectedId], queryFn: ({ signal }) => api<Conversation>(`/analysis/runs/${selectedId}`, { signal }), enabled: Boolean(selectedId), retry: false });
  const leagueId = scope.startsWith("league:") ? Number(scope.split(":")[1]) : undefined;
  const poolId = scope.startsWith("pool:") ? Number(scope.split(":")[1]) : undefined;
  const drafts = useQuery({ queryKey: ["draft-sessions-v2", leagueId], queryFn: () => api<Draft[]>(`/leagues/${leagueId}/draft-sessions`), enabled: Boolean(leagueId && !selectedId && !reportId) });
  const entries = useQuery({ queryKey: ["pool-entries", poolId], queryFn: () => api<PoolEntry[]>(`/pools/${poolId}/entries`), enabled: Boolean(poolId && !selectedId && !reportId) });
  const activeEntries = entries.data?.filter(entry => entry.active) || [];
  const selectedEntryId = entryId ? Number(entryId) : activeEntries.length === 1 ? activeEntries[0].id : undefined;
  const enabledProviders = (providers.data || []).filter(provider => provider.enabled);
  const analyst = enabledProviders.find(provider => provider.id === providerId) || enabledProviders.find(provider => provider.task_defaults?.includes("chat"));
  const turns = detail.data?.turns || [];
  const selected = turns.at(-1);
  const frozen = Boolean(selectedId || reportId);
  const savedScope: Scope = { ...(detail.data?.context || { league_id: positive(params.get("league_id")), pool_id: positive(params.get("pool_id")), team_name: params.get("team_name") || undefined, week: positive(params.get("week")), draft_session_id: positive(params.get("draft_session_id")) }) };
  if (!savedScope.league_name && savedScope.league_id) savedScope.league_name = leagues.data?.find(item => item.id === savedScope.league_id)?.name;
  if (!savedScope.pool_name && savedScope.pool_id) savedScope.pool_name = pools.data?.find(item => item.id === savedScope.pool_id)?.name;

  function update(values: Record<string, string | undefined>, replace = false) {
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
    setParams(next, { replace });
  }
  function openRun(id: number, rootId?: number) {
    if (rootId) { libraryPosition.current = { inner: libraryScroll.current?.scrollTop || 0, document: window.scrollY }; lastOpenedRoot.current = rootId; }
    focusReader.current = true;
    setNavigationTick(value => value + 1);
    setQuestion(""); setNotice(""); mutation.reset();
    update({ parent_run_id: String(id), league_report_id: undefined, view: undefined });
  }
  function startNew() {
    const next = new URLSearchParams();
    FILTER_KEYS.forEach(key => { if (params.get(key)) next.set(key, params.get(key)!); });
    setParams(next); setQuestion(""); setScope(""); setEntryId(""); setTeam(""); setWeek(""); setDraftId(""); setNotice(""); mutation.reset();
    requestAnimationFrame(() => { readerHeading.current?.focus(); readerHeading.current?.scrollIntoView({ block: "start" }); });
  }
  function backToHistory() {
    restoreLibrary.current = true;
    focusReader.current = false;
    update({ view: "history" });
  }
  useEffect(() => { setSearch(params.get("q") || ""); }, [params.get("q")]);
  useEffect(() => {
    if (!selectedId) {
      setScope(params.get("league_id") ? `league:${params.get("league_id")}` : params.get("pool_id") ? `pool:${params.get("pool_id")}` : "");
      setTeam(params.get("team_name") || ""); setWeek(params.get("week") || "");
      setDraftId(params.get("draft_session_id") || ""); setEntryId(params.get("pool_entry_id") || "");
    }
    if (answerToFocus.current !== selectedId) focusReader.current = true;
  }, [selectedId, reportId, params.get("league_id"), params.get("pool_id"), params.get("team_name"), params.get("week"), params.get("draft_session_id"), params.get("pool_entry_id")]);
  useEffect(() => {
    const timer = setTimeout(() => { if (search !== (params.get("q") || "")) update({ q: search, page: undefined }, true); }, 250);
    return () => clearTimeout(timer);
  }, [search, params]);
  useEffect(() => {
    if (view === "history") {
      if (!restoreLibrary.current) return;
      restoreLibrary.current = false;
      if (libraryScroll.current) libraryScroll.current.scrollTop = libraryPosition.current.inner;
      const row = rowRefs.current.get(lastOpenedRoot.current || detail.data?.root_id || 0);
      (row || libraryHeading.current)?.focus({ preventScroll: true });
      window.scrollTo({ top: row ? libraryPosition.current.document : 0, behavior: "instant" });
      return;
    }
    if (answerToFocus.current && detail.data?.selected_id === answerToFocus.current) {
      const answer = document.getElementById(`analysis-turn-${answerToFocus.current}`);
      answerToFocus.current = undefined;
      answer?.focus(); answer?.scrollIntoView({ block: "start" });
    } else if (focusReader.current && view === "reader") {
      focusReader.current = false;
      readerHeading.current?.focus(); readerHeading.current?.scrollIntoView({ block: "start" });
    }
  }, [detail.data, detail.error, view, selectedId, navigationTick]);

  const mutation = useMutation({
    mutationFn: ({ text }: { text: string; identity: string }) => post<AnalysisResult>("/analysis", {
      task: "chat", question: text, provider_id: analyst?.id,
      ...(selectedId ? { parent_run_id: selectedId } : reportId ? { league_report_id: reportId } : {
        league_id: leagueId, pool_id: poolId, pool_entry_id: selectedEntryId, team_name: team || undefined,
        week: week ? Number(week) : undefined, draft_session_id: draftId ? Number(draftId) : undefined,
      }),
    }).then(result => { if (result.status !== "completed" || !result.output) throw new Error(result.error || "The analyst could not answer. Your question is kept below."); return result; }),
    onMutate: ({ text }) => { setSubmitted(text); setNotice(""); },
    onSuccess: (result, request) => {
      if (!mounted.current || request.identity !== activeIdentity.current) return;
      const text = request.text;
      const run: AnalysisRun = { ...result, id: result.run_id, task: "chat", question: text, created_at: new Date().toISOString(), parent_run_id: result.parent_run_id ?? selectedId, context: result.context || savedScope };
      const conversation: Conversation = { selected_id: run.id, root_id: detail.data?.root_id || run.id, title: detail.data?.title || text, context: (result.context || (frozen ? savedScope : { league_id: leagueId, pool_id: poolId, team_name: team, week: Number(week) || undefined })) as Scope, turns: [...turns, run], branches: [], lineage_incomplete: false };
      answerToFocus.current = run.id;
      client.setQueryData(["analysis-conversation", run.id], conversation);
      update({ parent_run_id: String(run.id), league_report_id: undefined, view: undefined });
      setQuestion(""); setSubmitted(""); setNotice("Answer saved. You can ask another question using this conversation's saved evidence.");
    },
    onSettled: () => {
      submitting.current = false;
      void client.invalidateQueries({ queryKey: ["analysis-library"] });
      void client.invalidateQueries({ queryKey: ["analysis-runs"] });
    },
  });
  const pending = mutation.isPending;
  const canReply = !selectedId || Boolean(selected?.status === "completed" && selected.output);
  const ready = Boolean(analyst && canReply && !detail.isError && (!selectedId || detail.data) && (frozen || !poolId || (entries.isSuccess && (activeEntries.length === 0 || selectedEntryId))));
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || pending || submitting.current || !ready) return;
    submitting.current = true;
    mutation.mutate({ text: question.trim(), identity });
  }

  return <div className={`analyst-workspace analyst-view-${view}`}>
    <header className="analyst-page-heading"><h1>Analyst desk</h1><button type="button" className="ghost" onClick={startNew} disabled={pending}><Plus size={16} aria-hidden="true" />New analysis</button></header>
    <div className="analyst-work-grid">
      <aside className="analysis-library" aria-labelledby="analysis-library-title">
        <div className="analysis-library-heading"><h2 id="analysis-library-title" ref={libraryHeading} tabIndex={-1}>Analysis history</h2>{selectedId && view === "history" && <button type="button" className="ghost mobile-library-toggle" onClick={() => { focusReader.current = true; update({ view: undefined }); }}>Return to answer</button>}</div>
        <label className="analysis-search"><span>Search analyses</span><span className="analysis-search-control"><Search size={16} aria-hidden="true" /><input type="search" maxLength={300} value={search} onChange={event => setSearch(event.target.value)} placeholder="Question, player, team, or answer" /></span></label>
        <details className="analysis-library-filters" open={Boolean(params.get("scope") || params.get("filter_week") || params.get("status")) || undefined}>
          <summary>Filter history</summary>
          <label>League or pool<select value={params.get("scope") || ""} onChange={event => update({ scope: event.target.value, page: undefined })}><option value="">All contexts</option>{leagues.data?.map(item => <option key={`league:${item.id}`} value={`league:${item.id}`}>{item.name}</option>)}{pools.data?.map(item => <option key={`pool:${item.id}`} value={`pool:${item.id}`}>{item.name}</option>)}</select></label>
          <div className="analysis-filter-pair"><label>Week<select value={params.get("filter_week") || ""} onChange={event => update({ filter_week: event.target.value, page: undefined })}><option value="">All weeks</option>{Array.from({ length: 18 }, (_, i) => <option key={i + 1}>{i + 1}</option>)}</select></label><label>Status<select value={params.get("status") || ""} onChange={event => update({ status: event.target.value, page: undefined })}><option value="">All statuses</option><option value="completed">Completed</option><option value="failed">Failed</option><option value="running">Running</option><option value="queued">Queued</option></select></label></div>
        </details>
        <p className="analysis-library-count" role="status">{library.isFetching ? "Loading saved analyses…" : library.data ? `${library.data.total} conversation${library.data.total === 1 ? "" : "s"} · ${library.data.run_count} saved answers` : ""}</p>
        {library.error && <p role="alert">Could not load history. <button type="button" className="ghost" onClick={() => void library.refetch()}>Retry history</button></p>}
        <div ref={libraryScroll} className="analysis-library-list">
          {library.data?.items.map(item => <button ref={node => { if (node) rowRefs.current.set(item.root_id, node); else rowRefs.current.delete(item.root_id); }} type="button" key={item.root_id} className={`analysis-library-row ${detail.data?.root_id === item.root_id ? "selected" : ""}`} aria-label={`Open saved analysis: ${item.title}`} aria-describedby={`analysis-row-${item.root_id}-context analysis-row-${item.root_id}-meta`} aria-current={detail.data?.root_id === item.root_id ? "true" : undefined} disabled={pending} onClick={() => openRun(item.id, item.root_id)}>
            <strong>{item.title}</strong><span id={`analysis-row-${item.root_id}-context`} className="analysis-row-context">{contextLabel(item.context)}</span><span className="analysis-row-preview">{item.preview}</span><span id={`analysis-row-${item.root_id}-meta`} className="analysis-row-meta"><span className={`analysis-run-state state-${item.status}`}>{stateLabel(item.status)}</span><time dateTime={item.updated_at}>{timestamp(item.updated_at)}</time><span>{item.reply_count} {item.reply_count === 1 ? "reply" : "replies"}</span></span>
          </button>)}
          {library.isSuccess && !library.data.items.length && <div className="analysis-library-empty"><p>{library.data.run_count ? "No conversations match these filters." : "Your analyses will be saved here automatically."}</p>{library.data.run_count ? <button type="button" className="ghost" onClick={() => { setSearch(""); update({ q: undefined, scope: undefined, filter_week: undefined, status: undefined, page: undefined }); }}>Clear filters</button> : <button type="button" className="ghost" onClick={startNew}>Start an analysis</button>}</div>}
        </div>
        {library.data && (page > 1 || library.data.has_more) && <nav className="analysis-pagination" aria-label="History pages"><button type="button" className="ghost" disabled={page === 1 || library.isFetching} onClick={() => update({ page: page > 2 ? String(page - 1) : undefined })}><ChevronLeft size={16} />Previous</button><span>Page {page}</span><button type="button" className="ghost" disabled={!library.data.has_more || library.isFetching} onClick={() => update({ page: String(page + 1) })}>Next<ChevronRight size={16} /></button></nav>}
      </aside>
      <section className="analysis-reader analysis-answer" aria-labelledby="analysis-reader-title" aria-busy={pending || detail.isLoading}>
        <button type="button" className="ghost mobile-library-toggle" onClick={backToHistory} disabled={pending}><ArrowLeft size={16} aria-hidden="true" />Back to history</button>
        <h2 id="analysis-reader-title" ref={readerHeading} tabIndex={-1}>{selectedId ? detail.data?.title || "Saved analysis" : reportId ? "Continue saved report" : "New analysis"}</h2>
        {selectedId && detail.isLoading && <p role="status">Loading the saved conversation…</p>}
        {detail.error && selectedId && <p role="alert">{detail.error.message} <button type="button" className="ghost" onClick={() => void detail.refetch()}>Retry saved analysis</button></p>}
        {frozen && (!selectedId || detail.data) && <div className="analysis-context-header"><strong>{contextLabel(savedScope)}</strong><p>Using saved evidence{turns[0]?.created_at ? ` · captured ${timestamp(turns[0].created_at)}` : reportId ? ` · report #${reportId}` : ""}. Follow-ups keep this context.</p><button type="button" className="ghost" disabled={pending} onClick={startNew}>New analysis with current evidence</button></div>}
        {detail.data?.lineage_incomplete && <p role="status">Some earlier answers are unavailable. The saved answers below are preserved.</p>}
        <div className="analysis-conversation">
          {turns.map((run, index) => <article id={`analysis-turn-${run.id}`} tabIndex={-1} className="analysis-turn" key={run.id} aria-label={`Answer ${index + 1}`}>
            <div className="analysis-question"><h3>{index ? "Follow-up question" : "Question"}</h3><p><PlayerMentions text={run.question || "The question was not recorded for this earlier analysis."} /></p></div>
            <p className="analysis-turn-meta">{run.provider || "Removed provider"} · {run.model || "Unknown model"} · <time dateTime={run.created_at}>{timestamp(run.created_at)}</time> · {stateLabel(run.status)}</p>
            {run.output ? <Output output={run.output} id={run.id} /> : <p role={run.status === "failed" ? "alert" : "status"}>{run.error || "This saved analysis is still processing."}</p>}
          </article>)}
        </div>
        {!!detail.data?.branches.length && <details className="analysis-branches"><summary>Other replies in this conversation ({detail.data.branches.length})</summary><p>Opening a reply shows its earlier answers. A new question continues the answer currently shown.</p>{detail.data.branches.map(branch => <button type="button" className="ghost" key={branch.id} disabled={pending} onClick={() => openRun(branch.id)}>{branch.title} · {timestamp(branch.updated_at)}</button>)}</details>}
        {selected && !canReply && <p>This answer cannot be continued. <button type="button" className="ghost" onClick={startNew}>Start a new analysis</button></p>}
        {pending && <div className="analysis-request-status" role="status"><p><strong>You asked:</strong> {submitted}</p><p>The analyst is reviewing your question{frozen ? " and this conversation’s saved evidence" : " and the selected context"}… Keep this page open while the answer is prepared.</p></div>}
        <p className="analysis-announcement" role="status">{notice}</p>
        {canReply && !detail.error && <form className="analysis-composer" onSubmit={submit}>
          {!frozen && <><p>Choose the evidence for your question. The answer and its sources are saved automatically.</p><label>Context<select value={scope} disabled={pending} onChange={event => { setScope(event.target.value); setTeam(""); setDraftId(""); setEntryId(""); }}><option value="">General NFL question</option>{leagues.data?.map(item => <option key={`league:${item.id}`} value={`league:${item.id}`}>League · {item.name}</option>)}{pools.data?.map(item => <option key={`pool:${item.id}`} value={`pool:${item.id}`}>Pool · {item.name}</option>)}</select></label>
            {poolId && <label>Pool entry<select value={selectedEntryId || ""} required={activeEntries.length > 0} disabled={pending || entries.isLoading} onChange={event => setEntryId(event.target.value)}><option value="">{activeEntries.length ? "Choose an entry" : "No active entries · pool context only"}</option>{activeEntries.map(entry => <option key={entry.id} value={entry.id}>{entry.name}</option>)}</select></label>}
            {(leagueId || poolId) && <div className="analysis-context-fields">{leagueId && <label>Fantasy team<select value={team} disabled={pending} onChange={event => setTeam(event.target.value)}><option value="">Saved owner team</option>{leagues.data?.find(item => item.id === leagueId)?.team_names?.map(name => <option key={name}>{name}</option>)}</select></label>}<label>Week<input type="number" min={1} max={18} step={1} value={week} onChange={event => setWeek(event.target.value)} placeholder="Current week" disabled={pending} /></label></div>}
            {leagueId && !!drafts.data?.length && <details className="analysis-draft-settings"><summary>Include a draft</summary><label>Draft context<select value={draftId} disabled={pending} onChange={event => setDraftId(event.target.value)}><option value="">No draft · league context only</option>{drafts.data.map(draft => <option key={draft.id} value={draft.id}>{draft.kind} #{draft.id} · {draft.status} · {draft.team_count} teams</option>)}</select></label></details>}
          </>}
          <label htmlFor="analysis-question-input">{frozen ? "Your follow-up question" : "Question"}</label>
          <textarea id="analysis-question-input" ref={composer} rows={4} maxLength={10000} required value={question} readOnly={pending} aria-describedby="analysis-composer-help" onChange={event => { setQuestion(event.target.value); if (mutation.isError) mutation.reset(); }} placeholder={frozen ? "Why this recommendation? What would change your answer?" : originalQuestion} />
          <p id="analysis-composer-help" className="analysis-composer-help">{frozen ? "Continues the last answer shown above using its saved evidence." : "Ask a specific question about the selected context."}</p>
          <div className="analysis-composer-actions"><button type="submit" className="primary" disabled={!question.trim() || !ready || pending}>{pending ? "Analyzing…" : frozen ? "Ask follow-up" : "Analyze"}</button><details className="analysis-provider-settings"><summary>Analyst: {analyst?.name || "Unavailable"}</summary><label>AI analyst<select value={providerId || ""} disabled={pending} onChange={event => setProviderId(Number(event.target.value) || undefined)}><option value="">Configured chat default</option>{enabledProviders.map(provider => <option key={provider.id} value={provider.id}>{provider.name} · {provider.model}</option>)}</select></label></details></div>
          {providers.isLoading && <p role="status">Loading analysts…</p>}
          {providers.error && <p role="alert">Could not load analysts. <button type="button" className="ghost" onClick={() => void providers.refetch()}>Retry analysts</button></p>}
          {providers.isSuccess && !analyst && <p role="alert">Choose an enabled analyst above or <Link to="/settings">configure a chat default in Settings</Link>.</p>}
          {(leagues.error || pools.error || drafts.error || entries.error) && !frozen && <p role="alert">Some context choices could not load. <button type="button" className="ghost" onClick={() => { void leagues.refetch(); void pools.refetch(); if (leagueId) void drafts.refetch(); if (poolId) void entries.refetch(); }}>Retry context</button></p>}
          {mutation.error && <p role="alert">{mutation.error.message} Your question is kept above; try again when ready.</p>}
        </form>}
      </section>
    </div>
  </div>;
}
