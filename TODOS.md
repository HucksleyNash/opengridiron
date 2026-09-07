# TODOS

## Pools

### Enforce one game per entry/week in the database

**What:** Add a partial uniqueness constraint for non-null `(entry_id, week, game_id)` values in `pool_picks`.

**Why:** The weekly-card validator will prevent new duplicate-game rows, but the database should become the final integrity boundary after legacy repair debt is gone.

**Context:** The Pool Week Workspace release intentionally preserves legacy rows with null or duplicate game references and surfaces them as `needs_repair`. Adding this constraint in the same migration could fail deployment or force destructive guesses. Before implementing, query every card for duplicate non-null game references, confirm the unresolved count is zero, add the Alembic migration and model constraint, and retain the application validator for clear user-facing errors.

**Effort:** S
**Priority:** P2
**Depends on:** Zero unresolved `needs_repair` cards caused by duplicate non-null game references.

## Draft Suite

### Add a verified Yahoo API ranking transport

**What:** Add a Yahoo OAuth/API adapter that produces the same provider-neutral immutable ranking snapshot as the authenticated HTML ranking scraper.

**Why:** Structured API data would reduce markup fragility, improve typed provider errors, and potentially lower synchronization cost without changing Draft setup, forecasting, mock opponents, or replay.

**Pros:** Reuses the full ranking provenance, identity, freshness, 24-hour reuse, and finishability gates; makes the transport replaceable without another product or schema redesign.

**Cons:** Yahoo's public Fantasy Sports documentation does not currently guarantee that the required rank/ADP or `draft_analysis` fields are present for every football player/league response. Building against assumed fields would create a false-ready setup path.

**Context:** The Full Draft Suite rewrite intentionally uses authenticated Yahoo player-page parsing first. The HTML adapter normalizes external player identity, overall/position rank or ADP, retrieval time, source/version metadata, and coverage into `DraftRankingSnapshot`. Before implementing the API transport, capture real authorized responses, prove that they cover a finishable configured draft, and add them to the same contract/fixture matrix. The scraper remains the fallback until the API path passes parity and runtime rehearsal.

**Effort:** L
**Priority:** P2
**Depends on:** Approved Yahoo API access plus verified returned draft-ranking fields and a shipped provider-neutral ranking contract.

### Add keeper-draft format support

**What:** Add keeper declarations, retained-player costs, round forfeiture, player-pool removal, keeper-aware replacement value, simulations, recommendations, and replay.

**Why:** Keeper leagues change available-player supply and draft capital before pick one, so standard redraft logic would produce misleading rankings and draft-path advice.

**Context:** The Full Draft Suite plan intentionally limits its first release to standard snake redrafts while reserving versioned format-extension data. After that suite is stable, define keeper rules as a separate format contract rather than scattering optional keeper branches throughout redraft code. Cover round-cost, no-cost, traded-pick, duplicate-keeper, changed-keeper, and historical replay cases, and reuse canonical athletes plus immutable projection/ranking snapshots.

**Effort:** XL
**Priority:** P3
**Depends on:** Shipped and stable standard snake Draft Suite, stable athlete/projection identity contracts, and evidence of actual keeper-league demand.

### Add salary-cap draft format support

**What:** Add team budgets, nominations, winning bids, bid history, inflation-adjusted values, price-aware simulations, recommendations, and replay.

**Why:** Snake recommendations cannot answer whether a player is worth another dollar or how current spending changes future roster construction.

**Context:** The Full Draft Suite plan intentionally excludes salary-cap behavior from its first release. Reuse canonical athletes, immutable projections/rankings, evidence, and replay primitives, but define nomination, bid, budget, inflation, and completion rules as a separate versioned format contract. Do not force bid semantics into snake `pick_recorded` events or spread nullable auction branches through the standard draft reducer.

**Effort:** XL
**Priority:** P3
**Depends on:** Shipped and stable standard snake Draft Suite and evidence of actual salary-cap league demand.

## Leagues

### Refine the narrow current-week control

**What:** Give the shared Projection week select enough space, or shorten its current-week option further, so its numeric suffix stays visible at 390px.

**Why:** The working week is preserved correctly and appears in the report and Overview, but the select itself can still clip the end of its label.

**Priority:** P3
**Context:** Recorded after the bounded 2026-09-04 Impeccable confirmation pass. See `.impeccable/polish/league-and-forecast/verification.md`.

### Update legacy league browser-test expectations

**What:** Adapt `frontend/e2e/my-team.spec.ts` and `frontend/e2e/weekly-analysis.spec.ts` to shared URL-backed team/week controls, saved-default disclosure, and Evaluate/conditional waiver copy.

**Why:** These fixtures encode the intentionally replaced per-tab state and reload-reset behavior. Forty-two unit/render tests pass, including new coverage of the changed contract; the full legacy E2E suite is not claimed passing.

**Priority:** P2
**Depends on:** Authorization to modify existing tests, which the design-review skill excludes from its fix pass.

## Completed

- 2026-09-04: Fixed all five Impeccable priority findings for League Overview and Forecast, preserving DESIGN.md. Verified and installed locally; exact critique snapshots closed. See `.impeccable/polish/league-and-forecast/verification.md`.
