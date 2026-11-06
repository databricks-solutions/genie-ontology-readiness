"""Metadata pillar: column comment coverage is scanned in catalog batches (#112).

The App and the skill run the same per-batch SQL (``signal_spec`` ``column_comments``)
with the same batch size, so a large metastore never gets one metastore-wide column
scan from either path.
"""
import asyncio
import pathlib
import re
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

from server import signal_spec as S
from server.assessment import probes

SKILL_SIGNALS = (
    pathlib.Path(__file__).resolve().parents[3]
    / "skills" / "genie-ontology-readiness" / "references" / "pillar-signals.md"
)


def _batch_catalogs(sql: str) -> list[str]:
    m = re.search(r"table_catalog IN \(([^)]*)\)", sql)
    return re.findall(r"'([^']*)'", m.group(1))


class ColumnBatchSqlTest(unittest.TestCase):
    def test_app_runs_the_spec_query(self):
        sql = probes.column_coverage_batch_sql(["a", "b"], True)
        expected = S.render_sql(S.METADATA["queries_by_id"]["column_comments"]["sql"],
                                {"<COLUMNS>": "system.information_schema.columns",
                                 "<CATALOG_BATCH>": "'a', 'b'"},
                                schema_filter=probes.schema_predicate(None)).rstrip(";")
        self.assertEqual(sql, expected)
        self.assertTrue(S.METADATA["queries_by_id"]["column_comments"].get("shared"))

    def test_catalog_names_match_in_lower_case(self):
        # information_schema stores table_catalog in lower case; a configured "Sales"
        # must not silently match nothing.
        for system_ok in (True, False):
            sql = probes.column_coverage_batch_sql(["Sales"], system_ok)
            self.assertIn("table_catalog IN ('sales')", sql)

    def test_per_catalog_mode_unions_only_the_batch(self):
        sql = probes.column_coverage_batch_sql(["a", "b"], False)
        self.assertIn("SELECT * FROM `a`.information_schema.columns UNION ALL "
                      "SELECT * FROM `b`.information_schema.columns", sql)
        self.assertNotIn("system.information_schema", sql)

    def test_counts_are_right(self):
        rows = [("a", "s1", "c1", "x"), ("a", "s1", "c2", None), ("a", "s2", "c3", ""),
                ("b", "s1", "c4", "y"), ("c", "s1", "c5", "z"),           # c: not in batch
                ("a", "information_schema", "c6", "w")]                    # excluded schema
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE columns (table_catalog, table_schema, column_name, comment)")
        con.executemany("INSERT INTO columns VALUES (?, ?, ?, ?)", rows)
        sql = probes.column_coverage_batch_sql(["a", "b"], True).replace(
            "system.information_schema.columns", "columns")
        got = sorted(con.execute(sql).fetchall())
        self.assertEqual(got, [("a", "s1", 2, 1), ("a", "s2", 1, 0), ("b", "s1", 1, 1)])


class ChunkedScanTest(unittest.TestCase):
    def _scan(self, n_catalogs, fail=()):
        calls = []

        async def fake(sql, record=True):
            cats = _batch_catalogs(sql)
            calls.append(cats)
            if set(cats) & set(fail):
                raise RuntimeError("PERMISSION_DENIED")
            return [{"cat": c, "sch": "s", "total": "10", "commented": "4"} for c in cats]

        catalogs = [f"cat{i:03d}" for i in range(n_catalogs)]
        with patch.object(probes, "execute_sql", AsyncMock(side_effect=fake)), \
             patch.object(probes, "record_query"), patch.object(probes, "_emit_progress"):
            out = asyncio.run(probes._column_coverage_chunked(catalogs, True))
        return out, calls

    def test_batches_use_the_spec_size(self):
        (total, commented, per_schema, failed), calls = self._scan(60)
        self.assertEqual([len(c) for c in calls].count(S.METADATA_COLUMN_BATCH_SIZE), 2)
        self.assertEqual(sorted(len(c) for c in calls), [10, 25, 25])
        self.assertEqual((total, commented, len(per_schema), failed), (600, 240, 60, 0))

    def test_a_failed_batch_is_left_out_not_counted_as_zero(self):
        (total, commented, _, failed), _ = self._scan(60, fail=("cat000",))  # first batch: 25
        self.assertEqual((total, commented, failed), (350, 140, 25))  # the other 35 catalogs

    def test_every_batch_failing_raises(self):
        with self.assertRaises(RuntimeError):
            self._scan(30, fail=("cat000", "cat025"))

    def test_app_constants_come_from_the_spec(self):
        self.assertEqual(probes._METADATA_BATCH_SIZE, S.METADATA_COLUMN_BATCH_SIZE)
        self.assertEqual(probes._METADATA_NO_PROGRESS_TIMEOUT, S.METADATA_NO_PROGRESS_TIMEOUT_S)


class PartialCoverageDisclosureTest(unittest.TestCase):
    """When some batches fail, the App says so, like the skill's procedure (#112)."""

    def _probe(self, failed):
        sources = {"system_ok": True, "catalogs": [f"c{i}" for i in range(40)], "schemas": None}
        trows = [{"cat": "c0", "sch": "s", "total": "10", "commented": "5"}]
        with patch.object(probes, "_resolve_sources", AsyncMock(return_value=sources)), \
             patch.object(probes, "execute_sql", AsyncMock(return_value=trows)), \
             patch.object(probes, "_column_coverage_chunked", AsyncMock(return_value=(100, 30, {}, failed))), \
             patch.object(probes, "_scalar", AsyncMock(return_value=1)):
            return asyncio.run(probes.probe_metadata())

    def test_partial_scan_is_disclosed(self):
        out = self._probe(failed=15)
        self.assertEqual(out["note"], "Column coverage over 25 of 40 catalogs (15 could not be read).")
        col = next(s for s in out["signals"] if s["label"] == "Columns commented")
        self.assertIn("(over 25 of 40 catalogs)", col["detail"])

    def test_full_scan_has_no_note(self):
        out = self._probe(failed=0)
        self.assertIsNone(out["note"])


class SkillDocumentsBatchingTest(unittest.TestCase):
    def test_skill_has_the_batched_procedure_and_no_full_column_scan(self):
        text = SKILL_SIGNALS.read_text(encoding="utf-8")
        self.assertIn("WHERE table_catalog IN (<CATALOG_BATCH>)", text)
        self.assertIn(f"more than **{S.METADATA_BATCH_MAX_TABLES:,}** tables is a batch on its own", text)
        # Genie once covered "the rest" with one NOT IN query, an unbounded scan; the
        # procedure must forbid it explicitly (#112).
        self.assertIn("Never use `NOT IN`", text)
        # The column view is read ONLY through the batched query.
        self.assertEqual(text.count("FROM <COLUMNS>"), 1)


if __name__ == "__main__":
    unittest.main()
