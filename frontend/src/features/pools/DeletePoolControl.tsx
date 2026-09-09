import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import type { PoolOverview, PoolOverviewItem } from "../../types";
import { deletePool, poolKeys } from "./api";
import "./pool-settings.css";

export function DeletePoolControl({ pool, onDeleted }: {
  pool: PoolOverviewItem;
  onDeleted: (name: string) => void;
}) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const deletion = useMutation({
    mutationFn: () => deletePool(pool.id),
    onSuccess: async () => {
      await queryClient.cancelQueries({ queryKey: poolKeys.overview });
      queryClient.setQueryData<PoolOverview>(poolKeys.overview, (current) => current && ({
        ...current, pools: current.pools.filter((item) => item.id !== pool.id),
      }));
      for (const key of ["pool-entries", "pool-week", "pool-standings", "pool-strategy"]) {
        queryClient.removeQueries({ queryKey: [key, pool.id] });
      }
      onDeleted(pool.name);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: poolKeys.overview }),
        queryClient.invalidateQueries({ queryKey: ["pools"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ]);
    },
  });
  useEffect(() => { if (confirming) cancelButton.current?.focus(); }, [confirming]);
  const cancel = () => {
    if (deletion.isPending) return;
    setConfirming(false);
    deletion.reset();
    trigger.current?.focus();
  };

  return <div className="pool-delete" onKeyDown={(event) => {
    if (event.key === "Escape" && confirming) { event.preventDefault(); cancel(); }
  }}>
    <button ref={trigger} type="button" className="ghost pool-delete-button" aria-label={`Delete ${pool.name}`} aria-expanded={confirming} aria-controls={confirming ? `delete-pool-${pool.id}` : undefined} disabled={deletion.isPending} onClick={() => { deletion.reset(); setConfirming(true); }}>
      <Trash2 size={16} aria-hidden="true" />Delete pool
    </button>
    {confirming && <section id={`delete-pool-${pool.id}`} className="pool-delete-confirmation" aria-labelledby={`delete-pool-title-${pool.id}`}>
      <h3 id={`delete-pool-title-${pool.id}`}>Delete “{pool.name}”?</h3>
      <p>This permanently deletes this pool, all its entries, and their saved picks from Open Gridiron. This cannot be undone.</p>
      {pool.sleeper && <p>Your pool and picks on Sleeper will remain available.</p>}
      <div className="action-row">
        <button ref={cancelButton} type="button" disabled={deletion.isPending} onClick={cancel}>Cancel</button>
        <button type="button" className="pool-delete-button confirm" disabled={deletion.isPending} onClick={() => deletion.mutate()}>{deletion.isPending ? "Deleting…" : "Delete permanently"}</button>
      </div>
      {deletion.error && <p className="error-panel" role="alert">Could not delete this pool. {deletion.error.message} Try again.</p>}
    </section>}
  </div>;
}
