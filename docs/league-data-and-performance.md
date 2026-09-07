# League projection data and performance checks

## Projection contract

Player create/update requests and JSON player imports accept a `projection` object:

```json
{
  "name": "Example Player",
  "pro_team": "CHI",
  "position": "RB",
  "projected_points": 14.5,
  "ros_value": null,
  "projection": {
    "source": "Example provider",
    "period": "week",
    "season": 2026,
    "week": 1,
    "source_updated_at": "2026-09-03T12:00:00Z",
    "scoring_basis": "source_points"
  }
}
```

This is illustrative input, not a real player or forecast. Include `source_id` for stable matching on repeated imports.

- `period`: `unknown`, `season`, `week`, or `rest_of_season`. Known periods require a season; weekly values also require a week. Rest-of-season values can supply the starting week.
- `source_updated_at`: the provider's actual forecast update timestamp, with timezone. Omit it when unavailable. A file download time, sync completion time, HTTP cache timestamp, or recommendation generation time is not a substitute.
- `received_at`: assigned by the server and returned in the response. It is not accepted as an import assertion.
- `scoring_basis`: `unknown`, `source_points`, or `league_rules`. The last requires a `scoring` object containing the rules used. Declaring this context does not recalculate imported totals.
- `ros_value`: blank/missing/null means unavailable; numeric zero means the source supplied zero. Values must be finite.

CSV accepts `projection_source`, `projection_period`, `projection_season`, `projection_week`, `projection_source_updated_at`, `projection_scoring_basis`, and `projection_scoring` (a quoted JSON object). JSON uses the nested object shown above. All rows validate before any import writes are committed.

The league UI exposes period, source, source update time, receipt time, and supplied scoring context in Projection sources and waiver Ranking details. Mixed periods trigger a warning. Season/unknown projections are not described as weekly forecasts.

### Storage compatibility

Migration `0007` adds `players.projection_context_json`; it does not rewrite player numbers, roster ownership, or historical zero values. The legacy numeric ROS column remains compatible with existing draft/trade calculations. The persisted `ros_value_state` distinguishes `provided`, `missing`, and `legacy_unknown`; the public API returns null for `missing`, while the existing heuristic engine uses its zero fallback. Legacy untracked zero remains zero plus an explicit unverified label.

Yahoo's player-pool request uses `stat1=S_PS_<season>`. New successful ingestions retain that full-season context, source-scored league settings, and actual receipt time. Roster-only updates do not refresh projection timestamps. Yahoo does not supply a forecast publication timestamp in this ingestion path. Existing independent ROS inputs are not overwritten merely because Yahoo supplies new projected points.

## Bounded page requests

- `/api/v1/leagues/{id}/roster`: rostered players only, without verbose evidence bodies.
- `/api/v1/leagues/{id}/waivers/page`: defaults to ten results; maximum 50. Supports offset, search, role, team, status, and availability. Overall rank is calculated before filtering; ties use player ID for stable ordering.
- `/api/v1/leagues/{id}/projection-leaders`: top ten, requested only when its disclosure opens.

The original full player/waiver endpoints remain available for existing consumers. Read-only league responses and static assets support gzip. Authentication responses and event streams are not targeted for compression. Search is debounced and obsolete requests are cancellable; changing filters resets the visible batch.

## Repeatable measurements

Run from the repository root while the local app is running:

```sh
.venv/bin/python backend/tests/perf/league_benchmark.py --runs 5
```

The script performs GET requests only and prints response sizes, TTFB, total transport time, and row counts. Its 200 KiB/s transfer-floor calculation excludes CPU and latency. `curl --compressed --limit-rate 200k` can provide a separate transfer-limited sample, but very small bodies fit within curl's initial burst and are not a reliable slow-network page-load simulation.

Open [local performance diagnostics](http://127.0.0.1:8787/leagues/1?diagnostics=1) to see this visit's FCP, LCP so far, maximum observed interaction duration, CLS, long tasks, and API transfer bytes. Measurements are opt-in, local-only, unsaved, and never sent to a telemetry service. Unsupported metrics say so. The interaction sample is not field INP.

For physical-device validation, use an already-authorized secure access path on the intended device. Do not expose this localhost-only, authentication-disabled development service publicly. A phone-size desktop viewport does not emulate phone CPU or network. Record device/browser, cache state, connection, and sample count when comparing runs.
