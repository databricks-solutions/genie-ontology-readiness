#!/bin/bash
#
# compliance_scan.test.sh — public-release compliance gate for this Databricks App repo.
#
# Standalone, re-runnable scanner. FAILS (non-zero exit) if the repo contains
# secrets/tokens or real customer data; PASSES only if clean for public release
# per databricks-solutions norms.
#
# Design principle: false negatives (missing a real secret / customer datum) are
# the dangerous failure mode, so patterns favor recall — but each is written to
# skip the OBVIOUS legitimate false positives (env-var reads, ${var.x}
# placeholders, <angle> placeholders, .example/.template files, empty-string
# defaults, dummy values). Every exclusion is documented inline.
#
set -euo pipefail

# --- Deterministic cd to repo root (two levels up from scripts/__tests__) -----
cd "$(cd "$(dirname "$0")/../.." && pwd)"
if [ ! -f databricks.yml ]; then
  echo "FATAL: not at repo root (databricks.yml not found in $(pwd))" >&2
  exit 2
fi
REPO_ROOT="$(pwd)"

# Path of THIS script relative to repo root, so we skip scanning our own patterns.
SELF_REL="scripts/__tests__/compliance_scan.test.sh"

# --- Build the list of files to scan: tracked files only (git ls-files excludes
# node_modules/dist/.git), minus this script, minus lockfiles (huge, hash noise),
# minus binary screenshots (grep -I also guards this).
# (read loop instead of mapfile for macOS bash 3.2 portability)
FILES=()
while IFS= read -r f; do
  FILES+=("$f")
done < <(git ls-files \
  | grep -vE "^${SELF_REL}$" \
  | grep -vE '(^|/)package-lock\.json$' )

# Helper: run an ERE grep over the scan set. Emits "path:line:content" hits.
# Uses -I to skip binary files. Missing-match (exit 1) is fine; we handle it.
scan() {
  local pattern="$1"
  printf '%s\0' "${FILES[@]}" | xargs -0 grep -InE "$pattern" 2>/dev/null || true
}

# Accumulators
FAILS=0
declare -a FINDINGS

record_check() {
  # $1 = check name, $2 = hits (may be empty)
  local name="$1"; local hits="$2"
  if [ -n "$hits" ]; then
    echo "  FAIL: $name"
    echo "$hits" | sed 's/^/    > /'
    FINDINGS+=("$name")
    FAILS=$((FAILS+1))
  else
    echo "  PASS: $name"
  fi
}

echo "=============================================================="
echo "COMPLIANCE SCAN — public-release gate"
echo "repo: $REPO_ROOT"
echo "files scanned: ${#FILES[@]} (tracked, minus self + lockfiles)"
echo "=============================================================="

# ==============================================================================
# CHECK GROUP 1: SECRETS / TOKENS
# ==============================================================================
echo ""
echo "[1] SECRETS / TOKENS"

# 1a. Databricks PATs: dapi + 32 hex, and dkea (OAuth) tokens.
# A real PAT is `dapi` followed by exactly 32 hex chars. Env reads never contain
# a literal token, so matching the literal char class is inherently FP-safe here.
record_check "Databricks PAT (dapi<32hex> / dkea<hex>)" \
  "$(scan 'dapi[0-9a-f]{32}|dkea[0-9a-f]{16,}')"

# 1b. AWS access key id: literal AKIA + 16 upper-alnum. No env-read form exists
# for a literal AKIA..., so the char class alone excludes placeholders.
record_check "AWS access key id (AKIA...)" \
  "$(scan 'AKIA[0-9A-Z]{16}')"

# 1c. aws_secret_access_key ASSIGNED a real-looking literal (>=20 base64-ish chars).
# Excludes: env reads (os.environ / getenv / process.env), ${...} / <...> placeholders,
# and empty string. We require the value to be a quoted 20+ char secret-shaped token.
record_check "aws_secret_access_key = <literal>" \
  "$(scan 'aws_secret_access_key["'\'' ]*[:=][[:space:]]*["'\'' ]*[A-Za-z0-9/+]{20,}' \
     | grep -viE 'os\.environ|getenv|process\.env|\$\{|<[a-z_]+>|=\s*["'\'']{2}' )"

