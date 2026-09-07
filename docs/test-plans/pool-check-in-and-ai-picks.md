# Pool check-in and AI picks

Implemented September 5, 2026.

## Behavior and data flow

```text
Open / return to pool / visible five-minute interval
  -> POST check-in -> shared source lease and 60-second reuse window
  -> nflverse schedule, odds, model fallback and results; enabled news/injury feeds
  -> persist source statuses -> refresh card, overview, standings and strategy
                              (defer card reload while edits are unsaved)

Analyze picks -> refresh -> freeze pool/entry/week evidence -> configured AI
  -> schema + card-rule validation -> saved analysis and proposed card

Set AI picks -> verify scope and five-minute age -> refresh and compare evidence
  -> revalidate complete card, weights, team usage and locks
  -> existing version-checked atomic save with ATS receipt
```

Existing schedule ingestion, source collectors, provider adapters, analysis history,
season strategy, card validation and versioned saving are reused. No database migration
or additional external data integration is required. The provider output schema is
selected per request, preserving the existing general-analysis contract.

## Failure and freshness rules

- Collection failures are saved as partial coverage; cached games and picks survive.
- An empty schedule response is not treated as a current season.
- A shared process lease prevents duplicate pool refreshes. Waiting clients retry the
  status after two seconds; normal visible checks run every five minutes.
- No AI pick application after partial collection, absent probability evidence,
  invalid output, changed evidence/card, expired analysis, or a different entry/week.
- Proposals preserve locked selections, including confidence weights. Server lock
  validation also prevents replacing an open survivor slot with an already-started game.
- Autosave preserves edits queued behind an in-flight save and ignores responses from
  a previous card. Source reloads wait for pending saves to finish.
- Freshness means the latest published release was checked. nflverse is not a live
  odds feed, manual inputs require manual updates, and article publication dates remain
  visible to the analyst. Missing or old evidence must not become invented probabilities.

## Verification

Backend integration tests: `backend/tests/test_pool_analysis.py` covers source checks,
cooldown, forced retry, changed source configuration, collection/provider failures,
concurrent refresh status, empty seasons, winner/loser/ATS/confidence applications,
preview without mutation, lock preservation, missing probabilities, invalid output,
scope isolation, card/evidence changes, and expiry including expiry during collection.

Browser tests: `frontend/e2e/pool-week.spec.ts` covers open/return/manual source checks,
AI previews and saving for survivor and confidence entries, failed-source handling,
the existing manual/conflict flows, and a source refresh during queued autosave.
All run on desktop, tablet and mobile. Desktop/mobile screenshots of the proposal
were inspected against DESIGN.md; the existing typography, controls and structural
rules are retained. The mobile test verifies no horizontal page overflow.

External source and AI responses are controlled fixtures in these tests. Live provider
quality and upstream data publication latency are not established by these checks.

## Operational learning

The local checkout has no Git metadata. Its frontend scripts also require the bundled
Node directory on PATH in this Codex environment. Browser tests need permission to bind
temporary loopback servers; their database is separate from application data.
