# Draft Suite operations

The Draft Suite is a read-only companion to a real draft. It may ingest or reconcile observations,
but it must never submit a pick to Yahoo. Manual drafting and deterministic recommendations are the
core path; every other capability can fail independently.

## Capability controls

All flags are read at process start.

| Variable | Default | Effect when false |
|---|---:|---|
| `DRAFT_SUITE_ENABLED` | `true` | Removes Draft Suite API routes and hides the usable backend surface. |
| `DRAFT_YAHOO_ENABLED` | `true` | Rejects Yahoo sync; manual events continue. |
| `DRAFT_FORECAST_ENABLED` | `true` | Shows next-turn availability as unavailable; deterministic scores continue. |
| `DRAFT_SIMULATION_ENABLED` | `true` | Rejects scenario runs; deterministic scores continue. |
| `DRAFT_AI_EXPLANATIONS_ENABLED` | `false` | Keeps deterministic wording; no AI explanation work starts. |

After changing a switch, recreate or restart the application container. The diagnostics response
records the effective state for every capability.

## Before first live use

1. Create and complete a mock with the exact team count, roster slots, scoring, and draft position.
2. Confirm the imported projection snapshot, team order, owner slot, snake direction, and first
   three recommendation explanations.
3. Enable Yahoo approval mode. Verify that an exact observation confirms a manual pick and that a
   missing or conflicting observation creates a proposal rather than changing the board.
4. Exercise correction, undo, pause/resume, browser reload, and manual fallback.
5. Download or inspect `/api/v1/draft-sessions/{session_id}/diagnostics` and confirm it contains no
   credentials, cookies, raw provider payloads, notes, or news prose.
6. Complete the [owner-use checklist](test-plans/draft-suite-dogfood.md). During an attended draft,
   the owner may explicitly promote a validated approval feed to auto-approve safe sequential
   picks. A persisted, passing 50-pick rehearsal remains required for evidence-based promotion
   without that live owner confirmation.

## Yahoo approval and auto-approve workflow

Live sessions still start in manual mode. Selecting **Use scraper with approval** starts visible-tab
polling but keeps every missing or conflicting Yahoo observation behind **Accept Yahoo**. After the
owner has compared enough observations to trust the active feed, **Enable auto-approve** requires a
second explicit confirmation and changes only that session to authoritative mode.

Authoritative mode applies only the next missing pick when the player maps to the league and Yahoo's
team slot matches the expected snake slot. Unknown players, gaps, corrections, conflicts, and picks
beyond the configured board remain proposals. **Require approval** immediately downgrades the same
session without disabling polling; **Manual only** disables Yahoo observation entirely. Every mode
change and applied pick remains append-only and sequence checked.

## Automatic mock workflow

Automatic opponents are available only for mock sessions. During setup, choose **Automatic
opponents**. The room then reuses a complete Yahoo ranking snapshot from the last 24 hours or syncs
the most recent Yahoo scrape. **Start draft** remains disabled until the ranked, projected player
pool can finish the configured number of rounds and has enough players at each required position.

After start, the visible browser asks the server for one opponent pick at a time. Selection is
deterministic and accounts for Yahoo rank, the drafting team's roster needs, flex needs, depth, and
projections. It stops on the owner's turn. A stalled room can be resumed with **Pick now**; the
request is idempotent, so retries and competing tabs cannot create duplicate picks. Manual opponent
mode remains available for reproducing real drafts. Yahoo observation controls are intentionally
disabled in automatic mocks.

Automatic mock pick events record the selector version and inputs used. Changing selector policy
must introduce a new version rather than altering the meaning of historical events.

## Durable background work

Draft input previews are database-backed, expire after one hour, and can be committed only once;
repeating a successful commit returns the original result. This allows a browser refresh or process
restart between staging and committing without relying on process memory.

Scenario simulations are queued as durable computation runs. The UI polls the run until it becomes
ready, stale, timed out, failed, or interrupted. A result is published only if the session sequence
still matches the sequence it simulated. A process restart marks any queued or running work as
interrupted instead of leaving it permanently in progress; the user can safely submit it again.

## Incident response

### Yahoo is late, signed out, rate limited, or malformed

- Set `DRAFT_YAHOO_ENABLED=false` if retries are distracting or unsafe, then restart.
- Continue recording picks manually. Do not accept unresolved proposals merely to catch up.
- Re-enable Yahoo in shadow mode after the source is healthy and reconcile observations in order.

### The browser reports a stale sequence

- Reload the board; the server's event sequence is canonical.
- Confirm the highlighted intended player, then retry against the refreshed sequence.
- If conflicts repeat, pause the room and inspect diagnostics before appending more events.

### Recommendations are unavailable

- Confirm the session has a ready immutable projection snapshot and is live or paused.
- Check diagnostics for the projection snapshot ID, current sequence, input hash, and capability
  state. Forecast or simulation failure must not remove deterministic recommendations.
- Do not replace the snapshot mid-draft to make an error disappear; correct the input before start
  or create a new explicit session/generation.

### SQLite is busy or integrity is uncertain

- Pause draft mutations and take an online backup through Settings.
- Run the existing database integrity check/restore workflow before resuming.
- Never delete or rewrite Draft events to repair the board. Apply a correction/reversal through the
  normal API after integrity is established.

### Emergency rollback

1. Set `DRAFT_SUITE_ENABLED=false` and restart the application.
2. Preserve all Draft tables and snapshots. Do not run the migration downgrade during an incident.
3. Export diagnostics and restore a verified backup only if database integrity failed.
4. Re-enable the suite only after tests, event reduction, and a disposable-session smoke check pass.

## Verification commands

```bash
.venv/bin/pytest
.venv/bin/ruff format --check backend codex_runner
.venv/bin/ruff check backend codex_runner
PYTHONPATH=backend:. .venv/bin/python backend/tests/perf/draft_benchmark.py --json

cd frontend
pnpm test
pnpm run build
pnpm exec playwright test e2e/draft-room.spec.ts
```

The reference board benchmark uses 500 players and 480 events and fails at 150 ms p95. Browser
tests start the backend with `APP_ENV=test`, which is the only environment that exposes fixture
controls.
