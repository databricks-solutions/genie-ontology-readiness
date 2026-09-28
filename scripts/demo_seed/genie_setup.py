#!/usr/bin/env python3
"""Create, curate and exercise a Genie space over the seeded catalog.

The Genie Agents pillar is counted from `system.access.audit` (service_name
'aibiGenie'): the app never reads a space's curation, it just counts distinct/
active space_ids in the audit log. So what lights the pillar is a space that
EXISTS and has been ASKED questions (which emits audit events). We still curate
the space fully (instructions + sample questions + verified example SQL) so it is
a realistic, high-quality agent.

Idempotent: a run without `--space-id` first looks up an existing space by its
stable display name (TITLE) via the Genie list API and, if found, **re-curates
that space** rather than creating a new one — so repeated runs converge on a
single space. It only creates a new space when none with that title exists.

Space create/curate uses the SDK (`w.genie.create_space` / `w.genie.update_space`);
`serialized_space` is a JSON string whose shape we build in
`build_serialized_space()` (learned from a live space on this workspace).

    python3 genie_setup.py                       # find-or-create + curate + ask
    python3 genie_setup.py --space-id <id>       # curate + ask a specific space

NOTE: system.access.audit ingestion LAGS (minutes, sometimes hours), so the
Genie pillar may not reflect this activity immediately.
"""

import argparse
import json
import sys
import uuid

from _dbx import DEFAULT_CATALOG, DEFAULT_PROFILE, DEFAULT_WAREHOUSE, get_client

# Stable display name — the idempotence key. Repeated runs reuse the space with
# this exact title instead of creating duplicates.
TITLE = "Ontology Demo — Sales, Finance & Operations"
INSTRUCTIONS = (
    "SCOPE. This space covers a governed star schema across sales, finance and "
    "operations. Facts: sales.fact_orders, finance.fact_payments, "
    "finance.fact_gl_entries, operations.fact_shipments, operations.fact_inventory. "
    "Dimensions: sales.dim_customer, sales.dim_product, finance.dim_account, "
    "operations.dim_warehouse, reference.dim_date, reference.dim_geography. "
    "DEFINITIONS. 'Revenue' means SUM(net_amount) from sales.fact_orders unless "
    "stated otherwise; 'gross revenue' is SUM(gross_amount). Join facts to "
    "reference.dim_date on date_key for any time grouping. Prefer the marts metric "
    "views (mv_sales_orders, mv_customer_revenue, mv_product_performance, "
    "mv_payments, mv_gl_summary, mv_shipments_sla, mv_inventory) for standard KPIs."
)
SAMPLE_QUESTIONS = [
    "What is total net revenue by customer segment?",
    "Show monthly net revenue for 2024.",
    "Which product categories sell the most units?",
    "What is the on-time shipment rate by warehouse region?",
    "What is total payment value by payment method?",
]


def description_for(catalog: str) -> str:
    return (
        f"Analytics over the {catalog} governed star schema. Ask about revenue, "
        "orders, payments, shipments and inventory."
    )


def _tbl(identifier: str, description: str, cols: dict | None = None) -> dict:
    t = {"identifier": identifier, "description": [description]}
    if cols:
        t["column_configs"] = [{"column_name": c, "description": [d]} for c, d in cols.items()]
    return t


def _text(content: str) -> dict:
    return {"id": uuid.uuid4().hex, "content": [content]}


def _sample(q: str) -> dict:
    return {"id": uuid.uuid4().hex, "question": [q]}


def _example_sql(q: str, sql: str) -> dict:
    return {"id": uuid.uuid4().hex, "question": [q], "sql": [sql]}


