# Weekly league analysis

Approved direction: calculate a separate Open Gridiron weekly forecast and publish a saved,
team-specific analysis in each league’s Forecast tab. The open league supplies the
analysis context automatically. Yahoo imports remain untouched.

## Data flow

```text
Open league → Forecast → select fantasy team / week / analyst
  -> durable analysis job -> refresh sources -> freeze inputs
  -> independent weekly statistical baseline -> legal lineup / add-drop evaluation
  -> evidence-only AI explanation -> saved league report
```

Reuse the existing Yahoo transport, NFLverse cached downloads, scoring normalization,
provider adapters and output schema, authentication, and React Query. New forecast data
lives in analysis reports, never in Player.projected_points.

The initial model is an explicitly experimental recency-weighted historical baseline.
It uses only regular-season games before the forecast week, recomputed under the league's
scoring. It does not manufacture rookie history, unsupported scoring, injury adjustments,
or calibrated probabilities. Imported season totals are not weekly comparison values.
Reports retain model version, input fingerprint, sources, coverage, timestamps, and forecasts
for subsequent evaluation. Weekly comparable imported forecasts are retained separately.

## Implementation tasks

- T1: Add immutable weekly forecasts, stable identity matching, coverage and scoring checks.
- T2: Optimize the selected roster with kickoff locks; evaluate actual add/drop improvements.
- T3: Add persistent background runs, refresh/error handling, provider explanation, and history.
- T4: Add league Forecast controls, forecast comparisons, actions, evidence, changes, and saved runs.
- T5: Verify time cutoffs, unchanged imports, team isolation, locks, source/provider failure,
  history, restart recovery, and responsive league behavior.

## Engineering decisions

- Architecture: an independent report avoids overwriting the draft and legacy league engines.
- Code quality: pure forecast/decision functions are separate from network and job state.
- Performance: cache public inputs; only evaluate the top five available players per eligibility
  group, sufficient for the five best immediate upgrades; keep model contexts bounded.
- Reliability: a partial report survives source or analyst failure; interrupted runs become
  failed after restart. One active job per league prevents duplicate refreshes and model calls.
- Tests: pytest covers model and persisted API flows; Playwright covers league Forecast controls,
  polling, history, explicit partial/error states, and mobile layout.

## Boundaries

Automatic Yahoo transactions, subscriptions to new data vendors, and unvalidated learned
matchup/injury coefficients are outside this change. The user executes recommendations on
Yahoo. The statistical baseline must not claim an advantage over Yahoo before evaluation.
No shared code changes require parallel worktrees; implementation is sequential.
