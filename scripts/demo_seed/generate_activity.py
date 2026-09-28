#!/usr/bin/env python3
"""Generate query activity against the seeded catalog.

Runs a batch of varied SELECT / aggregate / join / metric-view queries so that
`system.query.history`, `system.access.audit` and `system.access.table_lineage`
populate — the signals behind the Adoption & Activity pillar (and the "top
accessed / certified" signal in Domains).

    python3 generate_activity.py --rounds 3

NOTE: system-table ingestion LAGS (often minutes, up to a few hours), so the
Adoption pillar may not reflect this activity immediately.
"""

import argparse

from _dbx import (
    DEFAULT_CATALOG,
    DEFAULT_PROFILE,
    DEFAULT_WAREHOUSE,
    get_client,
    run_sql,
    SqlError,
)


def queries(c: str) -> list[str]:
    return [
        # --- table scans / aggregates ---
        f"SELECT order_status, COUNT(*) orders, SUM(net_amount) net FROM {c}.sales.fact_orders GROUP BY order_status ORDER BY net DESC",
        f"SELECT segment, COUNT(*) FROM {c}.sales.dim_customer GROUP BY segment",
        f"SELECT category, ROUND(AVG(unit_price),2) avg_price FROM {c}.sales.dim_product GROUP BY category ORDER BY avg_price DESC",
        f"SELECT payment_method, payment_status, COUNT(*), SUM(amount) FROM {c}.finance.fact_payments GROUP BY payment_method, payment_status",
        f"SELECT account_type, SUM(amount) FROM {c}.finance.fact_gl_entries g JOIN {c}.finance.dim_account a ON g.account_key=a.account_key GROUP BY account_type",
        f"SELECT region, COUNT(*) shipments, ROUND(AVG(ship_days),2) avg_days FROM {c}.operations.fact_shipments s JOIN {c}.operations.dim_warehouse w ON s.warehouse_key=w.warehouse_key GROUP BY region",
        f"SELECT p.category, SUM(i.on_hand_qty) on_hand FROM {c}.operations.fact_inventory i JOIN {c}.sales.dim_product p ON i.product_key=p.product_key GROUP BY p.category",
        # --- star-schema joins ---
        f"SELECT d.year, d.month_num, SUM(o.net_amount) net FROM {c}.sales.fact_orders o JOIN {c}.reference.dim_date d ON o.date_key=d.date_key GROUP BY d.year, d.month_num ORDER BY d.year, d.month_num",
        f"SELECT c2.segment, SUM(o.net_amount) net FROM {c}.sales.fact_orders o JOIN {c}.sales.dim_customer c2 ON o.customer_key=c2.customer_key GROUP BY c2.segment ORDER BY net DESC",
        f"SELECT g.country, SUM(o.net_amount) net FROM {c}.sales.fact_orders o JOIN {c}.sales.dim_customer c2 ON o.customer_key=c2.customer_key JOIN {c}.reference.dim_geography g ON c2.geo_key=g.geo_key GROUP BY g.country ORDER BY net DESC LIMIT 10",
        # --- gold-layer reads ---
        f"SELECT * FROM {c}.marts.gold_daily_sales ORDER BY report_date DESC LIMIT 30",
        f"SELECT segment, ROUND(AVG(lifetime_net_revenue),2) FROM {c}.marts.gold_customer_360 GROUP BY segment",
        # --- metric-view queries (MEASURE) ---
        f"SELECT `Order Status`, MEASURE(`Net Revenue`) net, MEASURE(`Order Count`) n FROM {c}.marts.mv_sales_orders GROUP BY ALL ORDER BY net DESC",
        f"SELECT `Customer Segment`, MEASURE(`Revenue per Customer`) rpc FROM {c}.marts.mv_customer_revenue GROUP BY ALL ORDER BY rpc DESC",
        f"SELECT `Category`, MEASURE(`Units Sold`) u FROM {c}.marts.mv_product_performance GROUP BY ALL ORDER BY u DESC",
        f"SELECT `Warehouse Region`, MEASURE(`On-Time Rate`) otr FROM {c}.marts.mv_shipments_sla GROUP BY ALL ORDER BY otr DESC",
        f"SELECT `Payment Method`, MEASURE(`Total Paid`) tp FROM {c}.marts.mv_payments GROUP BY ALL ORDER BY tp DESC",
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--warehouse-id", default=DEFAULT_WAREHOUSE)
    ap.add_argument("--catalog", default=DEFAULT_CATALOG)
    ap.add_argument("--rounds", type=int, default=3, help="Times to run the query batch.")
    args = ap.parse_args()

    w = get_client(args.profile)
    qs = queries(args.catalog)
    ok = fail = 0
    for r in range(1, args.rounds + 1):
        print(f"\n=== round {r}/{args.rounds} ({len(qs)} queries) ===")
        for i, q in enumerate(qs, 1):
            try:
                rows = run_sql(w, args.warehouse_id, q)
                ok += 1
                print(f"  [{i}/{len(qs)}] ok ({len(rows)} rows)")
            except SqlError as e:
                fail += 1
                print(f"  [{i}/{len(qs)}] FAIL {e}")
    print(f"\nDONE: {ok} queries ok, {fail} failed.")
    print("system.query.history / system.access.audit ingestion may lag a few hours.")


if __name__ == "__main__":
    main()
