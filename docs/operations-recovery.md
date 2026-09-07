# Source refresh, startup, and disaster recovery

The application refreshes its saved evidence in the background. Weekly lineups are calculated on demand. General analyst calls are never scheduled. News classification runs only when you explicitly assign an enabled provider the `news` default in Settings; it classifies newly collected headlines once and records usage. Without that assignment, collection uses the local keyword classifier.

## Refresh controls

Set these environment variables before starting the app. All deployment Compose files forward them.

| Variable | Default | Behavior |
| --- | ---: | --- |
| `SCHEDULER_ENABLED` | `true` | Turn all background jobs on or off. Tests disable them. |
| `YAHOO_REFRESH_ENABLED` | `true` | Refresh saved Yahoo leagues only after URLs and a browser cookie have been configured. |
| `YAHOO_REFRESH_MINUTES` | `60` | Full Yahoo roster, status, and projection refresh; minimum 30 minutes. |
| `YAHOO_REQUEST_INTERVAL_SECONDS` | `1` | Minimum spacing between Yahoo request starts; minimum 0.5 seconds. |
| `NEWS_REFRESH_MINUTES` | `15` | Refresh enabled news sources; minimum 5 minutes. |
| `NFLVERSE_REFRESH_MINUTES` | `60` | Refresh games outside the kickoff window; minimum 15 minutes. |
| `NFLVERSE_LIVE_REFRESH_MINUTES` | `15` | Refresh games from 24 hours before through 8 hours after a saved kickoff; minimum 5 minutes. nflverse can lag live play. |
| `SOURCE_FAILURE_COOLDOWN_MINUTES` | `30` | Minimum delay after source failure. Longer Yahoo `Retry-After` values win. |
| `CODEX_RUN_TIMEOUT_SECONDS` | `270` | Runner execution limit. Timed-out or canceled CLI processes and children are terminated and reaped. |

NFL player identity rosters refresh once daily. Successful source snapshots retain their actual retrieval times; failures keep cached evidence and record an unsuccessful `JobRun`. Partial news collection is reported as partial or failed, never as fully completed. The job's saved `next_retry_at` survives restarts. Yahoo host throttles also survive restarts and apply to manual, background, and live draft synchronization. Shared-volume file leases prevent overlapping imports/jobs across workers. Operator recovery should use a single application instance; the SQLite deployment is not a distributed service.

A fresh authenticated installation requires `APP_SECRET`, a private/public HTTPS URL as appropriate, and `OWNER_PASSWORD`. Startup fails if authentication is enabled but no owner can be created. Once an owner exists, removing `OWNER_PASSWORD` does not reset that owner's password or prevent startup. Keep the original `APP_SECRET`: changing it makes encrypted provider credentials and Yahoo cookies unreadable.

## What ordinary backups contain

The seven rolling backups available in the UI contain a compact, consistent SQLite snapshot. `VACUUM INTO` omits unused database pages and opens the original database read-only. These backups retain leagues, entries, analysis history, encrypted settings, and account records. They do **not** include the encryption master secret, runtime `.env` values, or the separate `codex-home` volume. A downloaded database alone is not a complete disaster-recovery backup.

The offline recovery utility creates a passphrase-encrypted bundle containing an integrity-checked database snapshot, application data including the local master secret and saved model files, the complete Codex home, and the supplied environment file. Regenerable caches, rolling backups, and transient locks are excluded. The utility exposes no HTTP route and prints no credentials. Its output is created with owner-only permissions. Store the recovery passphrase separately from the bundle.

## Create a complete bundle

Use the deployment file that owns your volumes (`compose.yaml`, `compose.cloud.yaml`, or `compose.vpn.yaml`) consistently. The examples below use the local file. Do not switch project names when recovering existing volumes.

