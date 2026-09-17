#!/usr/bin/env python3
"""Cross-cloud coverage check for the app's documentation links.

Every embedded docs link is authored for AWS
(``https://docs.databricks.com/aws/en/<path>``) and rewritten per-cloud at serve
time by ``server.doc_links``. That rewrite assumes each page also exists on GCP
(``docs.databricks.com/gcp/en/<path>``) and Azure
(``learn.microsoft.com/en-us/azure/databricks/<path>``). This script verifies that
assumption over the network so a divergence is caught before a release rather than
becoming a broken link in a deployed app.

It is NOT part of the hermetic ``scripts/check.sh`` gate (that runs offline). Run
it manually before cutting a release:

    python3 scripts/verify_doc_links.py

Any non-AWS page that is missing (and not already listed in
``server.doc_links.AWS_ONLY_PATHS``) is reported and the script exits non-zero.
Add such paths to ``AWS_ONLY_PATHS`` so they fall back to AWS.
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


def _status(url: str) -> int:
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
                return 0
        return e.code
    except Exception:
        return 0


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
    for path in sorted(paths):
        row = {cloud: _status(base + path) for cloud, base in BASES.items()}
        gaps = [c for c in ("gcp", "azure") if row[c] != 200 and path not in AWS_ONLY_PATHS]
        flag = "  <-- GAP" if gaps else ""
        print(f"aws={row['aws']} gcp={row['gcp']} azure={row['azure']}  {path}{flag}")
        if gaps:
            missing.append(path)

    if missing:
        print(f"\n{len(missing)} path(s) missing on a non-AWS cloud. Add them to "
              "server.doc_links.AWS_ONLY_PATHS so they fall back to AWS:")
        for p in missing:
            print(f'    "{p}",')
        return 1
    print("\nAll doc paths resolve on aws, gcp, and azure.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
