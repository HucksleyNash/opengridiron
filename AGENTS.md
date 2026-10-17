## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:

- Product ideas/brainstorming: invoke `/office-hours`.
- Strategy/scope: invoke `/plan-ceo-review`.
- Architecture: invoke `/plan-eng-review`.
- Design system/plan review: invoke `/design-consultation` or `/plan-design-review`.
- Full review pipeline: invoke `/autoplan`.
- Bugs/errors: invoke `/investigate`.
- QA/testing site behavior: invoke `/qa` or `/qa-only`.
- Code review/diff check: invoke `/review`.
- Visual polish: invoke `/design-review`.
- Ship/deploy/PR: invoke `/ship` or `/land-and-deploy`.
- Save progress: invoke `/context-save`.
- Resume context: invoke `/context-restore`.
- Author a backlog-ready spec/issue: invoke `/spec`.

## Required CI gate

- On a new clone, install development dependencies and Chromium as described in
  `README.md`, then enable the tracked hook with
  `git config --local core.hooksPath .githooks`.
- Before every push, run `bash scripts/check.sh` from the repository root. This is
  the same check command used by GitHub Actions; focused tests alone do not count.
- Fix every failing check before pushing. Never bypass the pre-push hook, disable
  a check, weaken an assertion, or skip a failing test to get a green result.
- Commit or stash changes before pushing; verify the actual outgoing commit.
- When changing CI checks, update `scripts/check.sh` so local and hosted checks
  remain aligned. Keep Ruff and pnpm versions consistent with their manifests.
- After pushing, inspect the GitHub Actions run for the pushed SHA. Do not call
  work complete while that run is pending or failing. If verification is blocked,
  report the blocker explicitly instead of claiming success.

## Design System

Always read `DESIGN.md` before making visual or UI decisions.
All font choices, colors, spacing, component language, and aesthetic direction are defined there.
Do not deviate without explicit user approval.
In QA mode, flag code that does not match `DESIGN.md`.
