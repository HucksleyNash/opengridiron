# Yahoo Fantasy application guide

Yahoo Fantasy access is reviewed and read-only. Apply through [Yahoo’s access portal](https://sports.yahoo.com/developer/access/) and use the [Yahoo Fantasy API documentation](https://sports.yahoo.com/developer/docs/) when describing the integration.

## Application description

Suggested description:

> A private, single-user fantasy football decision-support application. Its OAuth component reads the owner’s selected NFL fantasy leagues, settings, scoring, teams, rosters, ownership, standings, scoreboards, draft results, transactions, and statistics through Yahoo's Fantasy Sports API. It does not modify Yahoo data, submit lineups or transactions, or expose league data to other users.

Requested access should be limited to the Fantasy Sports read scopes Yahoo makes available for the application.

## Callback URLs

Yahoo requires an exact match.

- Local: `http://localhost:8787/api/v1/integrations/yahoo/callback`
- Public cloud: `https://YOUR_DOMAIN/api/v1/integrations/yahoo/callback`
- VPN: `https://YOUR_PRIVATE_HTTPS_NAME/api/v1/integrations/yahoo/callback`

Use only the callback for the deployment that initiates authorization. Enter the same value under Open Gridiron → Settings.

## Connect after approval

1. In Settings, enter the approved client ID, client secret, and callback URL. These are encrypted before storage.
2. Call `GET /api/v1/integrations/yahoo/start` and open its `authorization_url` in the same browser.
3. Approve read access. Yahoo redirects to the callback and the app stores encrypted access/refresh tokens.
4. Call `POST /api/v1/integrations/yahoo/sync`.

Synchronization retains raw response snapshots, uses stable Yahoo keys for idempotency, refreshes expiring access tokens, and paginates league player collections. A missing resource or Yahoo outage does not erase the last successful snapshot.

## Privacy statement

For an access application, accurately state that this is a private self-hosted tool. League data stays in the owner’s SQLite volume. It is sent to an AI provider only when the owner explicitly starts an analysis, and then only as a generated, timestamped dossier. The isolated Codex runner has no database mount.
