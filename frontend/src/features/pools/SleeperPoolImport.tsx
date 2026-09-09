import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import type { SleeperPoolInfo } from "../../types";
import { importSleeperPool, poolKeys, previewSleeperPool, refreshSleeperPool } from "./api";
import "./sleeper-pools.css";

function SleeperFacts({ info }: { info: SleeperPoolInfo }) {
  return <div className="sleeper-facts">
    <p>{info.season} · {info.status.replaceAll("_", " ")}{info.current_week ? ` · Week ${info.current_week}` : ""}</p>
    <p>{info.entry_count} entries · {info.participant_count} participants{info.capacity !== null ? ` · Capacity ${info.capacity}` : ""}</p>
    <p>Commissioner: {info.commissioners.join(", ") || "Not provided"}</p>
    {info.rules && <p>{info.rules.picks_per_week} pick{info.rules.picks_per_week === 1 ? "" : "s"} per week · Straight-up winners · Each team {info.rules.max_team_uses === 1 ? "once" : `${info.rules.max_team_uses} times`} · No revives · Ties eliminate · Locks at kickoff</p>}
    {info.entries.map((entry) => <p key={entry.roster_id}>{entry.name} · Sleeper status: {entry.eliminated === null ? "Unknown" : entry.eliminated ? "Eliminated" : "Active"}</p>)}
    {info.unsupported.length > 0 && <div className="error-panel" role="alert"><strong>These rules need additional support</strong><ul>{info.unsupported.map((reason) => <li key={reason}>{reason}</li>)}</ul></div>}
    <details><summary>Rules and settings from Sleeper</summary><pre>{JSON.stringify({ settings: info.settings, scoring_settings: info.scoring_settings }, null, 2)}</pre></details>
    <small>Checked {new Date(info.fetched_at).toLocaleString()} · <a href={info.url} target="_blank" rel="noreferrer">Open pool on Sleeper</a> · <a href="https://support.sleeper.com/en/articles/9689521-nfl-survivor" target="_blank" rel="noreferrer">Sleeper survivor rules</a></small>
  </div>;
}

export function SleeperPoolImport() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState({ url: "", username: "" });
  const [success, setSuccess] = useState("");
  const preview = useMutation({ mutationFn: previewSleeperPool });
  const save = useMutation({
    mutationFn: importSleeperPool,
    onSuccess: async (pool) => {
      setSuccess(`${pool.name} imported. ${pool.entry_count} local ${pool.entry_count === 1 ? "entry" : "entries"} available.`);
      preview.reset();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: poolKeys.overview }),
        queryClient.invalidateQueries({ queryKey: ["pools"] }),
        queryClient.invalidateQueries({ queryKey: poolKeys.entries(pool.id) }),
        queryClient.invalidateQueries({ queryKey: ["pool-week", pool.id] }),
      ]);
    },
  });
  const busy = preview.isPending || save.isPending;
  const edit = (key: "url" | "username", value: string) => {
    setDraft((current) => ({ ...current, [key]: value }));
    preview.reset();
    save.reset();
    setSuccess("");
  };
  return <section className="sleeper-import">
    <h2>Import from Sleeper</h2>
    <p>Bring in survivor rules and league details. Add your username to import your entries.</p>
    <form className="sleeper-import-form" onSubmit={(event) => { event.preventDefault(); setSuccess(""); save.reset(); preview.mutate(draft); }}>
      <label className="field"><span>Sleeper pool URL or league ID</span><input value={draft.url} onChange={(event) => edit("url", event.target.value)} placeholder="https://sleeper.com/leagues/…" required maxLength={300} disabled={busy} /></label>
      <label className="field"><span>Sleeper username (optional)</span><input value={draft.username} onChange={(event) => edit("username", event.target.value)} placeholder="Your username" maxLength={80} disabled={busy} autoCapitalize="none" autoCorrect="off" /></label>
      <button disabled={busy} className="primary">{preview.isPending ? "Reading Sleeper…" : "Preview pool"}</button>
    </form>
    {(preview.error || save.error) && <div role="alert" className="error-panel">{(preview.error || save.error)?.message}</div>}
    {success && <p role="status">{success}</p>}
    {preview.data && <div className="sleeper-preview" aria-live="polite">
      <h3>{preview.data.name}</h3>
      <SleeperFacts info={preview.data} />
      <p className="sleeper-notice">{preview.data.warnings[0]}</p>
      {!preview.data.username && <p>You can add a local entry after importing, or enter your username above.</p>}
      <button className="primary" disabled={busy || !preview.data.rules || Boolean(preview.data.unsupported.length)} onClick={() => save.mutate(draft)}>{save.isPending ? "Importing…" : "Import pool"}</button>
    </div>}
  </section>;
}

export function SleeperPoolDetails({ poolId, info }: { poolId: number; info: SleeperPoolInfo }) {
  const queryClient = useQueryClient();
  const refresh = useMutation({
    mutationFn: () => refreshSleeperPool(poolId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: poolKeys.overview }),
        queryClient.invalidateQueries({ queryKey: ["pools"] }),
        queryClient.invalidateQueries({ queryKey: ["pool-week", poolId] }),
        queryClient.invalidateQueries({ queryKey: poolKeys.entries(poolId) }),
      ]);
    },
  });
  return <div className="sleeper-source">
    <details><summary>Sleeper pool details</summary><SleeperFacts info={info} />
      <div className="action-row"><button disabled={refresh.isPending} onClick={() => refresh.mutate()}><RefreshCw size={14} className={refresh.isPending ? "spin" : undefined} />{refresh.isPending ? "Refreshing Sleeper…" : "Refresh Sleeper details"}</button></div>
      {refresh.error && <p className="error-panel" role="alert">{refresh.error.message}</p>}
      {refresh.isSuccess && <p role="status">Sleeper details refreshed.</p>}
    </details>
    <p className="sleeper-notice">{info.warnings[0]}</p>
  </div>;
}
