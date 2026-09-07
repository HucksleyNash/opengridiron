# Architecture and operations

## Runtime boundary

```text
Browser PWA ──HTTPS/loopback──> FastAPI + scheduler ──> SQLite WAL / data volume
                                      │
                                      ├──read-only sources──> Yahoo API/pages / nflverse / NFL
                                      ├──explicit call───> OpenAI / Anthropic / local endpoint
                                      └──dossier only────> isolated Codex runner
```

FastAPI serves `/api/v1` and the compiled React application from one process. One in-process APScheduler instance is appropriate because each deployment intentionally runs one application process. Every scheduled operation creates a durable `JobRun`.

The Codex runner has its own persistent authentication volume but no application-data volume. It
can be authenticated with an API key sent over the private Compose network on an explicit Codex
request, or with a device login initiated from Settings and proxied through the application. The
runner exposes the one-time code only over its token-authenticated private API and stores the final
login in `codex-home`. Requests require an internal bearer token. Every invocation gets a temporary
workspace containing only `dossier.json` and `output-schema.json`; Codex uses
`--sandbox read-only`, `--ephemeral`, and a strict output schema. See the README's **Authenticate the Codex
sidecar** section for the UI and operator workflows.

OpenAI and Anthropic model discovery is server-side. Provider type selects a fixed HTTPS API host;
the browser cannot supply a discovery URL. The response contains model IDs only, never the API key.

## Normalized records

- `DataSnapshot` records source identity, retrieval/effective time, freshness, status, and raw snapshot metadata.
- League/player/draft records are provider-neutral while preserving Yahoo keys.
- Games store win and cover probabilities plus market line attribution and source time.
- Pools own rules; entries own picks so reuse constraints are entry-specific.
- News stores canonical link, short excerpt, attribution, classification, and content hash—never full articles.
- `IdentityMap` connects Yahoo and GSIS/nflverse identities with confidence and manual verification.
- `AnalysisRun` retains provider/model, prompt/schema versions, input hash, snapshot references, validated output, tokens, and failure status.

## Draft Suite data flow

```text
immutable projections/rankings + league rules + canonical athletes
                              │
manual pick ──┐               v
Yahoo shadow ─┼──> reconcile/propose ──> append-only DraftEvent log
correction ───┘                         │
                                       v
                              deterministic reducer
                                  │          │
                                  v          v
                           current board   immutable advice snapshot
                                                  │
                                                  v
                                          no-hindsight replay
```

`DraftSession.current_sequence` is the optimistic-concurrency boundary. Mutations include the
sequence they were based on and receive a typed conflict if another event won. Picks are never
updated in place: reversal and replacement events supersede earlier facts, and the reducer derives
the current board. Owner picks require the recommendation snapshot that was current for that
sequence, preserving what the owner actually saw.

Projection and ranking imports become immutable snapshots. `Athlete` and `AthleteAlias` provide
provider-neutral identity; name similarity alone never merges players or attaches news. Yahoo is
manual-first; enabling it starts approval mode, where exact observations confirm existing facts and
missing, conflicting, or unknown observations become owner-visible proposals. Authoritative ingestion is
gated by explicit owner confirmation after approval-mode validation, or by persisted evidence from a
passing rehearsal of at least 50 consecutive picks. Yahoo remains read-only in every mode.

Deterministic advice is the required path. Forecasts, scenario simulation, news evidence, and AI
wording are optional enhancements with independent capability switches and typed unavailable
states. Cross-league exposure is informational and never changes a candidate score.

## API groups

Interactive documentation is at `/docs`. Main groups are:

- `/api/v1/onboarding`, `/auth`, `/system`
- `/integrations/yahoo` (OAuth API and authenticated scraper), `/sync/nflverse`
- `/leagues`, `/players`, `/draft`, `/projections`
- `/leagues/{league_id}/draft-sessions`, `/draft-sessions/{session_id}` for setup, events, board,
  recommendations, Yahoo reconciliation, scenarios, replay, exposure, and redacted diagnostics
- `/games`, `/pools`, `/entries`
- `/news`, `/alerts`, `/identity`
- `/providers`, `/analysis`
- `/notifications`, `/backups`, `/exports`, `/events`

All mutating authenticated requests require the double-submit CSRF token. Cloud mode adds secure cookies, HSTS, and per-client rate limits; Caddy adds HTTPS and public ingress.

## Freshness and degraded operation

Source responses are appended as snapshots instead of replacing historical evidence. The dashboard shows the most recent source time/status. Manual data remains available during Yahoo, news, odds, or AI outages. Provider failures become failed analysis runs and never trigger silent fallback.

## Backups and restore

SQLite `VACUUM INTO` from a read-only connection creates a consistent, compact copy daily at 03:15 in the configured timezone. Unused pages are removed from the copy without modifying the source. Seven copies are retained. Create or download one under Settings or the `/backups` endpoints. Full credential recovery additionally needs the master secret, deployment configuration and Codex home; see [operations and recovery](operations-recovery.md).

Restore with `POST /api/v1/backups/{filename}/restore`. The service validates `PRAGMA integrity_check`, creates a pre-restore safety backup, then uses SQLite’s backup API to replace the live database. Restart the app after a restore so every long-lived view is refreshed.

`GET /api/v1/exports/user-data` returns leagues, players, draft picks, games, pools, entries, and picks. It intentionally excludes API credentials, OAuth tokens, owner password hashes, session tokens, and Web Push endpoints.

## Web Push

Set `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, and `VAPID_CLAIMS_EMAIL`, then enable alerts from Settings on each browser. New or materially changed injury/transaction items are deduplicated before notification. A local deployment can notify only while its server is running.

## Data-source schedules

- Official news sources: every 15 minutes, with ETag and Last-Modified caching; optional AI review requires an explicitly configured news task default.
- Configured Yahoo leagues: every 60 minutes, with shared pacing, import locks and durable rate-limit cooldowns.
- nflverse schedule/results: hourly, or every 15 minutes around saved kickoffs. Identity rosters refresh daily.
- Backup: daily at 03:15.

Manual endpoints can trigger each sync immediately. Source terms, robots directives, rate limits, and caching headers remain authoritative.

## Draft rollback boundary

`DRAFT_SUITE_ENABLED=false` removes the Draft Suite router at process start. Yahoo polling,
forecasting, simulation, and AI explanations can be isolated independently without disabling the
manual room. Schema changes are additive and legacy draft rows are retained, so rollback means
disabling the suite and preserving its event/snapshot history—not deleting tables or attempting a
destructive downgrade. Detailed switches and incident checks are in
[draft operations](draft-operations.md).
