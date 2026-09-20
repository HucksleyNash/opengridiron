#!/usr/bin/env bash
# The single source of truth for local and GitHub Actions checks.
set -euo pipefail
cd "$(dirname "$0")/.."

target="${1:-all}"
case "$target" in
  backend|frontend|all) ;;
  *) echo "Usage: $0 [backend|frontend|all]" >&2; exit 2 ;;
esac

if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x .venv/bin/python ]]; then
    PYTHON="$PWD/.venv/bin/python"
  else
    PYTHON="$(command -v python3)"
  fi
fi
export E2E_PYTHON="$PYTHON"
export CI=true

if [[ "$target" == backend || "$target" == all ]]; then
  "$PYTHON" -m ruff format --check backend codex_runner
  "$PYTHON" -m ruff check backend codex_runner
  "$PYTHON" -m pytest
fi

if [[ "$target" == frontend || "$target" == all ]]; then
  cd frontend
  pnpm install --frozen-lockfile
  pnpm test
  pnpm run build
  pnpm exec playwright test analysis-context.spec.ts weekly-analysis.spec.ts pool-week.spec.ts draft-room.spec.ts sleeper-pools.spec.ts --project=chromium --forbid-only --workers=2
fi
