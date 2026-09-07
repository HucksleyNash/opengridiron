# Yahoo authenticated scraper

The authenticated scraper is the no-API-key path for importing a league you can already view in Yahoo Fantasy Football. It makes read-only requests using an existing Yahoo browser session. It never signs in with or stores your Yahoo password and never submits lineups, transactions, bids, or picks.

## Connect a session

1. Sign in to Yahoo and open the home page for each football league you want to import.
2. Open the browser developer tools and select the **Network** panel.
3. Reload the league page and select the main document request to `football.fantasysports.yahoo.com`.
4. Under **Request Headers**, copy only the value beside `Cookie`. Copying `Cookie: …` also works; the app removes the prefix.
5. In Open Gridiron, open **Settings → Yahoo authenticated scraper**.
6. Paste one league home URL per line and paste the Cookie header value, then select **Save encrypted session**.
7. Select **Scrape now**.

Accepted league URLs use HTTPS on `football.fantasysports.yahoo.com` and point to a league home page, for example:

```text
https://football.fantasysports.yahoo.com/f1/123456
https://football.fantasysports.yahoo.com/league/your_custom_league_name
```

## What is imported

- League name, season, scoring modifiers, roster slots, and FAAB budget when shown by Yahoo.
- Every discoverable fantasy team and its current roster slots.
- Yahoo's paginated league player list, including team, position, injury status, and ownership.
- Draft results after a draft has completed.
- A normalized `yahoo_scrape` snapshot with source status. Raw cookies and raw authenticated HTML are excluded.

Imports are idempotent. Yahoo player IDs and league references update existing records, and later runs refresh ownership and roster slots without replacing stored projections.

## Session safety and expiration

Treat the Cookie header like a password: anyone holding it may be able to access the same Yahoo account session. Use Open Gridiron only over loopback, a trusted private network, or its authenticated HTTPS deployment. The value is encrypted with the application secret and is never returned to the frontend after saving.

Yahoo eventually expires sessions. If sync reports that Yahoo rejected the saved browser session, repeat the connection steps with a new Cookie header. Leaving the Cookie field blank when saving URL changes preserves the currently stored session.

The scraper deliberately accepts only Yahoo Fantasy Football HTTPS URLs, follows redirects only within that host, and caps player-list pagination. If the cap is reached, the sync and snapshot are marked partial instead of silently claiming a complete import.
