# Design System — Open Gridiron

## Product Context

- **What this is:** A private football decision workstation for fantasy leagues, drafts, pools, official NFL news, and evidence-grounded analysis.
- **Who it is for:** One serious owner operating several teams and entries who wants the information posture of an NFL analyst.
- **Space:** Football analytics, newsroom monitoring, decision support, and market-style data terminals.
- **Project type:** Responsive data-dense web application and installable PWA.

## Aesthetic Direction

- **Direction:** NFL intelligence terminal.
- **Decoration:** Minimal. Typography, alignment, live data, and thin rules carry the interface.
- **Mood:** Calm, exact, current, and professional. It should feel like an internal team operations workstation, not a fantasy-game dashboard.
- **Memorable quality:** “I feel like one of the analysts of an NFL team.”
- **Avoid:** Green-dominant themes, card mosaics, pill-heavy layouts, soft consumer-SaaS styling, ornamental gradients, and novelty sports graphics.

## Typography

- **Display:** Barlow Condensed, weights 600–700. Use for page titles, section titles, team abbreviations, and high-signal broadcast labels.
- **Body/UI:** IBM Plex Sans, weights 400–600. Use for navigation, prose, forms, and actions.
- **Data/tables:** IBM Plex Mono, weights 400–600, with tabular numbers. Use for timestamps, rankings, percentages, status, and compact labels.
- **Loading:** Google Fonts with `display=swap`; preconnect to the font origins.
- **Scale:** 11px data label, 12px caption, 14px compact UI, 16px body, 20px section title, 32–40px page title, 56px display title where space permits.
- **Rules:** Body copy is at least 16px when it carries instructions or narrative. Uppercase and letter spacing are reserved for short mono labels and condensed headings.

## Color

- **Approach:** Restrained. One cool signal accent plus semantic amber and red.
- **Canvas:** `#07090C`
- **Workspace:** `#0D1117`
- **Raised/selected state:** `#141A21`
- **Strong rule:** `#28313C`
- **Soft rule:** `#1D242C`
- **Primary text:** `#F2F4F7`
- **Secondary text:** `#98A2B0`
- **Faint metadata:** `#66717F`
- **Signal blue:** `#5AA7FF` for selection, links, freshness, and primary action.
- **Signal-blue background:** `#10263E`
- **Warning:** `#F6B94A`
- **Error/urgent:** `#FF6670`
- **Positive:** Use signal blue plus a textual label or icon; never depend on red/green encoding.
- **Native UI:** Declare `color-scheme: dark`.

## Spacing

- **Base unit:** 4px.
- **Density:** Compact but readable.
- **Scale:** 2xs 2px, xs 4px, sm 8px, md 12px, lg 16px, xl 24px, 2xl 32px, 3xl 48px, 4xl 64px.
- Related rows share a rule and tight vertical rhythm. Separate work areas with 24–32px gaps or structural borders.

## Layout

- **Approach:** Grid-disciplined workstation with editorial hierarchy.
- **Shell:** Persistent command rail, optional top information tape, main workspace, and secondary intelligence rail.
- **Grid:** 12-column desktop, 8-column tablet, 4-column mobile.
- **Content width:** Use the available workspace; constrain prose to 72 characters.
- **Borders:** One-pixel structural rules replace most containers.
- **Radius:** 2px controls, 4px grouped workspaces, 6px maximum. Full radius is reserved for status dots only.
- **Cards:** A card must be the interaction, such as a league or pool entry. Do not put ordinary sections, metrics, feeds, or settings groups in floating cards.
- **Responsive:** Data rows reduce columns by priority. Mobile retains a labeled five-destination navigation bar and at least 44px touch targets.

## Component Language

- **Metrics:** Horizontal stat tape separated by vertical rules.
- **News/alerts:** Wire-service rows with timestamp, headline, summary, and right-aligned category/status.
- **Tables:** Full-width rows, mono headers, tabular values, subtle hover/selection fills.
- **Forms:** Visible labels beside or above inputs; grouped with shared horizontal rules instead of individual cards.
- **Actions:** Square or slightly rounded. Signal blue is reserved for the primary action; secondary actions are transparent with a rule.
- **Status:** Dot plus explicit text. Color never communicates status alone.
- **Empty states:** Integrated into the work area with one direct next action; no large decorative illustration.

## Motion

- **Approach:** Minimal and functional.
- **Durations:** 80ms pressed state, 150ms hover/focus, 220ms panel/menu entry.
- **Easing:** ease-out for entry, ease-in for exit, ease-in-out for movement.
- **Properties:** Animate opacity and transform only. Never use `transition: all`.
- **Live updates:** A short background flash may identify a changed row. Do not continuously scroll the information tape.
- Respect `prefers-reduced-motion`.

## Decisions Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-09-02 | Replace the green card dashboard with an NFL intelligence-terminal system | The user wants a modern news/market/stats display that feels like an internal NFL analyst workstation. |
| 2026-09-02 | Use graphite neutrals, signal blue, Barlow Condensed, and IBM Plex | This combination creates broadcast authority, data clarity, and a distinct non-consumer posture. |
| 2026-09-02 | Make structural rules and rows the default grouping device | This removes explicit cards while retaining dense, scannable hierarchy. |
| 2026-09-02 | Use the full analyst-workstation composition for the Command Center | The approved view leads with an NFL-week ticker and five-metric tape, then pairs the priority wire and player market with a right-side source-status and weekly-game rail. |
