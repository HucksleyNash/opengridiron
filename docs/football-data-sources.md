# Free football sources

Researched and endpoint-tested on September 7, 2026 for personal, non-commercial
installations of Open Gridiron. The project's open-source license does not override
provider terms, including Sleeper's commercial-use restrictions. These additions require no account, cookie, API key,
paid subscription, or browser automation.

## Added sources

| Source | Useful for | Pull method | Live verification | Refresh |
| --- | --- | --- | --- | --- |
| [ESPN NFL](https://www.espn.com/espn/rss/nfl/news) | Pool, lineup and draft briefings: injuries, transactions and team reporting | Published NFL RSS feed | HTTP 200, 27 entries | Existing news schedule; ETag / Last-Modified when supplied |
| [CBS Sports NFL](https://www.cbssports.com/rss/headlines/nfl/) | Pool, lineup and draft briefings: team news and editorial analysis | Public NFL RSS feed | HTTP 200, 36 entries | Existing news schedule; conditional HTTP |
| [Sleeper players](https://docs.sleeper.com/#players) | Reported player availability, depth-chart order and identity cross-checks | Public JSON dictionary | HTTP 200, 12,226 raw entries; ingestion retains active players with a team and usable identity | At most once per 24 hours after a successful fetch |
| [Fantasy Football Calculator ADP](https://help.fantasyfootballcalculator.com/article/42-adp-rest-api) | Draft-market demand, average selection and sample variability | Public JSON API | 263 PPR, 227 half-PPR and 214 non-PPR rows for 12-team 2026 drafts; samples ended September 5 | At most once per 24 hours per season / format / team count |

ESPN documents its NFL feed in its [RSS guide](https://www.espn.com/espn/news/story?page=rssinfo).
RSS publishers are labeled separately from official NFL sources. The existing
collector retains headlines, bounded excerpts, article links and publication dates;
it does not fetch full articles. Syndicated stories can appear in more than one
publication; multiple headlines are not independent confirmation.

[Sleeper's API documentation](https://docs.sleeper.com/) permits free non-commercial
use without a token and asks clients to cache the complete player file, fetching
it no more than daily. Commercial use requires contacting Sleeper. Its current
player dictionary is not historical status data. Collection time is not the time
an injury field changed; missing injury fields do not establish that a player is
healthy. Confirm game-day decisions with official reports.

Fantasy Football Calculator's [API documentation](https://help.fantasyfootballcalculator.com/article/42-adp-rest-api)
allows free personal and commercial use, requests attribution and explains that
data updates daily. Its [calculation notes](https://help.fantasyfootballcalculator.com/article/34-average-draft-position-adp-data)
describe human mock-draft selections with computer selections excluded. ADP is
draft behavior, not a fantasy-points forecast or a calibrated next-pick probability.
The integration retains the sample window, total drafts, each player's sample
count and standard deviation. A sample ending more than 14 days ago is labeled
stale even if downloaded today.

## Where the app uses them

- **News wire → Source registry:** ESPN and CBS are seeded on startup alongside
  the two NFL sources. Existing records, owner names and disabled settings survive
  restarts. Both feeds participate in existing news collection, pool check-ins and
  weekly league refreshes; attributed stories enter analyst dossiers.
- **News wire → Player and draft data:** view source status, record counts,
  collection dates and ADP sample dates; fetch each source or use **Refresh all**.
  The registry's ADP reference is current-season, 12-team PPR.
- **Command center → Refresh sources:** also refreshes Sleeper and the ADP reference.
- **Pool analysis:** fetches cached Sleeper status and includes reported injuries
  for that week's teams. This supplemental source is labeled optional; its failure
  does not disable otherwise valid pool analysis. Existing required-source gates
  remain in force. No probabilities, lines or saved picks are changed.
- **Weekly league analysis:** refreshes Sleeper and freezes matched evidence into
  the report before the analyst runs. It does not overwrite Yahoo availability,
  points or lineup optimization inputs. Official availability still matters.
- **Draft analysis:** fetches Sleeper and the ADP market matching the session's
  frozen scoring and team count. Standard, half-PPR and PPR **1-QB** formats with
  8, 10, 12 or 14 teams are supported. Other formats are explicitly unsupported;
  a 12-team PPR reference is never silently substituted. Other scoring and roster
  differences remain a limitation. Immutable draft rankings and projections remain
  unchanged. Follow-ups reuse the original frozen evidence.

Player matching requires normalized name, team and position to agree uniquely.
Unmatched and ambiguous identities are omitted. League/draft dossiers include at
most 80 matched players per source, prioritizing the owner's roster and then saved
projection order. Pool dossiers include at most 120 injury-status rows. Counts
disclose this bounded coverage. Raw provider files are parsed into compact
`DataSnapshot` evidence with response hashes and source attribution.

The scheduler checks structured sources hourly, starting 75 seconds after startup;
the persistent 24-hour cache prevents repeated downloads. It includes the reference
ADP market and supported active draft-room markets. Network or parse failures get a
15-minute retry cooldown and preserve the last successful snapshot, labeled stale.
Current Sleeper status is withheld for a different NFL season. Saved reports and
follow-up evidence are not refreshed retrospectively.

## API and operation

New routes use the application's existing owner authentication and CSRF protection:

```text
GET  /api/v1/data-sources
POST /api/v1/data-sources/sleeper/fetch
POST /api/v1/data-sources/ffc/fetch?season=2026&scoring_format=half-ppr&teams=10
```

The list route accepts the same season, scoring_format and teams query parameters.
Fetch respects caching; there is no bypass that repeatedly downloads the full
Sleeper database. No database migration or new dependency is required. Rebuild and
restart an existing deployment to activate this code, then use either refresh
control. Local development starts collecting after the API restarts.

## Investigated but not added

| Candidate | Finding and decision |
| --- | --- |
| FantasyPros `https://www.fantasypros.com/rss/nfl.xml` | Returned HTTP 200 with an empty body. Excluded rather than reporting a successful feed. |
| FantasyPros general `https://www.fantasypros.com/feed/` | Working 10-entry editorial feed, but not scoped to NFL. Proposed football-specific feed paths were missing or returned a 404 page with HTTP 200. Excluded pending a reliable football feed. |
| FantasyPros structured API | [Documented endpoints require an API key](https://api.fantasypros.com/v2/docs); not a verified free, keyless source for this task. |
| nflverse injuries | The [maintainer's availability page](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html) says its injury source died after 2024, with no replacement ETA. Do not use it for current injury coverage. |
| nflverse snap counts / depth charts | Good candidates for a later forecast-feature change. [Published update schedules](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html) exist, but correct time alignment and held-out model evaluation are needed before changing serving forecasts. From 2025, depth charts use timestamps instead of week numbers. |

The existing nflverse schedule remains the source for pool lines, market-derived
probabilities and final results. These additions do not establish a new live odds
feed or a measured forecasting advantage.

## Validation

`backend/tests/test_football_sources.py` covers successful collection, daily caching,
durable failure cooldown, stale sample labeling, malformed HTTP-200 responses,
scoring/sample mismatches, ambiguous identities, attribution, no league-value
mutation, source seeding, and API validation. The command-center browser regression
checks that both new structured feeds are refreshed with existing sources.

Verification result: all 310 backend tests passed, the frontend production build
passed, and the Chromium source-refresh regression passed. In a temporary local
instance, the actual source buttons imported 27 ESPN stories, 36 CBS stories,
2,691 usable Sleeper player records and 263 FFC PPR ADP records. The rendered
registry showed collection dates, ADP sample dates and successful fetch results.