# 1d. Private keys (PEM blocks). Literal header; no legitimate placeholder form.
record_check "Private key PEM block" \
  "$(scan '\-\-\-\-\-BEGIN (RSA|OPENSSH|EC|DSA|PRIVATE) KEY\-\-\-\-\-')"

# 1e. Slack tokens (xoxb-/xoxa-/xoxp-/xoxr-/xoxs-) followed by real token body.
# Require digits+hyphen body so the bare literal "xoxb-" in docs won't match.
record_check "Slack token (xox[baprs]-...)" \
  "$(scan 'xox[baprs]-[0-9]{6,}-[0-9A-Za-z-]{6,}')"

# 1f. GitHub tokens: classic ghp_ + 36, and fine-grained github_pat_ .
record_check "GitHub token (ghp_ / github_pat_)" \
  "$(scan 'ghp_[0-9A-Za-z]{36}|github_pat_[0-9A-Za-z_]{20,}')"

# 1g. Google API key: AIza + 35 url-safe chars. Char class excludes placeholders.
record_check "Google API key (AIza...)" \
  "$(scan 'AIza[0-9A-Za-z_\-]{35}')"

# 1h. Bearer tokens with a REAL-looking literal after "Bearer ".
# Excludes: {token}/${...}/<...> interpolation, and the code idiom
# f"Bearer {token}" / "Bearer " + token used all over config.py. We only flag a
# long opaque literal (>=20 chars, no space/brace) directly after "Bearer ".
record_check "Bearer <literal-token>" \
  "$(scan 'Bearer [A-Za-z0-9._\-]{20,}' \
     | grep -viE '\{[^}]*\}|\$\{|<[a-z_]+>|Bearer \$|Bearer "' )"

# 1i. password = "<literal>" assignments. This is the classic hardcoded-cred leak.
# Excludes (all legitimate in this repo): env reads (os.environ.get("...PASSWORD..."),
# getenv, process.env), assignment FROM a variable (password=token, password=config[...]),
# ${...}/<...> placeholders, empty string "" / '', and yaml keys with empty value.
# We require a quoted NON-empty literal that is not itself an env lookup.
record_check "password = \"<literal>\"" \
  "$(scan 'password["'\'' ]*[:=][[:space:]]*["'\''][^"'\'' ]+["'\'']' \
     | grep -viE 'os\.environ|getenv|process\.env|\$\{|<[a-z_]+>|password["'\'' :=]*(""|'\'\'')|["'\'' ]password["'\'' ]*[:=]["'\'' ]*(token|config|None|\$)' )"

# 1j. client_secret ASSIGNED a real literal (excludes env reads / placeholders / empty).
record_check "client_secret = <literal>" \
  "$(scan 'client_secret["'\'' ]*[:=][[:space:]]*["'\''][^"'\'' ]{8,}["'\'']' \
     | grep -viE 'os\.environ|getenv|process\.env|\$\{|<[a-z_]+>' )"

# 1k. DB CLI password on command line: `-p <literal>` / `--password <literal>`.
# Excludes `-p` followed by a $VAR, ${...}, or <placeholder>.
record_check "CLI db password (-p/--password <literal>)" \
  "$(scan '(^|[[:space:]])(-p|--password)[[:space:]]+[^$<[:space:]-][A-Za-z0-9._@!/+-]{5,}' \
     | grep -viE '\$\{?|<[a-z_]+>|--password[= ]*["'\'']{0,1}\$' )"

# ==============================================================================
# CHECK GROUP 2: CUSTOMER / REAL DATA & PII
# ==============================================================================
echo ""
echo "[2] CUSTOMER / REAL DATA & PII"

