#!/usr/bin/env python3
"""Seed the Genie Ontology readiness demo catalog.

Runs the numbered SQL files in ./sql in order against a SQL warehouse, then prints
a per-pillar verification summary. Everything is idempotent — safe to re-run.

Usage:
    python3 seed.py                         # uses defaults (see _dbx.py)
    python3 seed.py --catalog my_demo \\
        --profile my-profile --warehouse-id abc123
    python3 seed.py --only 07_metric_views  # run a single step

Auth is read from the Databricks CLI profile; no tokens are stored in this repo.
"""

import argparse
import re
import sys
from pathlib import Path

from _dbx import (
    DEFAULT_CATALOG,
    DEFAULT_PROFILE,
    DEFAULT_WAREHOUSE,
    get_client,
    run_sql,
    scalar,
    SqlError,
)

SQL_DIR = Path(__file__).parent / "sql"
SEP = re.compile(r"^--\s*@@\s*$", re.MULTILINE)

# Substrings that mark an error as benign for idempotent re-runs (e.g. adding a
# constraint that a prior run already added). Statements that hit these are
# reported as "ok (already applied)" rather than failing the seed.
BENIGN = (
    "already exists",
    "already been added",
    "constraint with the same name",
    "cannot be found",  # DROP ... IF EXISTS races on some runtimes
)


def statements(sql_text: str, catalog: str):
    """Yield executable statements from a SQL file, splitting on `-- @@` lines,
    substituting {{CATALOG}}, and skipping comment-only chunks."""
    for chunk in SEP.split(sql_text):
        code = "\n".join(ln for ln in chunk.splitlines() if ln.strip() and not ln.strip().startswith("--"))
        if not code.strip():
            continue
        yield chunk.replace("{{CATALOG}}", catalog).strip()


def run_file(w, warehouse_id: str, path: Path, catalog: str) -> tuple[int, int, int]:
    text = path.read_text()
    ok = benign = failed = 0
    stmts = list(statements(text, catalog))
    print(f"\n=== {path.name} ({len(stmts)} statements) ===")
    for i, stmt in enumerate(stmts, 1):
        head = " ".join(stmt.split())[:70]
        try:
            run_sql(w, warehouse_id, stmt)
            ok += 1
            print(f"  [{i}/{len(stmts)}] ok   {head}")
        except SqlError as e:
            msg = str(e).lower()
            if any(b in msg for b in BENIGN):
                benign += 1
                print(f"  [{i}/{len(stmts)}] skip {head}  (already applied)")
            else:
                failed += 1
                print(f"  [{i}/{len(stmts)}] FAIL {head}\n        -> {e}")
    return ok, benign, failed


def verify(w, warehouse_id: str, catalog: str):
    print("\n" + "=" * 62)
    print("VERIFICATION")
    print("=" * 62)
    q = {
        "Schemas": f"SELECT COUNT(*) FROM {catalog}.information_schema.schemata WHERE schema_name<>'information_schema'",
        "Tables (managed)": f"SELECT COUNT(*) FROM {catalog}.information_schema.tables WHERE table_schema<>'information_schema' AND table_type IN ('MANAGED','MANAGED_SHALLOW_CLONE')",
        "Tables commented %": f"SELECT ROUND(100*AVG(CASE WHEN comment IS NOT NULL AND comment<>'' THEN 1 ELSE 0 END),1) FROM {catalog}.information_schema.tables WHERE table_schema<>'information_schema'",
        "Columns commented %": f"SELECT ROUND(100*AVG(CASE WHEN comment IS NOT NULL AND comment<>'' THEN 1 ELSE 0 END),1) FROM {catalog}.information_schema.columns WHERE table_schema<>'information_schema'",
        "Primary keys": f"SELECT COUNT(*) FROM {catalog}.information_schema.table_constraints WHERE constraint_type='PRIMARY KEY'",
        "Foreign keys": f"SELECT COUNT(*) FROM {catalog}.information_schema.table_constraints WHERE constraint_type='FOREIGN KEY'",
        "Metric views": f"SELECT COUNT(*) FROM {catalog}.information_schema.tables WHERE table_type='METRIC_VIEW'",
        "Metric views described %": f"SELECT ROUND(100*AVG(CASE WHEN comment IS NOT NULL AND trim(comment)<>'' THEN 1 ELSE 0 END),1) FROM {catalog}.information_schema.tables WHERE table_type='METRIC_VIEW'",
        "Tagged tables": f"SELECT COUNT(DISTINCT table_name) FROM {catalog}.information_schema.table_tags",
        "Distinct domains (tag)": f"SELECT COUNT(DISTINCT tag_value) FROM {catalog}.information_schema.table_tags WHERE lower(tag_name) IN ('domain','data_domain','business_domain','subject_area','data_product')",
        "Stewarded tables": f"SELECT COUNT(DISTINCT table_name) FROM {catalog}.information_schema.table_tags WHERE lower(tag_name)='steward'",
        "Certified tables": f"SELECT COUNT(DISTINCT table_name) FROM {catalog}.information_schema.table_tags WHERE lower(tag_name)='certification_status' AND lower(tag_value)='certified'",
    }
    for label, sql in q.items():
        try:
            print(f"  {label:28s}: {scalar(w, warehouse_id, sql)}")
        except SqlError as e:
            print(f"  {label:28s}: ERROR {e}")


def main():
    ap = argparse.ArgumentParser(description="Seed the Genie Ontology readiness demo catalog.")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--warehouse-id", default=DEFAULT_WAREHOUSE)
    ap.add_argument("--catalog", default=DEFAULT_CATALOG)
    ap.add_argument("--only", help="Run just one SQL file (basename, with or without .sql).")
    ap.add_argument("--skip-verify", action="store_true")
    args = ap.parse_args()

    print(f"profile={args.profile}  warehouse={args.warehouse_id}  catalog={args.catalog}")
    w = get_client(args.profile)

    files = sorted(SQL_DIR.glob("*.sql"))
    if args.only:
        stem = args.only.replace(".sql", "")
        files = [f for f in files if f.stem == stem]
        if not files:
            sys.exit(f"No SQL file matches --only {args.only!r}")

    totals = [0, 0, 0]
    for path in files:
        ok, benign, failed = run_file(w, args.warehouse_id, path, args.catalog)
        totals[0] += ok
        totals[1] += benign
        totals[2] += failed

    print(f"\nTOTAL: {totals[0]} ok, {totals[1]} already-applied, {totals[2]} failed")
    if not args.skip_verify and not args.only:
        verify(w, args.warehouse_id, args.catalog)
    sys.exit(1 if totals[2] else 0)


if __name__ == "__main__":
    main()
