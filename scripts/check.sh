#!/usr/bin/env bash
# One-command quality gate for the repo.
#
# GitHub Actions is disabled for this repo at the org level, so this script is the
# CI: run it locally before pushing (and it's wired as an opt-in pre-push hook via
# .pre-commit-config.yaml). It runs the same checks a CI workflow would:
#   1. frontend build + typecheck (tsc + vite)
#   2. frontend tests (vitest)
#   3. frontend build-artifact assertion
#   4. python lint (ruff)
#   5. python tests (pytest)
#   6. compliance / secrets-and-raw-data scan
#
# Usage:  bash scripts/check.sh
# Exits non-zero on the first failing gate.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

step "Frontend: install (if needed), build + typecheck"
pushd app/frontend >/dev/null
[ -d node_modules ] || npm ci
npm run build            # tsc -b && vite build
step "Frontend: tests (vitest)"
npm test
step "Frontend: build-artifact assertion"
bash __tests__/build.test.sh
popd >/dev/null

step "Backend: ruff lint"
if command -v ruff >/dev/null 2>&1; then
  ruff check app scripts
else
  python3 -m ruff check app scripts 2>/dev/null || {
    echo "ruff not installed — 'pip install ruff' (or 'pipx install ruff'). Skipping lint." >&2
  }
fi

step "Backend: pytest"
if command -v pytest >/dev/null 2>&1; then
  pytest -q
else
  python3 -m pytest -q 2>/dev/null || {
    echo "pytest not installed — 'pip install pytest'. Skipping tests." >&2
  }
fi

step "Compliance: secrets / raw-data scan"
bash scripts/__tests__/compliance_scan.test.sh

printf '\n\033[1;32mAll checks passed.\033[0m\n'