1. Keep your actual `.env` beside the Compose files. Create a private output directory with `mkdir -p recovery && chmod 700 recovery`.
2. Stop both services for a consistent application/Codex snapshot: `docker compose -f compose.yaml stop app codex-runner`.
3. Run the offline helper. It has no network and mounts both data volumes read-only:

   ```sh
   docker compose -f compose.yaml -f compose.recovery.yaml run --rm --build recovery create \
     --data-dir /data --codex-home /codex-home \
     --environment-file /recovery-config.env \
     --bundle /recovery-output/football-recovery.enc
   ```

   Enter a new passphrase of at least 12 characters. Choose a new filename for each bundle; existing files are never overwritten. On a host without Docker, run `PYTHONPATH=backend python -m app.operations.recovery create` with the actual directory paths. Use `--database` only when the SQLite file is outside the standard data location. For unattended backups, `--password-file` reads a protected passphrase file; keep that file outside the backup archive.
4. Start services again with `docker compose -f compose.yaml up -d app codex-runner`.
5. Copy the encrypted bundle to a separate machine or storage device. Test extraction into a new directory using the next procedure before relying on it.

The helper's temporary filesystem defaults to 2 GB, configurable with `RECOVERY_TMPFS_SIZE`. The database snapshot is compacted without changing the original database. Allow space for the compact database, retained data and Codex history, and their compressed archive; restore also needs room for the archive and extracted files. Increase the limit for larger installations or run the utility on a host with a private temporary directory and sufficient disk space. A container tmpfs uses host memory; the encryption/decryption streams data rather than loading the entire archive into a Python buffer.

## Verify and restore

Restore into a **new, empty deployment** while its app and Codex runner are stopped. Keep any previous deployment or volume backup until verification completes. Restoration into an occupied directory is rejected.

1. Use the helper to decrypt and verify the archive into a private staging directory:

   ```sh
   docker compose -f compose.yaml -f compose.recovery.yaml run --rm recovery restore \
     --bundle /recovery-output/football-recovery.enc \
     --output-dir /recovery-output/restored
   ```

   The passphrase and authenticated archive are checked before anything is installed. The restored SQLite file must pass `PRAGMA integrity_check`; symbolic links, device files, and archive path traversal are rejected.
2. Restore `recovery/restored/configuration.env` as the new deployment's `.env`, adjusting host-specific URLs and network bindings. The restored database is always `data/football.db`; if the original installation used a custom database filename or location, adjust `DATABASE_URL` to the restored path. Preserve `APP_SECRET` and runner credentials. If the bundle was created without an environment file, recover the named values from its private `manifest.json`; the local master secret is also retained in `data/master-secret`. Do not post these files in issues or upload them through the app.
3. Copy the staged `data/` into the new `football-data` volume and `codex-home/` into the new `codex-home` volume using your container operator tools. Both destination volumes must be empty. Preserve directory structure, then assign ownership to UID/GID `10001:10001` for app data and `10002:10002` for Codex home. The application images define these IDs. Do not copy SQLite `-wal` or `-shm` files from the old running instance; the bundle already contains a consistent database.
4. Start the app and runner with the **same deployment file/project name**. Migrations run before the API starts. Verify sign-in, saved leagues and pool entries, analysis history, and the configured provider status. Refresh Yahoo only after confirming the expected leagues; an expired Yahoo cookie still needs replacement. Codex authentication may require a new sign-in if its upstream session has expired.
5. Remove the unencrypted staging directory once verification and retention requirements are satisfied. Keep the encrypted bundle and its separately stored passphrase.

## Test isolation and CI

Backend tests create a unique temporary database per invocation and do not delete a shared filename. Migration tests use the current Python interpreter. Playwright starts its own backend on port 18080 and refuses to reuse existing backend/frontend servers; its unique database is under the system temporary directory. It blocks service workers, and its Vite proxy points only at the isolated backend. A busy test port fails explicitly. CI runs backend tests/lint/format, frontend unit tests/build, and selected analyst-context, provider-settings, weekly-analysis, pool-card, and draft browser tests.
