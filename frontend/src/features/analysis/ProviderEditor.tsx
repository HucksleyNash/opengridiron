import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { Provider } from "../../types";

export function ProviderTasks({ value, onChange }: { value: string[]; onChange: (tasks: string[]) => void }) {
  return <fieldset className="provider-tasks"><legend>Default tasks</legend>{["chat", "recommendation", "news"].map((task) => <label key={task}><input type="checkbox" checked={value.includes(task)} onChange={(event) => onChange(event.target.checked ? [...value, task] : value.filter((item) => item !== task))} />{task === "recommendation" ? "League, draft and pool recommendations" : task === "news" ? "Automatic news review" : "Analyst chat"}</label>)}<small>Each task has one default provider. Automatic news review may use provider credits.</small></fieldset>;
}

export function ProviderEditor({ provider }: { provider: Provider }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState({ name: provider.name, model: provider.model, base_url: provider.base_url || "", enabled: provider.enabled, task_defaults: provider.task_defaults });
  const [apiKey, setApiKey] = useState("");
  useEffect(() => setDraft({ name: provider.name, model: provider.model, base_url: provider.base_url || "", enabled: provider.enabled, task_defaults: provider.task_defaults }), [provider]);
  const save = useMutation({ mutationFn: () => api(`/providers/${provider.id}`, { method: "PATCH", body: JSON.stringify({ ...draft, base_url: draft.base_url || null, ...(apiKey ? { api_key: apiKey } : {}) }) }), onSuccess: () => { setApiKey(""); void queryClient.invalidateQueries({ queryKey: ["providers"] }); } });
  return <details><summary>Edit provider</summary><form onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
    <label className="field">Display name<input required value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
    <label className="field">Model<input required value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} /></label>
    <label className="field">Base URL or local provider<input value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} /></label>
    <label className="field">Replace API key<input type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="Leave blank to keep stored key" /></label>
    <label><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} />Enabled</label>
    <ProviderTasks value={draft.task_defaults} onChange={(task_defaults) => setDraft({ ...draft, task_defaults })} />
    <button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save provider"}</button>{save.error && <p role="alert">{save.error.message}</p>}{save.isSuccess && <p role="status">Provider saved.</p>}
  </form></details>;
}
