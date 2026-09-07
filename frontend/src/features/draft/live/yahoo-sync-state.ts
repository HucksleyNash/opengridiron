type SequencedSession = {
  current_sequence: number;
};

type YahooSyncOptions<TSession extends SequencedSession, TResult> = {
  loadSession: () => Promise<TSession>;
  sync: (expectedSequence: number) => Promise<TResult>;
  onSessionChange: (session: TSession) => void;
};

export async function syncYahooAtLatestSequence<TSession extends SequencedSession, TResult>({
  loadSession,
  sync,
  onSessionChange,
}: YahooSyncOptions<TSession, TResult>): Promise<TResult> {
  let latest = await loadSession();
  onSessionChange(latest);

  try {
    return await sync(latest.current_sequence);
  } catch (error) {
    if (
      typeof error !== "object"
      || error === null
      || !("code" in error)
      || error.code !== "draft_conflict"
    ) throw error;

    latest = await loadSession();
    onSessionChange(latest);
    return sync(latest.current_sequence);
  }
}
