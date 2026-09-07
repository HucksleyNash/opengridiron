# Player synopsis

Clicking a player in the roster or lineup opens an accessible, shared detail panel without losing the selected fantasy team or lineup objective. The same player control can serve the waiver list and projection leaders.

- Show identity, imported availability, fantasy roster slot, and stored projections with their period and provenance immediately.
- Retrieve the NFL's current injury table and a player-specific Google News RSS search on demand; combine search results with matching locally collected news from the last 30 days. Attribute every report, link to its source, and distinguish publication dates from retrieval dates.
- Keep official injury-table entries separate from articles and imported fantasy status. An absent entry is not medical clearance. Do not synthesize injuries or alter lineup data from article headlines.
- Use a native modal dialog styled with the existing DESIGN.md tokens, structural rules, and news rows. Support Escape, focus restoration, mobile scrolling, loading, retry, empty, partial-failure, and stale-cache states.
- Cache public sources briefly with bounded memory, share the NFL report across players, and retain prior successful data during temporary fetch failures. No API key or AI provider is required.

Alternatives considered: a separate player route would interrupt roster comparison; filtering only the existing news feed would miss player-specific coverage. A shared panel with on-demand public sources fits the requested workflow.

Validation: source parsing and identity matching, freshness and failure behavior, and browser checks for opening from roster/lineup, keyboard dismissal, mobile layout, and recovery.
