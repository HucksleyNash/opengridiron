import { FormEvent, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { createPoolEntry, poolKeys } from "./api";

export function PoolEntryForm({ poolId, poolName }: { poolId: number; poolName: string }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [validationError, setValidationError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const id = `pool-${poolId}-entry-name`;
  const addEntry = useMutation({
    mutationFn: (entryName: string) => createPoolEntry(poolId, entryName),
    onSuccess: async () => {
      setName("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: poolKeys.overview }),
        queryClient.invalidateQueries({ queryKey: poolKeys.entries(poolId) }),
      ]);
    },
  });
  const error = validationError || (addEntry.error
    ? `Could not add this entry. ${addEntry.error.message} Try Add entry again.`
    : "");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (addEntry.isPending) return;
    addEntry.reset();
    const entryName = name === "" ? "Main entry" : name.trim();
    if (!entryName || Array.from(entryName).length > 160) {
      setValidationError(!entryName ? "Enter a name with at least one non-space character, or clear the field to use Main entry." : "Use an entry name of 160 characters or fewer.");
      input.current?.focus();
      return;
    }
    setValidationError("");
    addEntry.mutate(entryName);
  };

  return <div className="pool-entry-creation">
    <form className="pool-add-entry" onSubmit={submit} aria-label={`Add entry to ${poolName}`}>
      <label className="field" htmlFor={id}>
        <span>Entry name</span>
        <input ref={input} id={id} aria-label={`Entry name for ${poolName}`} value={name}
          maxLength={160} readOnly={addEntry.isPending} placeholder="Main entry"
          aria-invalid={Boolean(validationError)} aria-describedby={`${id}-hint${error ? ` ${id}-error` : ""}`}
          onChange={(event) => { setName(event.target.value); setValidationError(""); addEntry.reset(); }} />
      </label>
      <button disabled={addEntry.isPending}><Plus size={14} aria-hidden="true" />{addEntry.isPending ? "Adding…" : "Add entry"}</button>
    </form>
    <p id={`${id}-hint`} className="pool-entry-hint">Leave blank for “Main entry”.</p>
    {error && <p id={`${id}-error`} className="error-panel" role="alert">{error}</p>}
    <p className="pool-form-status" role="status">{addEntry.isPending ? "Adding entry…" : addEntry.isSuccess ? `Entry “${addEntry.data.name}” added.` : ""}</p>
  </div>;
}
