# Initial repository health audit

Date: 2026-09-07. Scope: first-commit contents, repository hygiene, configured quality
checks, dependency advisories, and build readiness. This is the first baseline; there
is no earlier Git history or health trend.

## Results

| Check | Result |
| --- | --- |
| Python lint (`ruff check backend codex_runner`) | Pass |
| Python formatting (`ruff format --check backend codex_runner`) | Pass; 129 files |
| Backend tests (`pytest`) | 310 passed; two existing warnings |
| Frontend types (`pnpm run lint`) | Pass; this script runs TypeScript, not a JavaScript linter |
| Frontend unit/render tests (`pnpm test`) | 45 passed |
| Production frontend (`pnpm run build`) | Pass |
| CI Chromium journeys | 16 passed; three mobile/tablet-only cases intentionally skipped |
| Python dependency consistency (`pip check`) | Pass |
| Python advisory scan (`pip-audit`) | No known vulnerabilities in 46 installed third-party packages |
| Frontend advisory scan (`pnpm audit`) | No known vulnerabilities in the lockfile |
| Docker application and Codex runner builds | Both pass on the local ARM64 Docker host |
| Compose validation | Local, cloud, and VPN profiles pass, each with and without recovery |
| Credential and private-data review | No confirmed credentials found in the selected source files |
| Documentation file links | No missing or ignored Markdown link targets |

The CI browser command is:

```sh
cd frontend
pnpm exec playwright test analysis-context.spec.ts weekly-analysis.spec.ts pool-week.spec.ts draft-room.spec.ts --project=chromium
```

The health skill's configured type, lint, and test categories score **10/10** after
remediation. Unconfigured dead-code and shell-lint categories are excluded. This
score measures these checks; it is not a coverage measurement or security rating.

## Changes made before the first commit

- Expanded `.gitignore` to cover environment variants, credentials, SQLite journal/WAL
  files, recovery output, browser authentication state and test results, editor state,
  and generated review artifacts. Runtime files remain on disk.
- Updated `.dockerignore` to exclude the same sensitive runtime and generated content
  from image build contexts.
- Preserved source, migrations, synthetic fixtures, lockfiles, deployment files, and
  project documentation. Promoted three small forecast/pool benchmark records into
  `docs/benchmarks/` and updated the README links; raw audit folders stay ignored.
- Corrected one formatting violation in `backend/app/services/pool_week.py` that would
  have failed the existing CI check.
- Raised `cryptography` to `>=50.0.1,<51`, `pytest` to `>=9.0.3,<10`, and
  `pytest-asyncio` to `>=1,<2`. The tested versions are 50.0.1, 9.1.1, and 1.4.0.
- Updated development, CI, and both Docker installation paths to install
  `pip>=26.2.1,<27`. The earlier Python advisory scan reported affected versions of
  cryptography, pytest, and pip. The final scan is clean. Fix information was checked
  against the upstream [cryptography](https://cryptography.io/en/latest/changelog/),
  [pytest](https://docs.pytest.org/en/stable/changelog.html), and
  [pip](https://pip.pypa.io/en/stable/news/) release notes.
- Verified that cryptography 50 decrypts synthetic Fernet and AES-GCM ciphertext
  generated with cryptography 46. The full backend suite, including security,
  migrations, and recovery tests, passes after the upgrade.
- Restricted the GitHub Actions token to read-only repository contents and set a
  seven-day retention period for browser failure artifacts.

The selected project contents total approximately **2.2 MB**. Installed dependencies,
databases, caches, builds, screenshots, and local reports account for almost all of
the much larger working directory. No generated binary or file over 1 MB is selected.
Credential-scanner matches were reviewed individually: they are source expressions,
example placeholders, synthetic test identifiers, numeric model coefficients, or
documented localhost endpoints. The credential-bearing `example.com` URL in a test
is deliberate input proving that the URL validator rejects embedded credentials.

## Remaining maintenance work and limits

- Python dependencies have bounded version ranges but no lockfile; clean installs can
  resolve newer compatible releases. Consider a Python lockfile for repeatable builds.
- Frontend lint currently checks types only. There is no dedicated dead-code analysis
  or enforced test coverage threshold.
- Backend tests emit a Starlette/httpx deprecation warning and a Pydantic warning
  about the runner's `schema` field name. Neither fails the suite.
- This audit ran the CI Chromium subset, not every browser specification or live
  Yahoo/AI integration. Mobile/tablet coverage is outside this baseline.
- Advisory scans cover the local Python development environment and the frontend
  lockfile. They do not certify Docker OS packages, optional production-only Python
  dependencies, the bundled Codex CLI, or live application security. The local
  `opengridiron` package is unpublished and is not an auditable PyPI dependency.
- Cryptography no longer publishes Intel macOS or 32-bit Windows wheels. Docker
  remains the documented default installation path; native installs on those
  platforms need separate compatibility assessment.
- No license has been selected. Choose explicit licensing terms before inviting
  external reuse.

Detailed machine-specific logs and scan output are retained locally under the ignored
`.gstack/repository-audit/` directory. This maintained summary is the audit artifact
included in version control.
