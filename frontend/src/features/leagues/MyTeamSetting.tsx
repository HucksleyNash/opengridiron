import { useMutation, useQueryClient } from "@tanstack/react-query";
import { put } from "../../api";
import type { League } from "../../types";

export function MyTeamSetting({ league, rosterTeams, loading, error, onSaved }: {
  league: League;
  rosterTeams: string[];
  loading: boolean;
  error: boolean;
  onSaved: () => void;
}) {
  const queryClient = useQueryClient();
  const teams = [...new Set([...(league.team_names || []), ...rosterTeams])].sort();
  const save = useMutation({
    mutationFn: (name: string) => put<League>(`/leagues/${league.id}/my-team`, { my_team_name: name || null }),
    onSuccess: (saved) => {
      queryClient.setQueryData(["league", league.id], saved);
      queryClient.setQueryData<League[]>(["leagues"], (previous) => previous?.map((item) => item.id === saved.id ? saved : item));
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      onSaved();
    },
  });
  const unavailable = Boolean(league.my_team_name && !teams.includes(league.my_team_name));
  return <div className="league-my-team">
    <label className="field"><span>My team</span><select aria-describedby="my-team-help" value={save.isPending ? save.variables : league.my_team_name || ""}
      disabled={save.isPending || (!teams.length && !league.my_team_name)} onChange={(event) => save.mutate(event.target.value)}>
      <option value="">{loading && !teams.length ? "Loading teams…" : error && !teams.length ? "Teams unavailable" : "Choose my team"}</option>
      {unavailable && <option value={league.my_team_name!}>{league.my_team_name} (unavailable)</option>}
      {teams.map((team) => <option key={team} value={team}>{team}</option>)}
    </select></label>
    <div className="league-my-team-copy">
      <p id="my-team-help">{!loading && !error && unavailable ? "Your saved team is no longer in the imported teams. Sync the roster or choose your team again."
        : !loading && !error && !teams.length ? "Import a roster or connect Yahoo to choose your team."
          : "Saved for this league. Your roster, forecasts, and new drafts default to this team."}</p>
      <p role="status">{save.isPending ? "Saving…" : save.isSuccess ? league.my_team_name ? `Saved: ${league.my_team_name}` : "My team cleared." : ""}</p>
      {save.error && <p className="league-warning" role="alert">Could not save your team. {save.error.message} <button className="ghost" type="button" onClick={() => save.mutate(save.variables!)}>Retry</button></p>}
    </div>
  </div>;
}
