"""Metadata scan behavior on large / partly broken metastores (issue #128)."""
import unittest
from unittest.mock import AsyncMock, patch

from server.assessment import probes


def _rows_for(batch):
    return [{"cat": c, "sch": "s", "total": 10, "commented": 5} for c in batch]


class BatchRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_one_bad_catalog_skips_only_itself(self):
        cats = [f"c{i:02d}" for i in range(30)]  # batches: 25 + 5

        async def fake(query, *a, **k):
            if "c07" in query:  # c07 breaks any query that includes it
                raise Exception("[FAILED_JDBC.CONNECTION] remote is down")
            batch = [c for c in cats if f"'{c}'" in query or f"`{c}`" in query]
            return _rows_for(batch)

        with patch.object(probes, "execute_sql", AsyncMock(side_effect=fake)), \
             patch.object(probes, "_emit_progress"):
            total, commented, per_schema, skipped = await probes._column_coverage_chunked(cats, system_ok=True)

        self.assertEqual(skipped, ["c07"])
        self.assertEqual(len(per_schema), 29)
        self.assertEqual((total, commented), (290, 145))

    async def test_all_catalogs_failing_still_raises(self):
        with patch.object(probes, "execute_sql", AsyncMock(side_effect=Exception("warehouse down"))), \
             patch.object(probes, "_emit_progress"):
            with self.assertRaises(RuntimeError):
                await probes._column_coverage_chunked(["a", "b", "c"], system_ok=False)

    async def test_scan_queries_get_the_longer_time_budget(self):
        execute = AsyncMock(return_value=_rows_for(["a"]))
        with patch.object(probes, "execute_sql", execute), patch.object(probes, "_emit_progress"):
            await probes._column_coverage_chunked(["a"], system_ok=True)
        self.assertEqual(execute.await_args.kwargs["poll_seconds"], probes._METADATA_QUERY_POLL_SECONDS)
        self.assertGreater(probes._METADATA_QUERY_POLL_SECONDS, 120)
        self.assertLess(probes._METADATA_QUERY_POLL_SECONDS + 50, probes._METADATA_NO_PROGRESS_TIMEOUT + 60)


class ProbeMetadataScaleTest(unittest.IsolatedAsyncioTestCase):
    async def test_drill_down_is_capped_and_skips_are_reported(self):
        n = probes._METADATA_DRILL_MAX_ROWS + 250
        col_per_schema = {(f"cat{i}", "s"): (10, i % 10) for i in range(n)}
        sources = {"system_ok": True, "catalogs": [f"cat{i}" for i in range(n)], "schemas": None}
        table_rows = [{"cat": f"cat{i}", "sch": "s", "total": 4, "commented": 2} for i in range(n)]

        async def fake_sql(query, *a, **k):
            return table_rows

        with patch.object(probes, "_resolve_sources", AsyncMock(return_value=sources)), \
             patch.object(probes, "_src", return_value="system.information_schema.x"), \
             patch.object(probes, "execute_sql", AsyncMock(side_effect=fake_sql)), \
             patch.object(probes, "_scalar", AsyncMock(return_value=3)), \
             patch.object(probes, "_column_coverage_chunked",
                          AsyncMock(return_value=(n * 10, 4500, col_per_schema, ["broken_a", "broken_b"]))):
            result = await probes.probe_metadata()

        drill = result["drill_down"]
        self.assertEqual(len(drill["rows"]), probes._METADATA_DRILL_MAX_ROWS)
        self.assertIn(f"worst {probes._METADATA_DRILL_MAX_ROWS} of {n}", drill["title"])
        skipped = [s for s in result["signals"] if s["label"] == "Catalogs skipped"]
        self.assertEqual(skipped[0]["value"], 2)
        self.assertIn("broken_a, broken_b", skipped[0]["detail"])


if __name__ == "__main__":
    unittest.main()