# 2a. Specific customer/workspace identifier this project touched.
record_check "customer identifier 'demo-centre' / 'demo_centre'" \
  "$(scan 'demo[-_]centre')"

# 2b. Real workspace deployment hostnames (per-workspace = customer-identifying).
# dbc-<hex>-<hex>.cloud.databricks.com (AWS/GCP) and adb-<digits>.<n>.azuredatabricks.net.
# A generic "https://<host>" or *.cloud.databricks.com doc reference without the
# dbc-/adb- deployment prefix is NOT customer data, so we anchor on the prefix.
record_check "real workspace hostname (dbc-*.cloud.databricks.com)" \
  "$(scan 'dbc-[0-9a-f]+-[0-9a-f]+\.cloud\.databricks\.com')"
record_check "real workspace hostname (adb-*.azuredatabricks.net)" \
  "$(scan 'adb-[0-9]{6,}\.[0-9]+\.azuredatabricks\.net')"

# 2c. Real numeric workspace/account IDs embedded as data. Databricks workspace
# ids are long integers; flag `workspace_id`/`account_id` assigned a long literal
# (>=12 digits). Excludes env reads and ${var}/placeholder forms.
record_check "hardcoded workspace_id/account_id literal" \
  "$(scan '(workspace_id|account_id)["'\'' ]*[:=]["'\'' ]*["'\'']?[0-9]{12,}' \
     | grep -viE 'os\.environ|getenv|process\.env|\$\{|<[a-z_]+>' )"

# 2d. allan.cao personal filesystem paths embedded as DATA (e.g. /Users/allan.cao/...
# or /Workspace/Users/allan.cao@...). The owner-contact EMAIL is allowed (2f); a
# personal home path baked into code/data is not.
record_check "personal filesystem path (/Users/allan.cao, /Workspace/Users/allan.cao)" \
  "$(scan '/(Users|Workspace/Users|home)/allan\.cao')"

# 2e. Email addresses that are NOT the owner contact or example.com.
# Owner contact allan.cao@databricks.com is the intended support contact (allowed).
# security@databricks.com and bugbounty@databricks.com are published Databricks
# security-reporting addresses (allowed; bugbounty@ is the databricks-solutions
# org-standard SECURITY.md contact). example.com / example.org are RFC-2606 doc
# placeholders (allowed). npm-scoped names like @types, @vitejs etc. are not emails
# (excluded by leading @). Anything else that looks like a real person@company
# email is flagged.
record_check "real email address (not owner-contact / example.*)" \
  "$(scan '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' \
     | grep -viE 'allan\.cao@databricks\.com|security@databricks\.com|bugbounty@databricks\.com|@example\.(com|org|net)|(^|[^A-Za-z0-9._%+-])@(types|vitejs|tailwindcss|databricks|radix-ui|testing-library|babel|eslint|rollup|vitejs)' )"

# 2f. Data files that must be inspected for real datasets. This repo ships NO
# .csv/.parquet/.jsonl data files (it reads the customer's live environment).
# If any appear, flag for manual inspection — sample data must be clearly synthetic.
DATA_FILES="$(printf '%s\n' "${FILES[@]}" | grep -iE '\.(csv|parquet|avro|orc|jsonl|ndjson)$' || true)"
record_check "no raw data files (.csv/.parquet/.jsonl/...) shipped" "$DATA_FILES"

# ==============================================================================
# SUMMARY
# ==============================================================================
echo ""
echo "=============================================================="
if [ "$FAILS" -eq 0 ]; then
  echo "RESULT: PASS — repo is clean for public release."
  echo "=============================================================="
  exit 0
else
  echo "RESULT: FAIL — $FAILS check(s) flagged. Offending checks:"
  for f in "${FINDINGS[@]}"; do echo "  - $f"; done
  echo "Investigate each: real leak => remove/externalize the value;"
  echo "false positive => tighten the pattern (do not neuter the check)."
  echo "=============================================================="
  exit 1
fi
