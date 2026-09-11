import { useEffect, useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { MessageSquare } from "lucide-react";
import { api, post } from "../../api";
import type { AnalysisResult, Provider } from "../../types";
import { PlayerMentions } from "../leagues/PlayerDetails";
import "./analysis-follow-up.css";

type Source = { parent_run_id: number; league_report_id?: never } | { league_report_id: number; parent_run_id?: never };
type Exchange = { question: string; result: AnalysisResult };

// Callers key this component by its saved source so changing reports resets the conversation.
export function AnalysisFollowUp({ source, providerId, disabled = false, onAnswer }: {
  source: Source;
  providerId?: number;
  disabled?: boolean;
  onAnswer?: (runId: number) => void;
}) {
  const id = useId();
  const input = useRef<HTMLTextAreaElement>(null);
  const submitting = useRef(false);
  const mounted = useRef(true);
  const client = useQueryClient();
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api<Provider[]>("/providers") });
  const enabled = (providers.data || []).filter((provider) => provider.enabled);
  const analyst = enabled.find((provider) => provider.id === providerId)
    ?? enabled.find((provider) => provider.task_defaults?.includes("chat"))
    ?? enabled.find((provider) => provider.task_defaults?.includes("recommendation"))
    ?? enabled[0];
  const [question, setQuestion] = useState("");
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const parentId = exchanges.at(-1)?.result.run_id ?? source.parent_run_id;
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const mutation = useMutation({
    mutationFn: async (text: string) => {
      const result = await post<AnalysisResult>("/analysis", {
        task: "chat",
        question: text,
        provider_id: analyst?.id,
        ...(parentId ? { parent_run_id: parentId } : source),
      });
      if (result.status !== "completed" || !result.output) {
        throw new Error(result.error || "The analyst could not answer. Please try again.");
      }
      return result;
    },
    onSuccess: (result, text) => {
      if (!mounted.current) return;
      setExchanges((previous) => [...previous, { question: text, result }]);
      setQuestion("");
      onAnswer?.(result.run_id);
    },
    onSettled: () => {
      submitting.current = false;
      void client.invalidateQueries({ queryKey: ["analysis-runs"] });
      requestAnimationFrame(() => input.current?.focus());
    },
  });

  return <section className="analysis-follow-up" aria-labelledby={`${id}-title`}>
    <h3 className="analysis-follow-up-title" id={`${id}-title`}><MessageSquare size={18} aria-hidden="true" />Follow-up questions</h3>
    <p className="muted" id={`${id}-help`}>Ask about this analysis using its saved evidence and earlier answers. Replies are saved in <Link to="/analysis">Analyst desk</Link>.</p>
    <div className="analysis-follow-up-thread" aria-live="polite" aria-relevant="additions">
      {exchanges.map(({ question: text, result }) => <article className="analysis-exchange" key={result.run_id}>
        <div className="analysis-follow-up-question"><span className="eyebrow">You asked</span><p><PlayerMentions text={text} /></p></div>
        <span className="eyebrow">{result.provider} · {result.model}</span>
        <p><PlayerMentions text={result.output!.summary} /></p>
        {!!result.output!.recommendations.length && <ul>{result.output!.recommendations.map((value, index) => <li key={index}><PlayerMentions text={value} /></li>)}</ul>}
        {!!result.output!.risks.length && <p><strong>Risks:</strong> <PlayerMentions text={result.output!.risks.join(" ")} /></p>}
        {!!result.output!.missing_information.length && <p><strong>Missing evidence:</strong> <PlayerMentions text={result.output!.missing_information.join(" ")} /></p>}
        {!!result.output!.citations.length && <ul aria-label="Follow-up sources">{result.output!.citations.map((citation, index) => <li key={index}>{/^https?:\/\//i.test(citation) ? <a href={citation} target="_blank" rel="noreferrer">{citation}</a> : citation}</li>)}</ul>}
      </article>)}
    </div>
    <form onSubmit={(event) => {
      event.preventDefault();
      const text = question.trim();
      if (!text || disabled || !analyst || submitting.current) return;
      submitting.current = true;
      mutation.mutate(text);
    }}>
      <label htmlFor={`${id}-question`}>Your follow-up question</label>
      <textarea ref={input} id={`${id}-question`} aria-describedby={`${id}-help`} rows={3} maxLength={10000} required
        placeholder="Why this recommendation? What would change your answer?"
        value={question} readOnly={mutation.isPending} disabled={disabled}
        onChange={(event) => { setQuestion(event.target.value); if (mutation.isError) mutation.reset(); }} />
      <button type="submit" className="primary" disabled={disabled || !analyst || mutation.isPending || !question.trim()}>{mutation.isPending ? "Answering…" : "Ask follow-up"}</button>
    </form>
    {providers.isLoading && <p role="status">Loading analysts…</p>}
    {providers.error && <p role="alert">Could not load analysts. <button type="button" className="ghost" onClick={() => void providers.refetch()}>Retry analysts</button></p>}
    {providers.isSuccess && !analyst && <p><Link to="/settings">Configure an AI provider</Link> to ask follow-up questions.</p>}
    {mutation.isPending && <p role="status">The analyst is reviewing your question and this conversation…</p>}
    {mutation.error && <p role="alert">{mutation.error.message} Your question is kept above; you can try again.</p>}
  </section>;
}
