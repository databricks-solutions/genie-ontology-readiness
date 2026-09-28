#!/usr/bin/env python3
"""Create, curate and exercise a Genie space over the seeded catalog.

The Genie Agents pillar is counted from `system.access.audit` (service_name
'aibiGenie'): the app never reads a space's curation, it just counts distinct/
active space_ids in the audit log. So what lights the pillar is a space that
EXISTS and has been ASKED questions (which emits audit events). We still curate
the space fully (instructions + sample questions + verified example SQL) so it is
a realistic, high-quality agent.

Space creation uses the SDK `w.genie.create_space(warehouse_id, serialized_space,
title, description)`. `serialized_space` is a JSON string whose shape we build in
`build_serialized_space()` (learned from a live space on this workspace).

    python3 genie_setup.py                       # create + curate + ask
    python3 genie_setup.py --space-id <id>       # ask an existing space only

NOTE: system.access.audit ingestion LAGS (minutes, sometimes hours), so the
Genie pillar may not reflect this activity immediately.
"""

import argparse
import json
import uuid

from _dbx import DEFAULT_CATALOG, DEFAULT_PROFILE, DEFAULT_WAREHOUSE, get_client

TITLE = "Ontology Demo — Sales, Finance & Operations"
DESCRIPTION = (
    "Analytics over the ontology_demo governed star schema. Ask about revenue, "
    "orders, payments, shipments and inventory."
)
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


def create_space(w, catalog, warehouse_id):
    """Create + curate a Genie space via the SDK. Returns space_id or None."""
    serialized = build_serialized_space(catalog)
    try:
        space = w.genie.create_space(
            warehouse_id=warehouse_id,
            serialized_space=serialized,
            title=TITLE,
            description=DESCRIPTION,
        )
        sid = getattr(space, "space_id", None) or getattr(space, "id", None)
        print(f"  created + curated space {sid}")
        return sid
    except Exception as e:
        print(f"  create_space failed: {str(e)[:300]}")
        return None


def ask(w, sid, question):
    """Start a conversation and wait for the answer. Emits aibiGenie audit events."""
    try:
        msg = w.genie.start_conversation_and_wait(space_id=sid, content=question)
    except Exception as e:
        print(f"    ask failed: {str(e)[:200]}")
        return False
    status = getattr(getattr(msg, "status", None), "value", None) or getattr(msg, "status", None)
    attachments = getattr(msg, "attachments", None) or []
    text = ""
    for a in attachments:
        t = getattr(a, "text", None)
        if t is not None:
            text = getattr(t, "content", "") or ""
            break
    print(f"    -> status={status}; answer: {text[:160] if text else '(query/result attachment)'}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--warehouse-id", default=DEFAULT_WAREHOUSE)
    ap.add_argument("--catalog", default=DEFAULT_CATALOG)
    ap.add_argument("--space-id", help="Use an existing space instead of creating one.")
    ap.add_argument("--rounds", type=int, default=1, help="Times to ask the sample questions.")
    args = ap.parse_args()

    w = get_client(args.profile)
    sid = args.space_id
    if not sid:
        print("Creating + curating Genie space via SDK…")
        sid = create_space(w, args.catalog, args.warehouse_id)
    if not sid:
        print("\n" + "!" * 62)
        print("Could not create a Genie space via API on this workspace.")
        print("Create it in the UI (see README → 'Genie space'), then re-run:")
        print("  python3 genie_setup.py --space-id <SPACE_ID>")
        print("!" * 62)
        return

    print(f"\nAsking {len(SAMPLE_QUESTIONS)} question(s) x {args.rounds} round(s) on space {sid}…")
    for _ in range(args.rounds):
        for q in SAMPLE_QUESTIONS:
            print(f"  Q: {q}")
            ask(w, sid, q)
    print(f"\nDONE. Space id: {sid}")
    print(f"Space URL: {w.config.host}/genie/rooms/{sid}")
    print("system.access.audit (aibiGenie) ingestion may lag a few hours.")


if __name__ == "__main__":
    main()
