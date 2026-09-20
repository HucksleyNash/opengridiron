# Live Game Pulse score refresh deployment — lil-t-money

Status: **DEPLOYED AND VERIFIED**, September 20, 2026, 2:07 PM America/Chicago.

- URL: https://lil-t-money.tail519a34.ts.net:8443/
- Commit: `96fac18da9acb521cbd0adc39553929bc5ad7a43`.
- Release: `/home/thomaswkleckner/projects/opengridiron/releases/20260920T190245Z-live-scores-96fac18`.
- Image: `opengridiron:live-scores-96fac18`.
- Image ID: `sha256:4b2210ceb6923ac32f3d9fd15b5d57f8de238d1a2fb527f23df5d1edb9522d04`.
- Previous release: `/home/thomaswkleckner/projects/opengridiron/releases/20260920T173015Z-player-points-d8d1912`.
- Previous image: `opengridiron:player-points-d8d1912`.

## Scope and readiness

Refresh Sources now refreshes the nflverse schedule, requests ESPN's current NFL scoreboard, persists matched in-progress or final scores, and invalidates the Game Pulse query so the card updates immediately. Pregame 0–0 values, malformed records, and unmatched games are ignored. Existing cached scores remain available if the live source fails.

The release overlays the two changed backend runtime files and tested compiled frontend on the exact verified running image. Dependencies and database schema are unchanged. Before packaging, 434 backend tests, 47 frontend tests, the frontend production build, nine Command Center browser tests, targeted Ruff checks, and the Git diff check passed.

## Candidate and live verification

- Production-host access to ESPN's 2026 Week 2 scoreboard returned 16 events.
- An isolated candidate container passed `/healthz`, served the compiled frontend, and completed a real live-score sync against a temporary database.
- The deployed app is healthy and runs the expected candidate image ID.
- Private HTTPS returned HTTP 200 with certificate validation enabled; the frontend shell contains Open Gridiron.
- Anonymous access to the protected system-health and live-score sync APIs returns HTTP 401.
- The Codex runner retained the exact same container ID and remained healthy.
- Post-activation SQLite `quick_check` returned `ok`; 272 games, 17 scored games, and 1,441 snapshots were unchanged by activation.
- No queued, pending, or running analysis, draft-computation, source, or league-analysis jobs were present.
- The post-activation server error scan found no `Traceback`, `ERROR`, or `CRITICAL` entries.

## Backup and rollback

Verified online SQLite backup: `/data/deploy-backups/20260920T190245Z-live-scores-96fac18-predeploy/football.db` (969,519,104 bytes). SQLite `quick_check` returned `ok`.

To roll back, use the previous release's existing Compose files and protected `.env`: `docker compose -f compose.vpn.yaml -f compose.lil-t-money.yaml --env-file .env up -d --no-deps --no-build --pull never --wait app`. Verify `/healthz`, then atomically repoint `current` to the previous release. No schema rollback or database restore is required.