def build_serialized_space(c: str) -> str:
    tables = [
        _tbl(
            f"{c}.sales.fact_orders",
            "Order line fact: quantity, price, discount and gross/net amounts.",
            {"net_amount": "Net revenue after discount (USD).", "order_status": "Fulfilled/Open/Processing/Cancelled."},
        ),
        _tbl(f"{c}.sales.dim_customer", "Customer master with market segment and home geography."),
        _tbl(f"{c}.sales.dim_product", "Product master with category and unit price."),
        _tbl(f"{c}.finance.fact_payments", "Customer payment fact: amount, method and settlement status."),
        _tbl(f"{c}.finance.dim_account", "Chart of accounts."),
        _tbl(f"{c}.finance.fact_gl_entries", "General-ledger entries by account and date."),
        _tbl(f"{c}.operations.fact_shipments", "Shipment fact with ship duration, units and on-time flag."),
        _tbl(f"{c}.operations.dim_warehouse", "Warehouse/distribution-centre dimension."),
        _tbl(f"{c}.operations.fact_inventory", "Daily inventory positions by product and warehouse."),
        _tbl(f"{c}.reference.dim_date", "Conformed date dimension (2023-2024)."),
        _tbl(f"{c}.reference.dim_geography", "Conformed geography dimension."),
    ]
    example_sqls = [
        _example_sql(
            "What is total net revenue by customer segment?",
            f"SELECT dc.segment, SUM(fo.net_amount) AS net_revenue\n"
            f"FROM {c}.sales.fact_orders fo\n"
            f"JOIN {c}.sales.dim_customer dc ON fo.customer_key = dc.customer_key\n"
            f"GROUP BY dc.segment ORDER BY net_revenue DESC",
        ),
        _example_sql(
            "Show monthly net revenue for 2024.",
            f"SELECT dd.month_num, SUM(fo.net_amount) AS net_revenue\n"
            f"FROM {c}.sales.fact_orders fo\n"
            f"JOIN {c}.reference.dim_date dd ON fo.date_key = dd.date_key\n"
            f"WHERE dd.year = 2024\n"
            f"GROUP BY dd.month_num ORDER BY dd.month_num",
        ),
        _example_sql(
            "What is the on-time shipment rate by warehouse region?",
            f"SELECT dw.region, AVG(CASE WHEN fs.on_time_flag THEN 1.0 ELSE 0.0 END) AS on_time_rate\n"
            f"FROM {c}.operations.fact_shipments fs\n"
            f"JOIN {c}.operations.dim_warehouse dw ON fs.warehouse_key = dw.warehouse_key\n"
            f"GROUP BY dw.region ORDER BY on_time_rate DESC",
        ),
    ]
    # The Genie export proto requires data_sources.tables sorted by identifier.
    tables.sort(key=lambda t: t["identifier"])
    space = {
        "version": 1,
        "config": {"sample_questions": [_sample(q) for q in SAMPLE_QUESTIONS]},
        "data_sources": {"tables": tables},
        "instructions": {
            "text_instructions": [_text(INSTRUCTIONS)],
            "example_question_sqls": example_sqls,
        },
    }
    return json.dumps(space)


# Very large ceiling purely to prevent an unbounded loop if the API kept returning
# a next_page_token forever. Reaching it is treated as an ERROR (we can't prove the
# space is absent), never as "not found" — so we never create a duplicate on doubt.
_MAX_LIST_PAGES = 100_000


def find_space_by_title(w, title: str) -> str | None:
    """Return the space_id of the first space whose display name equals `title`.

    Pages through the Genie list API to completion (until `next_page_token` is
    absent). Returns None ONLY after a full, successful scan finds no match — that
    is the sole signal the caller may use to create. Any list_spaces error
    propagates (fail closed), and exhausting the page ceiling raises rather than
    returning None, so an incomplete scan can never be mistaken for "not found"."""
    page_token = None
    for _ in range(_MAX_LIST_PAGES):
        resp = w.genie.list_spaces(page_token=page_token) if page_token else w.genie.list_spaces()
        for s in getattr(resp, "spaces", None) or []:
            if getattr(s, "title", None) == title:
                return getattr(s, "space_id", None)
        page_token = getattr(resp, "next_page_token", None)
        if not page_token:
            return None  # full scan completed, no match → safe to create
    raise RuntimeError(
        f"list_spaces pagination exceeded {_MAX_LIST_PAGES} pages without exhausting "
        "the token; refusing to create a space that might already exist."
    )


def curate_space(w, sid, catalog, warehouse_id) -> str:
    """(Re)curate an existing space via update_space. Raises on failure so a failed
    curation is never mistaken for a successful reuse. Returns the space_id."""
    w.genie.update_space(
        sid,
        serialized_space=build_serialized_space(catalog),
        title=TITLE,
        description=description_for(catalog),
        warehouse_id=warehouse_id,
    )
    return sid


