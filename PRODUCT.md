# Product — Open Gridiron

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is one owner managing multiple fantasy football leagues and pool entries. Open Gridiron is their private decision workstation, not a public fantasy platform or an automated account operator.

## Product Purpose

Help the owner make informed football decisions by bringing league context, roster choices, weekly forecasts, pool information, and source-backed NFL analysis into one place. Success means understanding the recommendation, its supporting evidence, and its uncertainty well enough to decide what to do.

## Operating Context

- A private, self-hosted responsive web application and installable PWA, as documented in `README.md`.
- The owner works across multiple leagues and entries, inspects players and rosters, compares lineup and waiver options, and consults weekly forecasts and NFL news.
- The league workspace and its Forecast tab are related decision surfaces. Team, league, and forecast-period context must remain clear across them.
- The owner makes any external lineup, waiver, trade, draft, or pool submission themselves.

## Capabilities and Constraints

The owner confirmed the private, multi-league/pool purpose, source-backed recommendations, explicit forecast uncertainty, and absence of automatic Yahoo submissions. The following existing capabilities and limitations are documented in `README.md`:

- Manual entry and CSV/JSON imports support use alongside Yahoo-backed league data.
- Yahoo integrations read data; the application does not submit lineups, transactions, draft picks, or pool picks to Yahoo.
- League tools support roster inspection, lineup optimization, waiver comparisons, and saved weekly analysis reports.
- Imported Yahoo values, independent Open Gridiron forecasts, and AI explanations are distinct. Do not present them as interchangeable or compare incompatible periods or scoring contexts.
- The initial weekly forecast model is experimental. Historical ranges are descriptive, not calibrated probabilities; no superiority over Yahoo is established.
- Missing inputs, unsupported cases, stale or unavailable source context, and provider failures must not be disguised as verified results.
- Draft and pool decision workflows also belong to the product; a league-page refinement does not authorize changing them.

## Brand Commitments

- The product name is Open Gridiron.
- Use `opengridiron` for package and machine-readable brand identifiers, and `OG` for the compact brand mark.
- The existing `DESIGN.md` is the binding visual authority, as required by `AGENTS.md`. Product initialization does not replace or extend that design system.
- Recommendations should be precise about evidence and limitations, without invented certainty or performance claims.

## Evidence on Hand

- `README.md`: operating model, documented capabilities, forecast limitations, and integration boundaries.
- `DESIGN.md`: approved identity and interface constraints.
- `docs/designs/league-analysis.md`: weekly-analysis implementation design.
- `frontend/src/features/leagues/`: existing league-workspace implementation.
- Local review targets: `/leagues/1` and `/leagues/1?tab=forecast`.

These references describe the existing product; this record is not an independent validation of forecast accuracy or every documented feature.

## Product Principles

1. Keep the owner in control: advise and explain; do not execute external football decisions.
2. Make recommendations traceable to the available evidence and relevant league context.
3. Distinguish source data, modeled forecasts, and AI interpretation.
4. Expose uncertainty and missing information where they affect the decision.
5. Support the owner's multi-league workflow without losing the current team or period context.

## Open Decisions

No additional audiences, public/commercial positioning, forecast-accuracy promises, or product-specific accessibility certification were established during initialization. Future work must not assume them.
