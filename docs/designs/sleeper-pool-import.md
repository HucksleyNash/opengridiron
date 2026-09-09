# Sleeper survivor pool import

## Intended outcome

An owner can paste a Sleeper league URL into Pool settings, preview its rules and
membership, and import it into the existing pool workspace. An optional username
identifies the owner's entries. Refresh updates the same pool instead of duplicating it.

## Verified source evidence, 2026-09-08

Read-only requests to `https://api.sleeper.app/v1` verified an owner-supplied
NFL survivor league for regular season 2026, week 1. League and participant
identifiers are omitted from these public notes.
Settings specify one weekly pick, one use per team, straight-up scoring, no confidence
weights, and zero revives. `/rosters` returned 31 entries; `/users` returned 27
participants and an identified commissioner. `total_rosters=500` is capacity.
The supplied username matched one active owner entry.
`/matchups/1` returned an empty list; no pick-history import is claimed.

Sleeper's [API documentation](https://docs.sleeper.com/) describes a public, read-only
API. Its [survivor rules](https://support.sleeper.com/en/articles/9689521-nfl-survivor)
specify that ties eliminate and selections lock at kickoff. Those are documented
platform rules; other values come from the league response. Pick'em-specific numeric
settings are observed API fields rather than a published stable schema.

## Approach and boundaries

Use the existing httpx client library, PoolRules validation, pool workspace, and
owner-authenticated API. A manual copy would omit provenance and refresh; browser
scraping would add an unnecessary login dependency. A public API adapter fits the
verified data and existing workflows.

Add a preview/import form and source details using DESIGN.md and existing controls.
Persist a unique Sleeper league ID, a selected public metadata snapshot, and unique
roster IDs for imported owner entries. Never store chat messages or user account
payloads. Network failures do not change saved data. Requests use a fixed HTTPS API
host, validated numeric league IDs, and no redirects or credentials.

Reject unsupported/ambiguous rule configurations before creation. In particular,
revives require outcome-engine work and Sleeper ATS uses provider-specific spreads
that are not supplied by the documented league API. Preview these limitations rather
than silently translating them. Saved picks prevent subsequent rule/season changes.
Imports and refreshes do not overwrite local picks or local entry activity decisions.
Source elimination state is displayed separately from locally graded results.

Pick history is explicitly unavailable in this integration. Reuse checks and season
recommendations only include locally saved picks; users must compare those suggestions
with their Sleeper history and submit actual picks on Sleeper. No scheduled sync or
pick submission is included.

## Validation

Cover URL validation, exact rule mapping including tie elimination, unsupported
settings, user matching, capacity vs. membership, idempotent import, preservation of
saved picks, upstream errors, migration upgrade/downgrade, and browser import/refresh
flows at desktop and mobile widths. Verify the real pool read-only before handoff.

## Verification completed

103 backend tests passed, covering the importer, migrations, pool APIs, concurrent
card saves, legacy safety, pool analysis, and analyst context. All 45 frontend unit
tests passed; TypeScript, the production build, Ruff, and whitespace checks passed.

Browser verification used an isolated temporary database and the real Sleeper API.
Preview showed the verified rules, counts, commissioner, and owner entry; import
created one local entry, and refresh updated the saved source. Invalid URLs showed
an actionable error with no stale import button. At 390px width the page had no
horizontal overflow. The running production installation was not changed.
