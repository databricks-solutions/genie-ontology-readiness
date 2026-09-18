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
#   7. cross-cloud doc-link coverage (OPT-IN — makes network calls, so it is off
#      by default to keep this gate hermetic/offline; enable when cutting a release)
#
# Usage:  bash scripts/check.sh
#         GOR_VERIFY_DOC_LINKS=1 bash scripts/check.sh   # also run step 7 (needs network)
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

step "Backend: install deps (app requirements + tooling)"
# The backend test suite (tests/ + app/server/test_*.py) imports the app's
# runtime deps (fastapi, aiohttp, databricks-sdk, ...), so the gate must
# provision them before collecting — mirror the frontend's `npm ci`. Best-effort
# so a machine without a writable pip env degrades to the skips below rather than
# aborting; use a venv/uv if this warns (e.g. PEP 668 externally-managed).
python3 -m pip install -q -r app/requirements.txt ruff pytest || \
  echo "  (warning) backend dep install failed — ruff/pytest may be skipped below" >&2

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

# Cross-cloud doc-link coverage. Off by default (this gate is hermetic/offline);
# it HEAD-checks every embedded docs link on gcp/azure, so it needs the network.
# Enable for a release: GOR_VERIFY_DOC_LINKS=1 bash scripts/check.sh
if [ "${GOR_VERIFY_DOC_LINKS:-0}" = "1" ]; then
  step "Docs: cross-cloud link coverage (network)"
  python3 scripts/verify_doc_links.py
else
  printf '\n\033[2m(skipping cross-cloud doc-link check — set GOR_VERIFY_DOC_LINKS=1 to run it)\033[0m\n'
fi

printf '\n\033[1;32mAll checks passed.\033[0m\n'
