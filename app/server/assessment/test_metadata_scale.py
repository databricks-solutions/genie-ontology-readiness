"""Metadata scan behavior on large / partly broken metastores (issue #128)."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server.assessment import probes
from server.workspace_filter import set_catalog_scope, set_schema_scope


def _rows_for(batch):
    return [{"cat": c, "sch": "s", "total": 10, "commented": 5} for c in batch]


def _cats_in(query, cats):
    return [c for c in cats if probes.quote_literal(c) in query or probes.quote_ident(c) in query]


class ColumnScanTest(unittest.IsolatedAsyncioTestCase):
    async def test_one_bad_catalog_skips_only_itself(self):
        cats = [f"c{i:02d}" for i in range(30)]  # batches: 25 + 5
        calls = []

        async def fake(query, *a, **k):
            calls.append(query)
            if "c07" in query:
                raise Exception("[FAILED_JDBC.CONNECTION] remote is down")
            return _rows_for(_cats_in(query, cats))

        with patch.object(probes, "execute_sql", AsyncMock(side_effect=fake)), patch.object(probes, "_emit_progress"):
            total, commented, per_schema, skipped = await probes._column_coverage_chunked(cats, system_ok=True)

        self.assertEqual(skipped, ["c07"])
        self.assertEqual(len(per_schema), 29)
        self.assertEqual((total, commented), (290, 145))
        self.assertLess(len(calls), 2 + 12)  # bisection, not one query per catalog

    async def test_a_catalog_too_slow_for_its_budget_does_not_stall_the_scan(self):
        cats = [f"c{i:02d}" for i in range(8)]

        async def fake(query, *a, **k):
            if "huge" in query:
                await asyncio.sleep(0.2)  # each attempt uses its whole budget, then times out
                raise probes.StatementTimeout("SQL query timed out after 230 seconds")
            return _rows_for(_cats_in(query, cats))

        with patch.object(probes, "execute_sql", AsyncMock(side_effect=fake)), \
             patch.object(probes, "_emit_progress"), \
             patch.object(probes, "_METADATA_NO_PROGRESS_TIMEOUT", 0.3):
            _, _, per_schema, skipped = await probes._column_coverage_chunked(cats + ["huge"], system_ok=True)

        self.assertEqual(skipped, ["huge"])
        self.assertEqual(len(per_schema), 8)

    async def test_systemic_failure_stops_early_without_a_retry_storm(self):
        execute = AsyncMock(side_effect=Exception("warehouse down"))
        cats = [f"c{i:03d}" for i in range(500)]  # 20 batches
        with patch.object(probes, "execute_sql", execute), patch.object(probes, "_emit_progress"):
            with self.assertRaises(RuntimeError):
                await probes._column_coverage_chunked(cats, system_ok=False)
        self.assertLessEqual(execute.await_count, 20 + probes._METADATA_SCAN_CONCURRENCY)  # originals + canary, no fan-out

    async def test_scan_queries_get_a_budget_below_the_no_progress_stop(self):
        execute = AsyncMock(return_value=_rows_for(["a"]))
        with patch.object(probes, "execute_sql", execute), patch.object(probes, "_emit_progress"):
            await probes._column_coverage_chunked(["a"], system_ok=True)
        self.assertEqual(execute.await_args.kwargs["poll_seconds"], probes._METADATA_QUERY_POLL_SECONDS)
        self.assertEqual(execute.await_args.kwargs["wait_seconds"], 0)  # cancellable from the start
        self.assertGreater(probes._METADATA_QUERY_POLL_SECONDS, 120)
        # Budget + one bounded final status read (30 s) + the cancel (5 s) < the stall stop.
        self.assertLess(probes._METADATA_QUERY_POLL_SECONDS + 30 + 5, probes._METADATA_NO_PROGRESS_TIMEOUT)

    async def test_per_catalog_union_names_its_columns(self):
        q = probes._coverage_query("columns", ["a", "b"], system_ok=False)
        self.assertNotIn("SELECT *", q)
        self.assertIn("SELECT table_catalog, table_schema, comment FROM `a`.information_schema.columns", q)


class ColumnScanEdgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_one_fast_failing_catalog_in_a_single_batch_does_not_trip_the_breaker(self):
        cats = [f"c{i:02d}" for i in range(25)]  # ONE original batch

        async def fake(query, *a, **k):
            if "c00" in query:
                raise Exception("[CANNOT_ESTABLISH_CONNECTION_SERVERLESS] remote down")  # fails fast
            await asyncio.sleep(0.05)  # good halves are slower
            return _rows_for(_cats_in(query, cats))

        with patch.object(probes, "execute_sql", AsyncMock(side_effect=fake)), patch.object(probes, "_emit_progress"):
            _, _, per_schema, skipped = await probes._column_coverage_chunked(cats, system_ok=True)
        self.assertEqual(skipped, ["c00"])
        self.assertEqual(len(per_schema), 24)

    async def test_a_stall_raises_and_cancels_every_pending_query(self):
        started, cancelled = [], []

        async def hang(query, *a, **k):
            started.append(query)
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.append(query)
                raise

        cats = [f"c{i:03d}" for i in range(60)]  # 3 batches, all hang
        with patch.object(probes, "execute_sql", AsyncMock(side_effect=hang)), \
             patch.object(probes, "_emit_progress"), \
             patch.object(probes, "_METADATA_NO_PROGRESS_TIMEOUT", 0.1):
            with self.assertRaises(TimeoutError):
                await probes._column_coverage_chunked(cats, system_ok=True)
        self.assertEqual(len(started), 3)
        self.assertEqual(sorted(cancelled), sorted(started))


class BreakerAndTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_widespread_failures_mid_scan_keep_collected_data_and_stay_bounded(self):
        cats = [f"c{i:03d}" for i in range(500)]  # 20 batches
        state = {"n": 0}

        async def fake(query, *a, **k):
            state["n"] += 1
            if state["n"] == 1:
                return _rows_for(_cats_in(query, cats))  # first batch fine
            raise Exception("warehouse saturated")      # then everything fails

        execute = AsyncMock(side_effect=fake)
        with patch.object(probes, "execute_sql", execute), patch.object(probes, "_emit_progress"):
            _, _, per_schema, skipped = await probes._column_coverage_chunked(cats, system_ok=True)
        self.assertEqual(len(per_schema), 25)       # the first batch's data is kept
        self.assertEqual(len(skipped), 475)          # the rest is reported, not retried forever
        budget = max(probes._METADATA_RETRY_BUDGET_PER_BATCH * 20, 2 * probes._METADATA_BATCH_SIZE)
        self.assertLessEqual(execute.await_count, 20 + budget)

    async def test_a_timed_out_batch_goes_straight_to_single_catalogs(self):
        cats = [f"c{i:02d}" for i in range(8)] + ["huge"]
        seen = []

        async def fake(query, *a, **k):
            seen.append(len(_cats_in(query, cats)))
            if "huge" in query:
                raise probes.StatementTimeout("SQL query timed out after 230 seconds")
            return _rows_for(_cats_in(query, cats))

        with patch.object(probes, "execute_sql", AsyncMock(side_effect=fake)), patch.object(probes, "_emit_progress"):
            _, _, _, skipped = await probes._column_coverage_chunked(cats, system_ok=True)
        self.assertEqual(skipped, ["huge"])
        self.assertEqual(seen[0], 9)
        self.assertTrue(all(n == 1 for n in seen[1:]))  # no halving rounds of the slow catalog
        self.assertLessEqual(len(seen), 1 + probes._METADATA_SCAN_CONCURRENCY + 9)  # original + canary + singles

    async def test_a_remote_connection_timeout_message_is_split_not_fanned_out(self):
        # A federated "Connection timed out" error is not a statement timeout: halve.
        cats = [f"c{i:02d}" for i in range(8)]
        seen = []

        async def fake(query, *a, **k):
            seen.append(len(_cats_in(query, cats)))
            if "c00" in query:
                raise Exception("[CANNOT_ESTABLISH_CONNECTION] Connection timed out")
            return _rows_for(_cats_in(query, cats))

        with patch.object(probes, "execute_sql", AsyncMock(side_effect=fake)), patch.object(probes, "_emit_progress"), \
             self.assertLogs(probes.logger, "WARNING") as logs:
            _, _, _, skipped = await probes._column_coverage_chunked(cats, system_ok=True)
        self.assertEqual(skipped, ["c00"])
        text = "\n".join(logs.output)
        self.assertNotIn("retrying each alone", text)
        self.assertLessEqual(len(seen), 1 + probes._METADATA_SCAN_CONCURRENCY + 6)


class HeldFailuresTest(unittest.IsolatedAsyncioTestCase):
    async def test_broken_catalogs_clustered_first_do_not_fail_a_healthy_scan(self):
        broken = [f"aa_sf_{i:03d}" for i in range(150)]   # sort first: batches 1-6, all broken
        healthy = [f"zz_{i:03d}" for i in range(100)]
        cats = broken + healthy

        async def fake(query, *a, **k):
            if "aa_sf_" in query:
                raise Exception("[CANNOT_ESTABLISH_CONNECTION_SERVERLESS] remote down")
            return _rows_for(_cats_in(query, cats))

        with patch.object(probes, "execute_sql", AsyncMock(side_effect=fake)), patch.object(probes, "_emit_progress"):
            _, _, per_schema, skipped = await probes._column_coverage_chunked(cats, system_ok=True)
        self.assertEqual(len(per_schema), 100)        # every healthy catalog read
        self.assertEqual(sorted(skipped), sorted(broken))


class CanaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_canary_release_does_not_double_count(self):
        cats = [f"c{i:02d}" for i in range(25)]  # ONE batch; c00 broken

        async def fake(query, *a, **k):
            if "c00" in query:
                raise Exception("[FAILED_JDBC.CONNECTION] remote down")
            return _rows_for(_cats_in(query, cats))

        with patch.object(probes, "execute_sql", AsyncMock(side_effect=fake)), patch.object(probes, "_emit_progress"):
            total, commented, per_schema, skipped = await probes._column_coverage_chunked(cats, system_ok=True)
        self.assertEqual(skipped, ["c00"])
        self.assertEqual(len(per_schema), 24)
        self.assertEqual((total, commented), (240, 120))  # each catalog counted once


class CanaryTimingTest(unittest.IsolatedAsyncioTestCase):
    async def test_canaries_finishing_at_different_times_are_counted_once(self):
        cats = [f"c{i:02d}" for i in range(25)]  # ONE batch; c00 broken

        async def fake(query, *a, **k):
            found = _cats_in(query, cats)
            if "c00" in found:
                raise Exception("[FAILED_JDBC.CONNECTION] remote down")
            if found == ["c24"]:
                await asyncio.sleep(0.05)  # the last canary returns late
            return _rows_for(found)

        with patch.object(probes, "execute_sql", AsyncMock(side_effect=fake)), patch.object(probes, "_emit_progress"):
            total, commented, per_schema, skipped = await probes._column_coverage_chunked(cats, system_ok=True)
        self.assertEqual(skipped, ["c00"])
        self.assertEqual((total, commented, len(per_schema)), (240, 120, 24))


class CanaryFailureOrderTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_canary_failing_in_the_releasing_set_is_not_also_counted(self):
        cats = [f"c{i:02d}" for i in range(25)]  # ONE batch; c00 broken; canary c20 fails ONCE
        flaky = {"c20": 1}

        async def fake(query, *a, **k):
            found = _cats_in(query, cats)
            if "c00" in found:
                raise Exception("[FAILED_JDBC.CONNECTION] remote down")
            if found == ["c20"] and flaky["c20"]:
                flaky["c20"] -= 1
                raise Exception("transient")
            return _rows_for(found)

        with patch.object(probes, "execute_sql", AsyncMock(side_effect=fake)), patch.object(probes, "_emit_progress"):
            total, commented, per_schema, skipped = await probes._column_coverage_chunked(cats, system_ok=True)
        counted = {c for c, _ in per_schema}
        self.assertFalse(counted & set(skipped))           # never both skipped and counted
        self.assertEqual(len(counted) + len(skipped), 25)  # every catalog settled exactly once
        self.assertEqual(total, 10 * len(counted))


class ReadabilityShapeTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        set_catalog_scope(None)
        set_schema_scope(None)

    async def test_lakebase_shaped_catalog_is_excluded_and_reported(self):
        async def fake(query, *a, **k):
            if "lakebase_db" in query and "comment" in query:
                raise Exception("[UNRESOLVED_COLUMN.WITH_SUGGESTION] `comment` cannot be resolved")
            return [{"x": 1}]

        set_catalog_scope(["gold", "lakebase_db"])
        with patch.object(probes, "execute_sql", AsyncMock(side_effect=fake)):
            src = await probes._do_resolve_sources()
        self.assertEqual(src["catalogs"], ["gold"])
        self.assertEqual(src["excluded"], ["lakebase_db"])


class ProbeMetadataScaleTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, n, col_per_schema, skipped, excluded=()):
        sources = {"system_ok": True, "catalogs": [f"cat{i}" for i in range(n)], "schemas": None,
                   "excluded": list(excluded)}
        table_rows = [{"cat": f"cat{i}", "sch": "s", "total": 4, "commented": 2} for i in range(n)]
        with patch.object(probes, "_resolve_sources", AsyncMock(return_value=sources)), \
             patch.object(probes, "_src", return_value="system.information_schema.x"), \
             patch.object(probes, "execute_sql", AsyncMock(return_value=table_rows)), \
             patch.object(probes, "_scalar", AsyncMock(return_value=3)), \
             patch.object(probes, "_column_coverage_chunked",
                          AsyncMock(return_value=(len(col_per_schema) * 10, len(col_per_schema) * 5,
                                                  col_per_schema, list(skipped)))):
            return await probes.probe_metadata()

    async def test_drill_down_is_capped_with_the_total(self):
        n = probes._METADATA_DRILL_MAX_ROWS + 250
        result = await self._run(n, {(f"cat{i}", "s"): (10, 5) for i in range(n)}, [])
        drill = result["drill_down"]
        self.assertEqual(len(drill["rows"]), probes._METADATA_DRILL_MAX_ROWS)
        self.assertEqual(drill["total_rows"], n)
        self.assertIn(f"worst {probes._METADATA_DRILL_MAX_ROWS} of {n}", drill["title"])

    async def test_skipped_catalogs_leave_both_halves_and_the_drill_down(self):
        # 10 catalogs; cat0/cat1 skipped by the column scan, cat9 excluded up front.
        cps = {(f"cat{i}", "s"): (10, 5) for i in range(2, 9)}
        result = await self._run(10, cps, ["cat0", "cat1"], excluded=["cat9"])
        signals = {s["label"]: s for s in result["signals"]}
        self.assertEqual(signals["Tables commented"]["detail"], "16 of 32 tables")  # 8 catalogs x 4
        self.assertEqual(signals["Catalogs skipped"]["value"], 3)
        self.assertIn("cat0, cat1, cat9", signals["Catalogs skipped"]["detail"])
        drill_cats = {r["catalog"] for r in result["drill_down"]["rows"]}
        self.assertFalse(drill_cats & {"cat0", "cat1"})


if __name__ == "__main__":
    unittest.main()