def create_or_update_space(w, catalog, warehouse_id):
    """Find-or-create the space by its stable title, then (re)curate it. Idempotent.

    FAIL CLOSED: the lookup (find_space_by_title) is NOT wrapped — any list error or
    incomplete scan propagates, so a transient failure never leads to creating a
    duplicate. A re-curation (update_space) failure on an existing space also
    propagates. Returns space_id on success, or None only when create_space could
    not create one (e.g. the API is disabled on this workspace)."""
    existing = find_space_by_title(w, TITLE)  # may raise → caller exits non-zero, no create
    if existing:
        curate_space(w, existing, catalog, warehouse_id)  # may raise → non-zero, no false success
        print(f"  reused + re-curated existing space {existing}")
        return existing
    try:
        space = w.genie.create_space(
            warehouse_id=warehouse_id,
            serialized_space=build_serialized_space(catalog),
            title=TITLE,
            description=description_for(catalog),
        )
        sid = getattr(space, "space_id", None) or getattr(space, "id", None)
        print(f"  created + curated space {sid}")
        return sid
    except Exception as e:
        print(f"  create_space failed: {str(e)[:300]}")
        return None


def ask(w, sid, question) -> str | None:
    """Start a conversation and wait for the answer. Emits aibiGenie audit events.
    Returns the terminal status string (e.g. 'COMPLETED', 'FAILED'), or None if the
    request itself raised. Only 'COMPLETED' means the question was answered."""
    try:
        msg = w.genie.start_conversation_and_wait(space_id=sid, content=question)
    except Exception as e:
        print(f"    ask errored: {str(e)[:200]}")
        return None
    status_obj = getattr(msg, "status", None)
    status = getattr(status_obj, "value", None) or (str(status_obj) if status_obj is not None else None)
    text = ""
    for a in getattr(msg, "attachments", None) or []:
        t = getattr(a, "text", None)
        if t is not None:
            text = getattr(t, "content", "") or ""
            break
    detail = text[:160] if text else "(query/result attachment)"
    print(f"    -> status={status}; answer: {detail if status == 'COMPLETED' else '(not answered)'}")
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--warehouse-id", default=DEFAULT_WAREHOUSE)
    ap.add_argument("--catalog", default=DEFAULT_CATALOG)
    ap.add_argument("--space-id", help="Curate/ask a specific space instead of find-or-create.")
    ap.add_argument("--rounds", type=int, default=1, help="Times to ask the sample questions.")
    args = ap.parse_args()

    w = get_client(args.profile)
    # Fail closed: any error looking up or (re)curating a space exits non-zero
    # WITHOUT creating anything — a transient list/auth error or a failed curation
    # must never produce a duplicate space or a false success.
    try:
        if args.space_id:
            # Explicit space: (re)curate it too, so --space-id stays idempotent.
            sid = curate_space(w, args.space_id, args.catalog, args.warehouse_id)
            print(f"  re-curated space {sid}")
        else:
            print("Finding-or-creating Genie space via SDK…")
            sid = create_or_update_space(w, args.catalog, args.warehouse_id)
    except Exception as e:
        print(f"\nGenie space setup FAILED (fail-closed, no space created): {str(e)[:300]}")
        sys.exit(1)
    if not sid:
        print("\n" + "!" * 62)
        print("Could not create a Genie space via API on this workspace.")
        print("Create it in the UI (see README → 'Genie space'), then re-run:")
        print("  python3 genie_setup.py --space-id <SPACE_ID>")
        print("!" * 62)
        sys.exit(1)

    print(f"\nAsking {len(SAMPLE_QUESTIONS)} question(s) x {args.rounds} round(s) on space {sid}…")
    completed = failed = 0
    for _ in range(args.rounds):
        for q in SAMPLE_QUESTIONS:
            print(f"  Q: {q}")
            if ask(w, sid, q) == "COMPLETED":
                completed += 1
            else:
                failed += 1
    print(f"\nDONE. Space id: {sid}")
    print(f"Space URL: {w.config.host}/genie/rooms/{sid}")
    print(f"Answered COMPLETED: {completed}; not-answered: {failed}.")
    print("system.access.audit (aibiGenie) ingestion may lag a few hours.")
    # Non-zero exit if any question failed to complete, so automation can't read a
    # FAILED/CANCELLED answer as success.
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
