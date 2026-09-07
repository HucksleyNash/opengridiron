import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive as ArchiveIcon, ArchiveRestore, ArrowRight, ClipboardList, Plus, Settings2, ShieldCheck, X } from "lucide-react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api } from "../../api";
import { resolveDraftLeague } from "../../draft-room-state";
import type { League } from "../../types";
import { draftApi, type DraftSessionInput } from "./api";
import { DraftCockpit } from "./live/DraftCockpit";
import { DraftReplay } from "./replay/DraftReplay";
import { DraftSetup } from "./setup/DraftSetup";
import type { DraftSession } from "./types";
import { DecisionAnalyst } from "../analysis/DecisionAnalyst";

export function DraftRoute() {
  const { leagueId } = useParams();
  const [searchParams] = useSearchParams();
  const requestedSessionId = Number(searchParams.get("session"));
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selectedSessionId, setSelectedSessionId] = useState<number>();
  const [creatingNew, setCreatingNew] = useState(false);
  const [managingSessions, setManagingSessions] = useState(false);
  const leaguesQuery = useQuery({ queryKey: ["leagues"], queryFn: () => api<League[]>("/leagues") });
  const diagnostics = useQuery({ queryKey: ["draft-diagnostics", selectedSessionId], queryFn: () => api<{ capabilities?: { ai_explanations?: boolean } }>(`/draft-sessions/${selectedSessionId}/diagnostics`), enabled: Boolean(selectedSessionId) });
  const league = resolveDraftLeague(leagueId, leaguesQuery.data || []);
  const sessionsQuery = useQuery({
    queryKey: ["draft-sessions-v2", league?.id],
    queryFn: () => draftApi.sessions(league?.id || 0),
    enabled: Boolean(league),
  });
  const sessions = sessionsQuery.data || [];
  const activeSessions = sessions.filter((item) => !item.archived_at);
  const archivedSessions = sessions.filter((item) => item.archived_at);

  useEffect(() => {
    if (!leagueId && league) navigate(`/draft/${league.id}`, { replace: true });
  }, [league, leagueId, navigate]);
  useEffect(() => {
    setSelectedSessionId(undefined);
    setCreatingNew(false);
  }, [league?.id]);
  useEffect(() => {
    if (!creatingNew && !selectedSessionId && activeSessions.length) {
      const preferred = activeSessions.find((session) => session.id === requestedSessionId)
        || activeSessions.find((session) => session.status !== "ABANDONED") || activeSessions[0];
      setSelectedSessionId(preferred.id);
    }
  }, [activeSessions, creatingNew, requestedSessionId, selectedSessionId]);

  const sessionQuery = useQuery({
    queryKey: ["draft-session-v2", selectedSessionId],
    queryFn: () => draftApi.session(selectedSessionId || 0),
    enabled: Boolean(selectedSessionId),
  });
  const setSession = (session: DraftSession) => {
    setSelectedSessionId(session.id);
    queryClient.setQueryData(["draft-session-v2", session.id], session);
  };
  const create = useMutation({
    mutationFn: (input: DraftSessionInput) => draftApi.createSession(league?.id || 0, input),
    onSuccess: async (session) => {
      setCreatingNew(false);
      setSession(session);
      await queryClient.invalidateQueries({ queryKey: ["draft-sessions-v2", league?.id] });
    },
  });
  const start = useMutation({
    mutationFn: () => draftApi.action(sessionQuery.data?.id || 0, "start", sessionQuery.data?.current_sequence || 0),
    onSuccess: async (result) => {
      setSession(result.session);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["draft-board-v2", result.session.id] }),
        queryClient.invalidateQueries({ queryKey: ["draft-recommendations-v2", result.session.id] }),
        queryClient.invalidateQueries({ queryKey: ["draft-sessions-v2", league?.id] }),
      ]);
    },
  });
  const rankingSync = useMutation({
    mutationFn: (refresh: boolean) => draftApi.syncRankings(
      league?.id || 0,
      sessionQuery.data?.id || 0,
      sessionQuery.data?.current_sequence || 0,
      refresh,
    ),
    onSuccess: async (result) => {
      setSession(result.session);
      await queryClient.invalidateQueries({ queryKey: ["draft-sessions-v2", league?.id] });
    },
  });
  const yahooTeamSync = useMutation({
    mutationFn: async () => {
      const leagueId = league?.id || 0;
      await draftApi.syncYahooLeague(leagueId);
      const refreshed = await api<League>(`/leagues/${leagueId}`);
      queryClient.setQueryData<League[]>(["leagues"], (current) =>
        (current || []).map((item) => item.id === refreshed.id ? refreshed : item),
      );
      const teamNames = (refreshed.team_names || []).map((name) => name.trim()).filter(Boolean);
      const orderVerified = refreshed.team_order_source === "yahoo_draft_order";
      const currentSession = creatingNew ? undefined : sessionQuery.data;
      let sessionUpdated = false;
      let ownerMatchRequired = false;
      if (orderVerified && currentSession && ["SETUP", "READY"].includes(currentSession.status)) {
        const ownerName = currentSession.teams.find((team) => team.is_owner)?.name.trim().toLowerCase();
        const ownerMatches = ownerName
          ? teamNames.map((name, index) => name.toLowerCase() === ownerName ? index + 1 : 0).filter(Boolean)
          : [];
        if (ownerMatches.length === 1) {
          const updated = await draftApi.updateSetup(currentSession.id, {
            expectedSequence: currentSession.current_sequence,
            teamCount: teamNames.length,
            roundCount: currentSession.round_count,
            ownerTeamSlot: ownerMatches[0],
            teamNames,
          });
          setSession(updated);
          sessionUpdated = true;
          await queryClient.invalidateQueries({ queryKey: ["draft-sessions-v2", leagueId] });
        } else {
          ownerMatchRequired = true;
        }
      }
      return { teamNames, orderVerified, sessionUpdated, ownerMatchRequired };
    },
  });
  const archive = useMutation({
    mutationFn: ({ item, archived }: { item: DraftSession; archived: boolean }) =>
      draftApi.archiveSession(item.id, item.current_sequence, archived),
    onSuccess: async (updated) => {
      queryClient.setQueryData(["draft-session-v2", updated.id], updated);
      if (updated.archived_at && selectedSessionId === updated.id) {
        const next = sessions.find((item) => item.id !== updated.id && !item.archived_at);
        setSelectedSessionId(next?.id);
        setCreatingNew(!next);
      }
      await queryClient.invalidateQueries({ queryKey: ["draft-sessions-v2", league?.id] });
    },
  });

  if (leaguesQuery.isLoading) return <div className="draft-route-loading"><span />Opening the Draft Suite…</div>;
  if (leaguesQuery.error) return <div className="error-panel">{leaguesQuery.error.message}</div>;
  const leagues = leaguesQuery.data || [];
  const picker = <label className="field draft-route-picker"><span>League</span><select aria-label="League" value={league?.id || ""} onChange={(event) => navigate(`/draft/${event.target.value}`)}><option value="" disabled>Select a league</option>{leagues.map((item) => <option value={item.id} key={item.id}>{item.name} · {item.season}</option>)}</select></label>;

  if (!leagues.length) return <>
    <header className="page-header"><div><span className="eyebrow">Prepare · Execute · Learn</span><h1>Draft Suite</h1></div></header>
    <section className="panel draft-empty"><ClipboardList size={30} /><h2>Create a fantasy league first.</h2><p>The Draft Suite needs league scoring, roster slots, and a player pool before it can give grounded advice.</p><Link className="button primary" to="/leagues">Add a league <ArrowRight size={15} /></Link></section>
  </>;
  if (!league) return <><header className="page-header"><div><span className="eyebrow">League not found</span><h1>Draft Suite</h1></div>{picker}</header><div className="error-panel">That league does not exist. Choose an available league.</div></>;

  const session = sessionQuery.data;
  const showSetup = creatingNew || !session || ["SETUP", "READY", "ABANDONED"].includes(session.status);
  return <>
    <header className="page-header draft-suite-header"><div><span className="eyebrow">Prepare · Execute · Learn</span><h1>Draft Suite</h1><p>Given who is gone, your league rules, roster, and next-turn uncertainty: who should you take and why?</p></div><div className="draft-header-actions">{picker}<span className="draft-readonly"><ShieldCheck size={14} /> Read-only advice</span></div></header>
    {sessions.length > 0 && <>
      <div className="draft-session-switcher"><span>Sessions</span>{activeSessions.map((item) => <button key={item.id} className={!creatingNew && item.id === session?.id ? "active" : ""} onClick={() => { setCreatingNew(false); setSelectedSessionId(item.id); }}>{item.kind === "mock" ? "Mock" : "Live"} · {item.status.toLowerCase()}</button>)}<button className={creatingNew ? "active" : ""} onClick={() => setCreatingNew(true)}><Plus size={14} /> New</button><button className={`draft-manage-toggle ${managingSessions ? "active" : ""}`} aria-expanded={managingSessions} onClick={() => setManagingSessions((value) => !value)}><Settings2 size={14} /> Manage{archivedSessions.length ? <b>{archivedSessions.length}</b> : null}</button></div>
      {managingSessions && <section className="panel draft-session-manager" aria-label="Manage draft sessions">
        <div className="draft-manager-heading"><div><span className="eyebrow">Draft cleanup</span><h2>Manage drafts</h2><p>Archive hides a room from the session strip without deleting its picks, recommendations, or replay.</p></div><button className="ghost" aria-label="Close draft manager" onClick={() => setManagingSessions(false)}><X size={16} /></button></div>
        <div className="draft-manager-list">{sessions.map((item) => {
          const isArchived = Boolean(item.archived_at);
          return <article key={item.id} className={isArchived ? "archived" : ""}>
            <span className="draft-manager-kind">{item.kind === "mock" ? "Mock" : "Live"}</span>
            <span><strong>{item.kind === "mock" ? "Mock draft" : "Live draft"}</strong><small>{item.status.toLowerCase()} · created {new Date(item.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}</small></span>
            {!isArchived && item.id === session?.id && !creatingNew ? <em>Open</em> : null}
            <button className="ghost" aria-label={`${isArchived ? "Restore" : "Archive"} draft ${item.id}`} disabled={archive.isPending} onClick={() => archive.mutate({ item, archived: !isArchived })}>{isArchived ? <><ArchiveRestore size={15} /> Restore</> : <><ArchiveIcon size={15} /> Archive</>}</button>
          </article>;
        })}</div>
        {archive.error && <div className="error-panel" role="alert">{archive.error.message}</div>}
      </section>}
    </>}
    {(sessionsQuery.isLoading || (selectedSessionId && sessionQuery.isLoading)) && <div className="draft-route-loading"><span />Restoring the latest session…</div>}
    {!sessionsQuery.isLoading && showSetup && <DraftSetup league={league} session={creatingNew || session?.status === "ABANDONED" ? undefined : session} pending={create.isPending || start.isPending} syncingRankings={rankingSync.isPending} syncingYahooTeams={yahooTeamSync.isPending} error={(create.error || start.error) as Error | null} rankingError={rankingSync.error as Error | null} yahooTeamError={yahooTeamSync.error as Error | null} onCreate={(input) => create.mutate(input)} onStart={() => start.mutate()} onSyncRankings={(refresh) => rankingSync.mutate(refresh)} onSyncYahooTeams={() => yahooTeamSync.mutateAsync()} />}
    {!creatingNew && session && ["LIVE", "PAUSED"].includes(session.status) && <DraftCockpit league={league} session={session} onSessionChange={setSession} />}
    {!creatingNew && session?.status === "COMPLETE" && <DraftReplay league={league} session={session} onSessionChange={setSession} />}
    {!creatingNew && session && diagnostics.data?.capabilities?.ai_explanations !== false && <DecisionAnalyst key={session.id} revision={session.current_sequence} context={{ league_id: league.id, draft_session_id: session.id }} question="Explain the best draft choices from this session's current board, scoring rules, owner roster, needs, replacement value and likely next-turn availability. Cite missing data and treat draft probabilities as estimates." />}
  </>;
}
