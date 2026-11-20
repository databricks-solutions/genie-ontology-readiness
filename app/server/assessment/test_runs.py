import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from server.assessment import runs


def _events(chunks: list[str]) -> list[dict]:
    out = []
    for c in chunks:
        if c.startswith("data: ") and "[DONE]" not in c:
            out.append(json.loads(c[len("data: "):]))
    return out


async def _collect(agen) -> list[str]:
    return [c async for c in agen]


class StreamEventsTest(unittest.IsolatedAsyncioTestCase):
    async def test_replays_from_after_and_ends_when_done(self):
        run = runs.AssessmentRun(id="r1", principal="a@x")
        for i in range(3):
            run.append({"type": "pillar_progress", "key": "metadata", "detail": str(i)})
        run.finish()

        chunks = await _collect(runs.stream_events(run, after=1))

        self.assertEqual(chunks[0], ": connected\n\n")
        evs = _events(chunks)
        self.assertEqual([e.get("seq") for e in evs[:-1]], [1, 2])
        self.assertEqual(evs[-1]["type"], "end")
        self.assertEqual(evs[-1]["next"], 3)
        self.assertEqual(chunks[-1], "data: [DONE]\n\n")

    async def test_sends_keepalive_while_idle_then_reconnect_marker(self):
        run = runs.AssessmentRun(id="r2", principal="a@x")
        run.append({"type": "pillar_progress", "key": "metadata", "detail": "x"})

        chunks = await _collect(runs.stream_events(run, after=0, window=0.12, heartbeat=0.03))

        self.assertIn(": keepalive\n\n", chunks)
        last = _events(chunks)[-1]
        self.assertEqual(last, {"type": "reconnect", "next": 1, "elapsed_s": last["elapsed_s"]})
        self.assertNotIn("data: [DONE]\n\n", chunks)

    async def test_wakes_reader_on_new_event(self):
        run = runs.AssessmentRun(id="r3", principal="a@x")

        async def produce():
            await asyncio.sleep(0.02)
            run.append({"type": "pillar_progress", "key": "k", "detail": "late"})
            run.finish()

        producer = asyncio.create_task(produce())
        chunks = await _collect(runs.stream_events(run, after=0, window=5, heartbeat=5))
        await producer

        evs = _events(chunks)
        self.assertEqual(evs[0]["detail"], "late")
        self.assertEqual(evs[-1]["type"], "end")


class RunLifecycleTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        runs._runs.clear()

    async def test_failed_run_reports_reference_and_progress(self):
        async def boom():
            yield {"type": "pillar", "pillar": {"key": "unity_catalog"}}
            raise RuntimeError("SELECT secret_column FROM t failed")

        with patch.object(runs, "run_assessment_stream", boom):
            run = runs.start_run("a@x", None, None, None, None)
            await run.task

        err = run.events[-1]
        self.assertTrue(run.done)
        self.assertEqual(err["type"], "error")
        self.assertEqual(err["reason"], "failed")
        self.assertEqual(err["error_type"], "RuntimeError")
        self.assertEqual(err["completed_pillars"], ["unity_catalog"])
        self.assertEqual(err["run_id"], run.id)
        self.assertIn(err["reference"], err["error"])
        # The raw upstream message must never reach the client.
        self.assertNotIn("secret_column", json.dumps(err))

    async def test_complete_event_is_saved_to_history(self):
        async def ok():
            yield {"type": "complete", "overall": {"score": 50}, "pillars": [], "top_gaps": []}

        save = AsyncMock(return_value=7)
        with patch.object(runs, "run_assessment_stream", ok), patch.object(runs.snapshots, "save_snapshot", save):
            run = runs.start_run("a@x", None, None, None, None)
            await run.task

        self.assertEqual(run.events[-1]["snapshot_id"], 7)
        self.assertTrue(run.events[-1]["snapshot_saved"])
        self.assertEqual(save.await_args.kwargs["created_by"], "a@x")

    async def test_second_run_is_refused_while_one_is_working(self):
        started = asyncio.Event()

        async def slow():
            started.set()
            await asyncio.sleep(30)
            yield {}

        with patch.object(runs, "run_assessment_stream", slow):
            first = runs.start_run("a@x", None, None, None, None)
            await started.wait()
            with self.assertRaises(runs.ActiveRunExists) as ctx:
                runs.start_run("a@x", None, None, None, None)
            self.assertEqual(ctx.exception.run_id, first.id)
            # Another user is not blocked by this user's run.
            other = runs.start_run("b@x", None, None, None, None)

        self.assertFalse(first.done)
        self.assertFalse(other.done)
        for r in list(runs._runs.values()):
            if r.task and not r.task.done():
                r.task.cancel()

    async def test_stop_ends_the_run_as_cancelled_and_never_saves_it(self):
        started = asyncio.Event()

        async def slow():
            started.set()
            await asyncio.sleep(30)
            yield {"type": "complete", "overall": {}, "pillars": [], "top_gaps": []}

        save = AsyncMock(return_value=1)
        with patch.object(runs, "run_assessment_stream", slow), patch.object(runs.snapshots, "save_snapshot", save):
            run = runs.start_run("a@x", None, None, None, None)
            await started.wait()
            self.assertTrue(runs.cancel_run(run.id, "a@x"))
            with self.assertRaises(asyncio.CancelledError):
                await run.task

        self.assertEqual(run.events[-1]["type"], "error")
        self.assertEqual(run.events[-1]["reason"], "cancelled")
        self.assertEqual(run.events[-1]["error"], "The assessment was stopped.")
        save.assert_not_awaited()

    async def test_run_stopped_by_a_limit_reports_a_failure_with_the_reason(self):
        started = asyncio.Event()

        async def slow():
            started.set()
            await asyncio.sleep(30)
            yield {}

        with patch.object(runs, "run_assessment_stream", slow):
            run = runs.start_run("a@x", None, None, None, None)
            await started.wait()
            runs._cancel(run, "it ran past the run time limit")
            with self.assertRaises(asyncio.CancelledError):
                await run.task

        self.assertEqual(run.events[-1]["reason"], "failed")
        self.assertIn("run time limit", run.events[-1]["error"])

    async def test_other_users_cannot_read_a_run(self):
        run = runs.AssessmentRun(id="r9", principal="a@x")
        runs._runs[run.id] = run
        self.assertIs(runs.get_run("r9", "a@x"), run)
        self.assertIsNone(runs.get_run("r9", "b@x"))
        self.assertFalse(runs.cancel_run("r9", "b@x"))


class LimitsTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        for r in list(runs._runs.values()):
            if r.task and not r.task.done():
                r.task.cancel()
        runs._runs.clear()

    async def _slow_run(self, principal="a@x", **kw):
        async def slow():
            await asyncio.sleep(30)
            yield {}

        with patch.object(runs, "run_assessment_stream", slow):
            run = runs.start_run(principal, "tok", None, None, None, **kw)
            await asyncio.sleep(0)
        return run

    async def test_cancel_before_first_step_still_finishes_the_run(self):
        run = runs.start_run("a@x", None, None, None, None)
        run.task.cancel()  # before the loop ever runs _drive
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertTrue(run.done)
        self.assertEqual(run.events[-1]["reason"], "cancelled")
        self.assertIsNone(run.task)

    async def test_run_with_no_browser_attached_keeps_going(self):
        # The tab can close mid-run: nobody is reading, and the run must still finish.
        unattended = await self._slow_run("a@x")
        overdue = await self._slow_run("b@x")
        now = unattended.started_at + 10 * 60
        overdue.started_at = now - runs.MAX_RUN_SECONDS - 1
        runs._sweep(now)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertFalse(unattended.done)
        self.assertTrue(overdue.done)
        self.assertEqual(overdue.events[-1]["reason"], "failed")

        unattended.finish()
        runs._sweep(unattended.finished_at + runs.FINISHED_TTL_SECONDS + 1)
        self.assertNotIn(unattended.id, runs._runs)

    async def test_legacy_stream_keeps_old_shape_and_does_not_replace_ui_run(self):
        ui_run = await self._slow_run("a@x")

        async def quick():
            yield {"type": "pillar", "pillar": {"key": "k"}}

        with patch.object(runs, "run_assessment_stream", quick):
            legacy = runs.start_run("a@x", None, None, None, None, exclusive=False)
            chunks = await _collect(runs.stream_whole_run(legacy))

        self.assertFalse(ui_run.done)
        evs = _events(chunks)
        self.assertEqual(evs, [{"type": "pillar", "pillar": {"key": "k"}}])
        self.assertEqual(chunks[-1], "data: [DONE]\n\n")

    async def test_legacy_stream_run_does_not_block_or_get_blocked_by_a_ui_run(self):
        legacy = await self._slow_run("a@x", exclusive=False)
        ui = await self._slow_run("a@x")
        self.assertFalse(legacy.done)
        self.assertFalse(ui.done)
        self.assertIs(runs.active_run("a@x"), ui)
        with self.assertRaises(runs.ActiveRunExists):
            await self._slow_run("a@x")
        self.assertFalse(ui.done)
        self.assertFalse(legacy.done)

    async def test_legacy_stream_disconnect_cancels_the_run(self):
        run = await self._slow_run("a@x", exclusive=False)
        agen = runs.stream_whole_run(run)
        await agen.__anext__()  # ": connected"
        await agen.aclose()  # client went away
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertTrue(run.done)
        self.assertEqual(run.events[-1]["reason"], "cancelled")


class ContextHandOffTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        runs._runs.clear()

    async def test_run_sees_the_callers_token_and_scope_and_clears_the_token(self):
        from server.config import get_user_token
        from server.workspace_filter import get_catalog_scope, get_schema_scope, get_workspace_filter

        seen = {}

        async def probe():
            seen.update(token=get_user_token(), wsf=get_workspace_filter(),
                        catalogs=get_catalog_scope(), schemas=get_schema_scope())
            yield {"type": "pillar", "pillar": {"key": "k"}}

        wsf = {"mode": "include", "workspace_ids": ["123"]}
        # Record what the run sets, calling through so the probe still sees the
        # values. (Reading the finished task's context needs Python 3.12; the
        # Databricks Apps runtime is 3.11.)
        token_calls = []
        real_set_user_token = runs.set_user_token

        def recording_set_user_token(value):
            token_calls.append(value)
            real_set_user_token(value)

        with patch.object(runs, "run_assessment_stream", probe), \
             patch.object(runs, "set_user_token", recording_set_user_token):
            run = runs.start_run("a@x", "user-token", wsf, ["cat_a"], ["sch"])
            await asyncio.sleep(0)
            task = run.task
            await task

        self.assertEqual(seen["token"], "user-token")
        self.assertEqual(seen["catalogs"], ["cat_a"])
        self.assertEqual(seen["schemas"], ["sch"])
        self.assertEqual(seen["wsf"]["workspace_ids"], ["123"])
        # The run sets the caller's token, then clears it when it ends, and (after
        # its done callback, one loop tick later) drops the task.
        self.assertEqual(token_calls, ["user-token", None])
        await asyncio.sleep(0)
        self.assertIsNone(run.task)


class RoutesTest(unittest.IsolatedAsyncioTestCase):
    """The HTTP surface the UI depends on."""

    def setUp(self):
        import httpx
        from fastapi import FastAPI

        from server.routes import _shared
        from server.routes import assess as assess_routes

        self.principal = "a@x"
        app = FastAPI()
        app.include_router(assess_routes.router, prefix="/api")
        app.dependency_overrides[_shared.current_principal] = lambda: self.principal
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")

    async def asyncTearDown(self):
        await self.client.aclose()
        for r in list(runs._runs.values()):
            if r.task and not r.task.done():
                r.task.cancel()
        runs._runs.clear()

    async def test_start_read_and_cancel(self):
        async def quick():
            yield {"type": "pillar", "pillar": {"key": "k"}}

        with patch.object(runs, "run_assessment_stream", quick):
            r = await self.client.post("/api/assess/runs", json={"catalogs": ["c1"]})
            self.assertEqual(r.status_code, 200)
            run_id = r.json()["run_id"]
            task = runs._runs[run_id].task
            if task:
                await task

        r = await self.client.get(f"/api/assess/runs/{run_id}/events?after=0")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/event-stream"))
        types = [json.loads(line[6:])["type"] for line in r.text.splitlines() if line.startswith("data: {")]
        self.assertEqual(types, ["pillar", "end"])

        r = await self.client.delete(f"/api/assess/runs/{run_id}")
        self.assertEqual((r.status_code, r.json()), (200, {"cancelled": True}))

    async def test_second_start_is_409_with_the_active_run_and_active_endpoint_finds_it(self):
        async def slow():
            await asyncio.sleep(30)
            yield {}

        with patch.object(runs, "run_assessment_stream", slow):
            first = await self.client.post("/api/assess/runs", json={})
            run_id = first.json()["run_id"]
            again = await self.client.post("/api/assess/runs", json={})
            active = await self.client.get("/api/assess/runs/active")
            self.principal = "b@x"
            other = await self.client.get("/api/assess/runs/active")
            self.principal = "a@x"

        self.assertEqual(first.status_code, 200)
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json()["run_id"], run_id)
        self.assertEqual(active.json()["run_id"], run_id)
        self.assertEqual(other.json(), {"run_id": None})

        # Once the run ends, there is nothing active and a new run can start.
        runs._runs[run_id].task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual((await self.client.get("/api/assess/runs/active")).json(), {"run_id": None})

    async def test_unknown_or_foreign_run_is_a_json_404(self):
        run = runs.AssessmentRun(id="theirs", principal="b@x")
        runs._runs[run.id] = run
        for path in ("/api/assess/runs/theirs/events", "/api/assess/runs/nope/events"):
            r = await self.client.get(path)
            self.assertEqual(r.status_code, 404)
            self.assertIn("no longer available", r.json()["error"])
        r = await self.client.delete("/api/assess/runs/theirs")
        self.assertEqual(r.status_code, 404)


class RunCapConfigTest(unittest.TestCase):
    def test_env_override_and_bad_values(self):
        from server.config import env_seconds
        with patch.dict("os.environ", {"GOR_MAX_RUN_SECONDS": "7200"}):
            self.assertEqual(env_seconds("GOR_MAX_RUN_SECONDS", 10), 7200)
        for bad in ("abc", "-5", "0", "", "inf", "Infinity", "nan"):
            with patch.dict("os.environ", {"GOR_MAX_RUN_SECONDS": bad}):
                self.assertEqual(env_seconds("GOR_MAX_RUN_SECONDS", 10), 10, bad)
        self.assertGreaterEqual(runs.MAX_RUN_SECONDS, 3 * 60 * 60)


if __name__ == "__main__":
    unittest.main()
