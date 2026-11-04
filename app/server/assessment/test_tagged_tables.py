"""Metadata pillar: tagged tables are counted by fully-qualified name (#92).

Runs the App's actual SQL against an in-memory SQLite ``table_tags`` (with a
``concat_ws`` shim) so the test checks counting semantics, not just query shape,
and asserts the skill's documented query uses the same FQN form.
"""
import pathlib
import sqlite3
import unittest

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


if __name__ == "__main__":
    unittest.main()
