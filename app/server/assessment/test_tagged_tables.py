"""Metadata pillar: tagged tables are counted by fully-qualified name (#92).

Runs the App's actual SQL against an in-memory SQLite ``table_tags`` (with a
``concat_ws`` shim) so the test checks counting semantics, not just query shape,
and asserts the skill's documented query uses the same FQN form.
"""
import pathlib
import sqlite3
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server.assessment import probes

SKILL_SIGNALS = (
    pathlib.Path(__file__).resolve().parents[3]
    / "skills" / "genie-ontology-readiness" / "references" / "pillar-signals.md"
)

_ROWS = [
    # same table name in two schemas of one catalog
    ("main", "sales", "dim_customer", "pii"),
    ("main", "finance", "dim_customer", "pii"),
    # same name in a different catalog
    ("other", "sales", "dim_customer", "pii"),
    # one table carrying two tags is still one table
    ("main", "sales", "fact_orders", "pii"),
    ("main", "sales", "fact_orders", "certified"),
    # excluded by the default information_schema predicate
    ("main", "information_schema", "tables", "pii"),
    # internal catalogs: excluded in system mode
    ("system", "access", "audit", "pii"),
    ("__databricks_internal", "x", "y", "pii"),
]


def _run(sql: str):
    con = sqlite3.connect(":memory:")
    con.create_function("concat_ws", -1, lambda sep, *parts: sep.join(str(p) for p in parts if p is not None))
    con.execute("CREATE TABLE table_tags (catalog_name, schema_name, table_name, tag_name)")
    con.executemany("INSERT INTO table_tags VALUES (?, ?, ?, ?)", _ROWS)
    return con.execute(sql).fetchone()[0]


class TaggedTablesByFqnTest(unittest.TestCase):
    def test_same_named_tables_in_different_schemas_count_separately(self):
        sql = probes.tagged_tables_sql("table_tags", probes.schema_predicate(None, "schema_name"))
        self.assertEqual(_run(sql), 6)  # per-catalog mode: no internal-catalog filter

    def test_system_mode_excludes_internal_catalogs(self):
        where = probes.schema_predicate(None, "schema_name") + " " + probes._internal_catalog_filter(
            {"system_ok": True}, "catalog_name")
        sql = probes.tagged_tables_sql("table_tags", where.strip())
        # sqlite has no RLIKE; emulate the '^__' prefix check
        sql = sql.replace("catalog_name NOT RLIKE '^__'", "catalog_name NOT LIKE '\\_\\_%' ESCAPE '\\'")
        self.assertEqual(_run(sql), 4)

    def test_schema_allowlist_is_applied(self):
        sql = probes.tagged_tables_sql("table_tags", probes.schema_predicate(["sales"], "schema_name"))
        self.assertEqual(_run(sql), 3)

    def test_skill_documents_the_same_fqn_count(self):
        text = SKILL_SIGNALS.read_text()
        self.assertNotIn("COUNT(DISTINCT table_name) AS tagged_tables", text)
        self.assertIn(
            "SELECT COUNT(DISTINCT concat_ws('.', catalog_name, schema_name, table_name)) AS tagged_tables",
            text,
        )


class TableScanExcludesInternalCatalogsTest(unittest.TestCase):
    """System mode: the table-comment scan drops __-prefixed internal catalogs (#109)."""

    _TABLES = [
        ("main", "sales", "t1", "described"),
        ("main", "sales", "t2", None),
        ("other", "fin", "t3", ""),
        ("main", "information_schema", "tables", None),        # default schema predicate
        ("system", "access", "audit", None),                    # named internal catalog
        ("__databricks_internal_catalog_ab12", "x", "y", None),  # __-prefixed (#109)
        ("__databricks_internal", "x", "z", None),
    ]

    def _table_scan_sql(self):
        sources = {"system_ok": True, "catalogs": ["main", "other"], "schemas": None}
        with patch.object(probes, "_resolve_sources", AsyncMock(return_value=sources)), \
             patch.object(probes, "execute_sql", AsyncMock(return_value=[])) as ex, \
             patch.object(probes, "_column_coverage_chunked", AsyncMock(return_value=(0, 0, {}, 0))), \
             patch.object(probes, "_scalar", AsyncMock(return_value=None)):
            asyncio.run(probes.probe_metadata())
        return ex.call_args_list[0].args[0]

    def test_internal_catalogs_are_not_counted(self):
        sql = self._table_scan_sql()
        self.assertIn("table_catalog NOT RLIKE '^__'", sql)
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE tables (table_catalog, table_schema, table_name, comment)")
        con.executemany("INSERT INTO tables VALUES (?, ?, ?, ?)", self._TABLES)
        sql = (sql.replace("system.information_schema.tables", "tables")
                  .replace("table_catalog NOT RLIKE '^__'", "table_catalog NOT LIKE '\\_\\_%' ESCAPE '\\'"))
        rows = con.execute(sql).fetchall()
        self.assertEqual(sorted((r[0], r[2]) for r in rows), [("main", 2), ("other", 1)])


