#!/usr/bin/env python3
"""Cross-cloud coverage check for the app's documentation links.

Every embedded docs link is authored for AWS
(``https://docs.databricks.com/aws/en/<path>``) and rewritten per-cloud at serve
time by ``server.doc_links``. That rewrite assumes each page also exists on GCP
(``docs.databricks.com/gcp/en/<path>``) and Azure
(``learn.microsoft.com/en-us/azure/databricks/<path>``). This script verifies that
assumption over the network so a divergence is caught before a release rather than
becoming a broken link in a deployed app.

Because it makes network calls it is OFF by default in ``scripts/check.sh`` (that
gate is hermetic/offline). It is wired there as an opt-in step — enable it when
cutting a release:

    GOR_VERIFY_DOC_LINKS=1 bash scripts/check.sh   # runs it as part of the gate
    python3 scripts/verify_doc_links.py            # or run it directly

A non-AWS page that returns a real HTTP error (e.g. 404) and is not already in
``server.doc_links.AWS_ONLY_PATHS`` is reported as a GAP and the script exits 1 —
add such paths to ``AWS_ONLY_PATHS`` so they fall back to AWS. A page that cannot
be reached at all (timeout/DNS/connection) is reported separately as UNREACHABLE
and exits 2 — that is a transient network problem, not a doc gap, so re-run rather
than pinning the path.
"""

import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCAN = [
    REPO / "app/server/content/accelerators.py",
    REPO / "app/server/content/library.py",
    *(REPO / "app/accelerators").rglob("*.md"),
]

AWS_PREFIX = "https://docs.databricks.com/aws/en/"
BASES = {
    "aws": "https://docs.databricks.com/aws/en/",
    "gcp": "https://docs.databricks.com/gcp/en/",
    "azure": "https://learn.microsoft.com/en-us/azure/databricks/",
}
_URL_RE = re.compile(r"https://docs\.databricks\.com/aws/en/[^\s)\]\"'>]+")


# Sentinel for "could not determine status" — a transient network failure
# (timeout, DNS, connection reset), NOT an HTTP response. Kept distinct from a
# real HTTP code so a flaky network is never mistaken for a missing page and a
# valid path is never wrongly pinned to AWS_ONLY_PATHS.
UNREACHABLE = -1


def _status(url: str) -> int:
    """Return the HTTP status for ``url``, or ``UNREACHABLE`` on a transient
    network error (timeout, DNS, connection reset) — i.e. no HTTP response at all.
    """
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "gor-doclink-check"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        if e.code in (403, 405):  # some pages reject HEAD — retry with GET
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": "gor-doclink-check"}), timeout=30
                ) as r:
                    return r.status
            except urllib.error.HTTPError as e2:
                return e2.code
            except Exception:
                return UNREACHABLE
        return e.code
    except Exception:
        return UNREACHABLE


def main() -> int:
    try:
        sys.path.insert(0, str(REPO / "app"))
        from server.doc_links import AWS_ONLY_PATHS
    except Exception:
        AWS_ONLY_PATHS = frozenset()

    paths: set[str] = set()
    for f in SCAN:
        for m in _URL_RE.finditer(f.read_text(encoding="utf-8")):
            paths.add(m.group(0)[len(AWS_PREFIX):])

    print(f"Checking {len(paths)} doc paths across aws/gcp/azure...\n")
    missing: list[str] = []
    unreachable: list[str] = []
    for path in sorted(paths):
        row = {cloud: _status(base + path) for cloud, base in BASES.items()}
        # A real GAP = an HTTP response that is not 200 (e.g. 404). UNREACHABLE
        # (a transient network error, no HTTP response) is NOT a gap — flagging it
        # as one would tell the operator to pin a perfectly valid path to AWS.
        gaps = [c for c in ("gcp", "azure") if row[c] not in (200, UNREACHABLE) and path not in AWS_ONLY_PATHS]
        stalled = [c for c in ("gcp", "azure") if row[c] == UNREACHABLE and path not in AWS_ONLY_PATHS]
        flag = "  <-- GAP" if gaps else ("  <-- UNREACHABLE (retry)" if stalled else "")
        print(f"aws={row['aws']} gcp={row['gcp']} azure={row['azure']}  {path}{flag}")
        if gaps:
            missing.append(path)
        elif stalled:
            unreachable.append(path)

    rc = 0
    if missing:
        print(f"\n{len(missing)} path(s) missing on a non-AWS cloud. Add them to "
              "server.doc_links.AWS_ONLY_PATHS so they fall back to AWS:")
        for p in missing:
            print(f'    "{p}",')
        rc = 1
    if unreachable:
        print(f"\n{len(unreachable)} path(s) could not be checked due to a network error "
              "(timeout/DNS/connection). This is NOT a doc gap — re-run when the network is "
              "stable; do NOT add these to AWS_ONLY_PATHS:")
        for p in unreachable:
            print(f"    {p}")
        rc = rc or 2
    if rc == 0:
        print("\nAll doc paths resolve on aws, gcp, and azure.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
