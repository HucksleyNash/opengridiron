# Weekly forecasting remediation evidence

The production forecast now runs the same selection pipeline as the offline benchmark. The first real benchmark **rejected the usage/matchup candidate**: its 2025 mean absolute error was 4.472081 versus 4.459746 for the recency baseline, on the identical 6,502 appearances. The baseline therefore remains in service. This is evidence of a working evaluation/admission gate, not evidence of an advantage over Yahoo.

## Reproduce

Download the six public source CSVs listed with URLs and SHA256 hashes in [forecast-benchmark-2026.json](forecast-benchmark-2026.json) into a temporary directory named player-2023.csv through player-2025.csv and team-2023.csv through team-2025.csv. Then run from the repository root:

```sh
PYTHONPATH=backend .venv/bin/python -m app.services.forecast_benchmark --input-dir /tmp/fourth-down-benchmark-inputs --season 2026 --output /tmp/forecast-benchmark.json
```

The command does not access the network, application database, Yahoo credentials, or AI providers. `--scoring /path/to/rules.json` evaluates the same league-scoring contract as serving. No new ML dependency is required.

## Split and features

- Training: 2023, 4,477 supported appearances.
- Calibration/model penalty selection: 2024, 6,271 appearances.
- Untouched final evaluation: 2025, 6,502 appearances.
- Features: prior-three-game production change, usage change expressed in historical points per opportunity, and prior opponent positional points allowed versus the league. Every feature excludes the predicted week and all later weeks.
- The residual correction uses standardized ridge regression with penalty chosen from a fixed 10/100/1000 grid on calibration data. The exact admitted fit is used by serving; no unrelated training endpoint is involved.
- Admission requires lower paired holdout MAE, at least 70% realized coverage for an 80% interval, and a separate per-position improvement gate with at least 100 observations. A failed candidate cannot silently become production.

The baseline's calibration interval covered 79.98% of the final holdout; the rejected candidate covered 80.07%. This is marginal historical calibration, not a guarantee for a particular player or changed role.

| Position | Held-out appearances | Baseline MAE | Candidate MAE | Candidate interval coverage |
|---|---:|---:|---:|---:|
| DEF | 459 | 4.3938 | 4.3453 | 84.3% |
| K | 513 | 3.8770 | 3.8754 | 85.6% |
| QB | 600 | 6.7957 | 6.7858 | 58.5% |
| RB | 1431 | 4.4980 | 4.5111 | 79.7% |
| TE | 1187 | 3.6684 | 3.6926 | 86.2% |
| WR | 2312 | 4.3786 | 4.4053 | 80.7% |

## Decision correctness

All roster engines share Yahoo availability normalization, including INACTIVE, PUP-R, NFI variants, IR-Return, and reserve-slot restrictions. Unavailable players do not generate upgrade recommendations. Conditional statuses retain explicit uncertainty without invented injury probabilities.

Waivers compare matching periods/scoring and can calculate selected-roster weekly gain. Unknown periods produce review rows, never add verdicts. No invented FAAB range is returned. A specific drop is withheld when comparable bench rest-of-season costs are missing; the report exposes the legal bench alternatives and separates weekly gain from drop cost. Trades return unavailable values for missing ROS or incompatible periods; an explicitly supplied zero remains zero.

Yahoo weekly comparisons are stored separately from season/draft projections. They require an explicitly selected `S_PW_<week>` table, the requested season, exact player identity and matching league scoring. A six-hour cache and shared import lock/rate-limit policy limit requests. The bounded top-50-per-position coverage is stated. A failed verification leaves the source comparison unavailable. This path has fixture verification, not a live Yahoo-account verification during remediation.

A rookie can use a matching weekly source projection only as a labeled source fallback, with no fabricated floor/ceiling and no self-comparison counted as independent accuracy. Source fallback rows are excluded from model outcome evaluation.

D/ST forecasts now ingest nflverse team histories and score supported league categories. Ambiguous fumble-return, blocked-kick/return or points-allowed aggregates are omitted instead of assigned guessed scoring. Unsupported scoring and IDP still remain explicitly unavailable. Data coverage is not equivalent to accuracy validation.

Stale/failed Yahoo roster, schedule, identity, or required current-statistics sources withhold actionable roster swaps and waivers while preserving forecasts for inspection. Saved reports retain those reasons, source timestamps and hashes, the model evaluation, and independent outcome interval coverage.