class RenderSqlTest(unittest.TestCase):
    def test_substituted_values_are_not_rescanned(self):
        from server.signal_spec import render_sql

        sql = render_sql("FROM <TABLE_TAGS> WHERE {schema_filter}",
                         {"<TABLE_TAGS>": "`odd{schema_filter}cat`.information_schema.table_tags"},
                         schema_filter="schema_name IN ('<TABLE_TAGS>')")
        self.assertEqual(sql, "FROM `odd{schema_filter}cat`.information_schema.table_tags "
                              "WHERE schema_name IN ('<TABLE_TAGS>')")


class MetadataProbeGoldenTest(unittest.TestCase):
    """probe_metadata output is unchanged by moving its rules into signal_spec."""

    def _run(self, tagged, t_total=10, t_com=5, c_com=30):
        sources = {"system_ok": True, "catalogs": ["main"], "schemas": None}
        trows = [{"cat": "main", "sch": "sales", "total": str(t_total), "commented": str(t_com)}] if t_total else []
        with patch.object(probes, "_resolve_sources", AsyncMock(return_value=sources)), \
             patch.object(probes, "execute_sql", AsyncMock(return_value=trows)), \
             patch.object(probes, "_column_coverage_chunked", AsyncMock(return_value=(100, c_com, {}, 0))), \
             patch.object(probes, "_scalar", AsyncMock(return_value=tagged)) as scalar:
            out = asyncio.run(probes.probe_metadata())
        return out, scalar

    def test_score_gaps_and_signals(self):
        out, scalar = self._run(tagged=0)
        self.assertEqual(out["score"], 40.0)
        self.assertEqual(out["gaps"], [
            "Only 50.0% of tables have descriptions — Genie relies on these to understand data.",
            "Only 30.0% of columns are commented; aim for high coverage on gold-layer columns.",
            "No governed tags found; tags aid discovery and domain organization.",
        ])
        self.assertEqual([s["label"] for s in out["signals"]],
                         ["Tables commented", "Columns commented", "Tagged tables"])
        self.assertIn("concat_ws('.', catalog_name, schema_name, table_name)", scalar.call_args.args[0])

    def test_app_gaps_match_the_spec_rules(self):
        """Evaluate each spec ``when`` rule and require the App to emit exactly those gaps."""
        from server.signal_spec import METADATA, gap_text

        for t_total, t_com in ((0, 0), (100, 80), (100, 79)):  # each side of every threshold
            for c_com in (60, 59):
                for tagged in (None, 0, 3):
                    out, _ = self._run(tagged, t_total=t_total, t_com=t_com, c_com=c_com)
                    vals = {"table_total": t_total, "tagged_tables": tagged,
                            "table_pct": out["metrics"]["table_comment_pct"],
                            "col_pct": out["metrics"]["column_comment_pct"]}
                    expected = [gap_text(METADATA, g["id"], **vals) for g in METADATA["gaps"]
                                if eval(g["when"], {}, dict(vals))]  # trusted, repo-owned spec
                    self.assertEqual(out["gaps"], expected, vals)

    def test_signal_labels_come_from_the_spec(self):
        from server.signal_spec import METADATA

        out, _ = self._run(tagged=1)
        self.assertEqual([s["label"] for s in out["signals"]], [s["label"] for s in METADATA["signals"]])

    def test_tags_present_means_no_tag_gap(self):
        out, _ = self._run(tagged=3)
        self.assertEqual(out["metrics"]["tagged_tables"], 3)
        self.assertEqual(len(out["gaps"]), 2)


if __name__ == "__main__":
    unittest.main()
