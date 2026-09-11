# Open Gridiron

Open Gridiron is an open-source, self-hosted progressive web app (PWA) for fantasy football, survivor/loser pools, confidence pools, NFL news monitoring, and evidence-grounded AI analysis. Each installation is designed for one owner managing multiple leagues and pool entries, with their data stored on their own server.

Licensed under [Apache 2.0](LICENSE). Start with Docker below, or see [Development](#development), [Contributing](#contributing), and [License and third-party sources](#license-and-third-party-sources).

The app is useful before Yahoo API access is approved: leagues, players, projections, games, pool rules, entries, and picks all have manual workflows. Yahoo data can be synchronized read-only through an authenticated browser-session scraper now and through OAuth after Yahoo approves an API client.

## Start locally

Requirements: Docker Desktop or Docker Engine with Compose.

```bash
git clone https://github.com/HucksleyNash/opengridiron.git
cd opengridiron
docker compose up --build
```

Open [http://localhost:8787](http://localhost:8787). The local profile publishes only on `127.0.0.1`; authentication is disabled only in this loopback profile. Complete the short owner setup, then create a manual league or pool.

Manual workflows and deterministic recommendations work without an AI account or Yahoo API approval. AI analysis is optional and requires a configured provider; hosted providers may charge for usage. For access from another device, use one of the authenticated [hosted profiles](#hosted-profiles).

Data, cached nflverse files, encrypted settings, browser subscriptions, and seven rolling backups live in the `football-data` Docker volume. The local encryption master secret is generated once inside that volume.

Open Gridiron was previously named Fourth Down. The Compose project names and encrypted
recovery format retain their original identifiers so existing installations keep their
data, Codex sign-in, and readable backups. Continue using your existing Compose commands;
no data migration or project-directory rename is required. Archived reports retain the
name recorded when they were generated.

## What works

- Weekly command center with alert, freshness, league, pool, and analysis status.
- Manual and Yahoo-backed leagues; authenticated Yahoo page scraping; CSV/JSON player and projection imports.
- Yahoo OAuth refresh and synchronization of settings, teams/rosters, available players, standings, scoreboards, draft results, and transactions into idempotent records and raw snapshots.
- nflverse schedule and roster ingestion with cached source files and identity mapping.
- NFL, ESPN and CBS NFL source registry, conditional HTTP requests, excerpts, classification, deduplication, in-app alerts, and optional Web Push.
- Free Sleeper player-status and Fantasy Football Calculator ADP evidence, with daily caching, source dates, matching by player identity and draft format, and visible fetch controls. See [sources, usage and limitations](docs/football-data-sources.md).
- Clickable roster and lineup players with a synopsis, projection context, official NFL injury entries, and recent player-specific articles. Reports load on demand without an API key; source dates, partial failures, and cached results are labeled.
- An Injury report workspace combines official NFL entries, supplemental Sleeper injury/practice fields, and imported availability tags. Filter by player or injury, NFL team, position, designation, league, or **My players** across the current season's leagues. Set **My team** in each league to include its roster.
- On-demand injury AI checks collect player coverage, save source citations, and assess playing availability, workload risk, and fantasy implications for a named upcoming game. Checks use an enabled provider from Settings, run in the background, and remain available after a reload. Missing, stale, or mismatched official evidence produces an unknown outlook; no injury probabilities are fabricated. Official reports are cached for ten minutes (manual refresh has a one-minute cooldown); the shared Sleeper status feed refreshes at most daily and does not supply per-field publication dates.
- Deterministic floor/balanced/ceiling lineup optimization, period-aware waiver comparisons, supported trade deltas, and a live draft board. FAAB bids are withheld without budget and winning-bid evidence.
- Winner or loser survivor pools, straight-up or ATS rules, saved pick receipts, team reuse enforcement, constrained season allocation and multiple-entry diversification.
- Preview, import, and refresh supported Sleeper survivor pools through its public API, including rules and owner entry matching; submit actual picks on Sleeper.
- Straight-up or ATS confidence pools with expected-points weight assignment.
- League-specific raw-stat scoring, conservative position uncertainty, projection imports, and manual/model/market game probabilities with no-vig helpers.
- OpenAI Responses, Anthropic Messages, OpenAI-compatible local endpoints, and an isolated Codex CLI provider, all validated against one JSON Schema.
- SQLite WAL, migrations, scheduled collection, durable job runs, SSE heartbeats, daily online backups, integrity-checked restore, and credential-free data export.
- Final-score ingestion, reproducible ATS grading, standings and survivor elimination. Market probabilities take priority over a validated historical team-strength fallback.
- Report-aware AI follow-ups, direct draft/pool explanations, editable exclusive task defaults, and optional AI news review.

## Weekly league analysis

Open a league and choose **My team** once. Open Gridiron saves a separate choice for
each league and defaults its roster, forecasts, and new draft setup to that team,
including after a reload or on another device. You can still inspect other teams
using **Fantasy team** without changing your saved choice. Change **My team** at
any time, or choose **Choose my team** to clear it.

Open a league under **Leagues**, then select its **Forecast** tab. The open league is
selected automatically. Choose your fantasy team, week, and AI provider, then select
**Run league analysis**. The app refreshes league,
schedule, statistics, and news sources in the background and saves a report you can
reopen after leaving the page. Missing providers or failed sources are explicit;
available statistical forecasts survive an analyst outage.

Reports include independent Open Gridiron weekly forecasts, eligible lineup changes,
one-week waiver add/drop alternatives, an evidence-grounded AI briefing, changes since
the previous run, source coverage, and forecast tracking. Yahoo's imported values stay
separate. Differences are shown only when source forecasts match the week and scoring.

The serving pipeline compares a recency-weighted baseline with learned usage and opponent
adjustments using prior training, calibration and held-out seasons. A candidate must pass
the accuracy and interval-coverage gates before serving. The first 2026 evaluation rejected
the adjustment model: baseline MAE **4.459746** versus candidate **4.472081** on 6,502 held-out
2025 appearances. The baseline stays active. See the [benchmark evidence and reproduction
steps](docs/benchmarks/forecast-remediation.md).

Supported D/ST histories use league scoring. A player without sufficient history can use
an exact-week Yahoo projection only as a labeled source fallback, with no invented range.
Unsupported positions, ambiguous scoring and missing identities remain unavailable.
Yahoo weekly snapshots are separate from season/draft values and must confirm the requested
season, week and scoring. Source collection failures or stale critical data withhold
actionable lineup changes. Specific drops require comparable rest-of-season costs.

League lineup comparisons rank only complete modeled lineups; they do not invent matchup
win odds or playoff probabilities. Forecast tracking scores eligible pre-kickoff predictions
against actuals and keeps independent source comparisons separate. No accuracy advantage
over Yahoo is claimed. **Sources, coverage, and forecast method** explains each report.

Use **Ask a follow-up about this report** to carry its exact saved evidence, team and week
into the analyst. Subsequent questions retain the conversation. **Start a new conversation**
clears that context. Draft context is selected explicitly.

One analysis runs per league at a time. Server restarts mark interrupted runs as failed
so they can be retried. Migration `0008` adds the report table without rewriting player
values. See the [implementation design](docs/designs/league-analysis.md).

## Pool decisions and results

To remove a pool, open **Pools → Pool settings**, then choose **Delete pool** below
that pool. Review its name and choose **Delete permanently**. This removes the local
pool, all its entries, and their saved picks. Deleting an imported pool does not affect
the original pool on Sleeper.

Under **Pool settings → Import from Sleeper**, paste a Sleeper league URL or ID.
Enter your Sleeper username to match your entries, then select **Preview pool** and
**Import pool**. No login or API key is required. Supported regular-season NFL survivor
pools import their name, season, weekly pick limit, team-use limit, commissioner,
participant/entry counts, and your entry IDs. Sleeper ties eliminate and picks lock at
kickoff. Capacity is shown separately from actual participants.

**Sleeper pool details → Refresh Sleeper details** updates the existing pool without
duplicating entries or overwriting local picks. Rules and season cannot change after
local picks are saved. Revives, spread scoring (which requires Sleeper's locked lines),
and unknown rule configurations are shown in the preview but cannot be imported.
Source status is reported as of the last refresh, separately from local grading.

**Sleeper picks and pick history are not imported.** Team-use checks and season plans
only include picks saved here; compare them with your Sleeper history. Submit actual picks on
Sleeper. See [import scope and verified sources](docs/designs/sleeper-pool-import.md).

Opening a pool week, returning to its browser tab, or leaving it visible for five
minutes checks the latest nflverse schedule, probabilities, lines and results plus
all enabled news and injury sources. **Refresh data** forces another check; automatic
checks within one minute share the last result. **Source freshness and coverage**
shows collection times and failures. Cached games and saved picks remain available
when collection fails. Manual inputs still need manual updates, and nflverse provides
its latest published odds rather than a live market feed.

Choose an analyst and select **Analyze picks** for a saved, evidence-grounded briefing
and proposed card. Review it, then choose **Set AI picks** to save it for the selected
entry in Open Gridiron. Analysis itself does not save picks. The server checks source
availability, complete confidence weights, team-use limits and kickoff locks. It also
rejects proposals older than five minutes or whose evidence or saved card changed.
Locked selections are preserved. Source failures allow a qualified briefing but
withhold AI pick saving until a successful refresh and new analysis.

Weekly cards show market probabilities when available. An Elo fallback can fill missing
straight-up prices only after beating a 50/50 baseline on the previous season; its 2025
Brier score was **0.22277**, behind market **0.21038** on the same 285 games. Elo is not an
ATS model and does not imply an edge over the market. The [pool benchmark](docs/benchmarks/pool-benchmark-2026.json)
records the source hash, holdout and coverage.

**Season plan and entry diversification** allocates all remaining regular-season weeks
subject to team-use limits, allowed teams, existing picks and locks. Missing schedule or
probabilities leave an explicitly partial plan. Multiple entries use an overlap penalty;
this is a diversification heuristic. Suggestions do not save or submit picks.

nflverse final scores settle saved picks. ATS uses the handicap saved when the pick was
accepted; later line changes cannot rewrite it. Old ATS picks without that receipt and
malformed legacy cards remain ungraded. Rule/season changes are blocked after picks exist.
Manual-source game results can be recorded with `PUT /api/v1/games/{id}/result`, providing
both scores; nflverse-managed results are refreshed from the source. Standings, elimination,
and the selected card feed **AI decision review** on the pool page.

## Draft Suite

The Draft page is a read-only second-screen decision cockpit. Its job is to answer: given who has
already been selected, this league's rules, the owner's roster, and likely next-turn availability,
who should the owner take and why? It never submits a pick to Yahoo.

Create a standard snake-redraft session from a league, confirm the team order and owner slot, and
start the room. During the draft, record picks manually or enable Yahoo in approval mode. After
validating the active feed, the owner can explicitly auto-approve clean sequential picks while every
unknown, conflicting, corrected, or out-of-order observation still waits for approval. The cockpit
keeps three recommendations prominent, explains value above replacement, roster fit, tier urgency,
risk, and any explicitly linked news evidence, and supports queueing, corrections, undo, pause,
resume, search, source conflicts, optional scenarios, and cross-league exposure context. Every
change is append-only and sequence checked, so a stale browser cannot silently overwrite the room.

After completion, Replay shows the advice that existed at each owner turn without recomputing it
from later results. Keeper and salary-cap formats are intentionally not supported yet. See the
[Draft Suite plan](docs/designs/draft-suite.md), [operator runbook](docs/draft-operations.md), and
[first-owner-use checklist](docs/test-plans/draft-suite-dogfood.md).

## Yahoo setup

### Authenticated scraper (no API key)

Open **Settings → Yahoo authenticated scraper** and enter each Yahoo Football league home URL on its own line. While signed in to Yahoo in your browser, use the browser developer tools Network panel to reload the league page, select the page request, and copy the value of its `Cookie` request header. Save that value in Open Gridiron, then select **Scrape now**.

The cookie is equivalent to a signed-in browser session. Open Gridiron encrypts it at rest, never returns it from the API, never includes it in snapshots, and uses it only for HTTPS requests to `football.fantasysports.yahoo.com`. Replace it after Yahoo expires or signs out the session. The scraper reads league settings and scoring, roster slots, team rosters, player ownership/status, and draft results. See [the authenticated scraper guide](docs/yahoo-scraper.md) for detailed steps and limitations.

### OAuth API (after approval)

Yahoo requires application review. Start with [the Yahoo application guide](docs/yahoo-application.md); the local callback is:

```text
http://localhost:8787/api/v1/integrations/yahoo/callback
```

Once approved, store the client ID, client secret, and exact callback under Settings, open the authorization URL from `/api/v1/integrations/yahoo/start`, and run synchronization from `/api/v1/integrations/yahoo/sync`. The application never submits a lineup, transaction, draft pick, or pool pick to Yahoo.

## AI providers

Add providers under Settings and choose task defaults independently for chat, recommendation explanation, and news classification.

- OpenAI uses `POST /v1/responses` with strict structured output.
- Anthropic uses Messages structured output.
- Ollama, LM Studio, and compatible servers use their OpenAI-compatible chat endpoint.
- Codex runs in the separate `codex-runner` container. It receives only a generated dossier and output schema, has no application-data mount, uses a read-only ephemeral workspace, and has no published port.

### Authenticate the Codex sidecar

Codex supports either API-key authentication or a ChatGPT login. Authentication is cached in
the persistent `codex-home` Docker volume, so recreating the container does not require another
login.

For unattended deployments, set `OPENAI_API_KEY` in `.env`, or enter an API key when creating the
Codex provider in Settings. A Codex provider without its own stored key automatically uses
`OPENAI_API_KEY`; the app sends the key only to the private runner, which logs the CLI in through
standard input before the first analysis.

To use ChatGPT subscription access instead, choose **Codex CLI sidecar** while adding a provider
in Settings, then select **Sign in with ChatGPT**. The app displays the one-time device code and
opens the ChatGPT verification page while it polls the private runner for completion.

The equivalent operator fallback is:

```bash
docker compose exec codex-runner codex login --device-auth
docker compose exec -T codex-runner codex login status
```

For a hosted profile, include its Compose file and environment file in both commands, for example:

```bash
docker compose -f compose.cloud.yaml --env-file .env exec codex-runner codex login --device-auth
```

Device-code login must be enabled in the ChatGPT account or workspace. Treat the `codex-home`
volume as a secret because it contains the cached Codex credentials. If the volume is deleted, the
sidecar must be authenticated again.

When adding an OpenAI or Anthropic provider, the model field starts as a dropdown. Entering an API
key and leaving the key field loads models automatically; **Load available models** can also use a
server environment key when the field is blank. The server calls that provider's fixed official
Models API and fills the dropdown with models available to the account. Manual model-ID entry
remains available. Keys are never returned to the browser or included in model-discovery errors.

No provider silently falls back to another. Every analyst question and structured result is saved for review in **AI analyst → Analysis history**. Failed or invalid model output is retained as a failed `AnalysisRun`; deterministic recommendations remain available.

## Import format

Upload CSV or JSON to `POST /api/v1/leagues/{league_id}/players/import`. CSV columns are:

```text
source_id,name,pro_team,position,status,ownership,rostered_by,current_slot,projected_points,floor,ceiling,ros_value,risk
```

`name`, `team`/`pro_team`, `position`, and `projection`/`projected_points` are the useful minimum. Imports are versioned as data snapshots and upsert by `source_id`.

## Development

Python 3.12 and Node 22 are recommended.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade 'pip>=26.2.1,<27'
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff format --check backend codex_runner
.venv/bin/ruff check backend codex_runner

PYTHONPATH=backend:. .venv/bin/python backend/tests/perf/draft_benchmark.py --json

cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm test
pnpm run build
pnpm exec playwright install chromium
pnpm exec playwright test analysis-context.spec.ts weekly-analysis.spec.ts pool-week.spec.ts draft-room.spec.ts sleeper-pools.spec.ts --project=chromium
cd ..
```

The browser command above matches the CI Chromium journeys. Use `pnpm test:e2e` from `frontend` to run the full browser suite, including tablet and mobile projects. Optional backend features can be installed with `.venv/bin/pip install -e '.[dev,ml,push]'`; the Docker image includes the forecasting and Web Push extras.

From the repository root, initialize or migrate the local database and run the API:

```bash
mkdir -p data
export DATA_DIR=./data DATABASE_URL=sqlite:///./data/football.db
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/uvicorn app.main:app --app-dir backend --reload --port 8000
```

Then run `pnpm run dev` in `frontend`; Vite proxies `/api` to port 8000.

## Contributing

Bug reports and pull requests are welcome at [GitHub](https://github.com/HucksleyNash/opengridiron). Include reproduction steps, expected behavior, and relevant logs with credentials and personal league data removed. Discuss substantial features in an issue before implementing them.

Use the development checks above, add regression coverage for behavior changes, and follow [DESIGN.md](DESIGN.md) for interface changes. Keep generated files and runtime data out of commits. Contributions are accepted under the project's [Apache 2.0 license](LICENSE).

## Repository contents

Version control contains application source, migrations, tests and synthetic fixtures,
dependency manifests and the frontend lockfile, deployment configuration, and maintained
documentation. The small benchmark records in `docs/benchmarks/` support the published
forecast comparisons.

Local databases and SQLite sidecars, environment files, recovery bundles, installed
dependencies, build output, browser test results, and generated agent reports are ignored.
Keep credentials in `.env` or the application's encrypted settings; `.env.example` contains
only placeholders. Local review evidence remains in ignored directories such as `.gstack/`,
`.impeccable/`, and `docs/review-artifacts/`.

Before committing, inspect `git status --short` and `git diff --cached --stat`. Do not force-add
ignored runtime files. See the [initial repository health audit](docs/repository-health.md)
for verification results and remaining maintenance work.

## Hosted profiles

Copy `.env.example` to `.env` and generate two independent secrets:

```bash
openssl rand -base64 48
openssl rand -base64 48
```

Use one as `APP_SECRET` and one as `CODEX_RUNNER_TOKEN`.

Public HTTPS:

```bash
docker compose -f compose.cloud.yaml --env-file .env up -d --build
```

Set `DOMAIN`, `TLS_EMAIL`, `APP_SECRET`, `CODEX_RUNNER_TOKEN`, and `OWNER_PASSWORD` before the first authenticated startup. Caddy obtains TLS certificates and is the only service with public ports. An existing owner does not require the bootstrap password to remain configured.

VPN/private interface:

```bash
docker compose -f compose.vpn.yaml --env-file .env up -d --build
```

Set `VPN_BIND_ADDRESS` to the exact private interface address and `PUBLIC_BASE_URL` to its private HTTPS URL (for example, a Tailscale HTTPS name). This profile has no public proxy.

The app listens on plain HTTP at that private address on port 8787. Configure a private HTTPS reverse proxy or Tailscale Serve to forward the chosen HTTPS URL to it; setting `PUBLIC_BASE_URL` alone does not provide TLS.

See [architecture and operations](docs/architecture.md) for data flow, security boundaries, backup restoration, and API groups.

See [refresh controls and full recovery](docs/operations-recovery.md) for scheduled Yahoo/news/nflverse collection, durable cooldowns, and an encrypted bundle containing the database, master secret, Codex home and deployment configuration. Rolling SQLite backups alone cannot recover every credential.

## Important model boundaries

Probabilities and fantasy scores are deterministic inputs to AI analysis. AI explains them, connects relevant attributed news, and identifies missing or stale data; it is not allowed to invent new odds, injuries, or projections. ATS performance should be judged only from time-ordered holdout results—there is no claim of a durable betting edge.

## License and third-party sources

Copyright 2026 HucksleyNash and contributors. Open Gridiron's original code and documentation are licensed under the [Apache License, Version 2.0](LICENSE); see [NOTICE](NOTICE) for project attribution. This permits personal and commercial use, modification, and redistribution under the license's terms, including its notice requirements and contributor patent grant. Modified versions may remain closed source.

Dependencies retain their own licenses. The project license does not grant rights to third-party football data, news excerpts, logos, trademarks, or provider services. Follow each source's terms and attribution requirements. In particular, [Sleeper's API](https://docs.sleeper.com/) is free for non-commercial use; commercial API use requires discussing licensing with Sleeper. See [football data sources](docs/football-data-sources.md) and the [Yahoo scraper guide](docs/yahoo-scraper.md) for integration details and limitations.
