#!/bin/bash
set -euo pipefail

# Build-verification test for the extracted standalone genie-ontology-readiness repo.
# Proves the extraction did not break the build:
#   CHECK 1 - frontend builds (npm ci/install + npm run build) and emits a non-empty dist/
#   CHECK 2 - `databricks bundle validate` passes (SKIPs on CLI-auth or missing-CLI env issues)
#
# Exit 0 only if CHECK 1 PASSED and CHECK 2 is PASS or SKIP.

# --- Resolve repo root deterministically -------------------------------------
# This file lives at app/frontend/__tests__/build.test.sh -> repo root is 3 levels up.
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

if [ ! -f databricks.yml ]; then
  echo "FAIL: could not resolve repo root (no databricks.yml at $REPO_ROOT)"
  exit 1
fi
echo "Repo root: $REPO_ROOT"
echo "======================================================================"

FRONTEND_DIR="$REPO_ROOT/app/frontend"
DIST_DIR="$FRONTEND_DIR/dist"

CHECK1_STATUS="FAIL"
CHECK2_STATUS="FAIL"

# --- CHECK 1: frontend build -------------------------------------------------
echo "CHECK 1: frontend build"
cd "$FRONTEND_DIR"

if [ -f package-lock.json ]; then
  echo "  -> npm ci"
  npm ci
else
  echo "  -> npm install (no package-lock.json)"
  npm install
fi

echo "  -> npm run build"
npm run build

if [ -d "$DIST_DIR" ] && [ -n "$(ls -A "$DIST_DIR" 2>/dev/null)" ]; then
  CHECK1_STATUS="PASS"
  echo "PASS CHECK 1: build output present and non-empty at $DIST_DIR"
  echo "  dist contents:"
  ls -la "$DIST_DIR" | sed 's/^/    /'
else
  CHECK1_STATUS="FAIL"
  echo "FAIL CHECK 1: build output dir missing or empty ($DIST_DIR)"
fi
echo "----------------------------------------------------------------------"

# --- CHECK 2: bundle validate ------------------------------------------------
echo "CHECK 2: databricks bundle validate"
cd "$REPO_ROOT"

if ! command -v databricks >/dev/null 2>&1; then
  CHECK2_STATUS="SKIP"
  echo "SKIP CHECK 2: databricks CLI not installed, cannot validate bundle"
else
  # Capture combined output; do not let a non-zero exit abort the script.
  set +e
  BUNDLE_OUT="$(databricks bundle validate 2>&1)"
  BUNDLE_RC=$?
  set -e

  echo "  databricks bundle validate exit code: $BUNDLE_RC"
  echo "  ---- output ----"
  echo "$BUNDLE_OUT" | sed 's/^/    /'
  echo "  ----------------"

  if [ $BUNDLE_RC -eq 0 ]; then
    CHECK2_STATUS="PASS"
    echo "PASS CHECK 2: bundle validate succeeded"
  elif echo "$BUNDLE_OUT" | grep -qiE "refresh token|token is invalid|token expired|authentication|oauth|default auth|cannot resolve|no configuration profile|credentials"; then
    CHECK2_STATUS="SKIP"
    echo "SKIP: bundle validate blocked on CLI auth, not a config error"
  else
    CHECK2_STATUS="FAIL"
    echo "FAIL CHECK 2: bundle validate failed with a genuine config/schema error"
  fi
fi
echo "======================================================================"

# --- Summary -----------------------------------------------------------------
echo "SUMMARY:"
echo "  CHECK 1 (frontend build):   $CHECK1_STATUS"
echo "  CHECK 2 (bundle validate):  $CHECK2_STATUS"

if [ "$CHECK1_STATUS" = "PASS" ] && { [ "$CHECK2_STATUS" = "PASS" ] || [ "$CHECK2_STATUS" = "SKIP" ]; }; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
