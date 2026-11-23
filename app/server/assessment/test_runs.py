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

    async def test_a_stopped_run_no_longer_blocks_a_new_one(self):
        async def slow():
            await asyncio.sleep(30)
            yield {}

        with patch.object(runs, "run_assessment_stream", slow):
            first = runs.start_run("a@x", None, None, None, None)
            await asyncio.sleep(0)
            self.assertTrue(runs.cancel_run(first.id, "a@x"))
            # Still not done (the task has not wound down yet), but no longer active.
            self.assertFalse(first.done)
            self.assertIsNone(runs.active_run("a@x"))
            second = runs.start_run("a@x", None, None, None, None)
            self.assertIsNot(second, first)
            second.task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first.task

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

        # Stopping a run that already finished is a 404, not a "stopped" (it was saved).
        r = await self.client.delete(f"/api/assess/runs/{run_id}")
        self.assertEqual(r.status_code, 404)
        self.assertIn("no longer running", r.json()["error"])

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
        self.assertIsNone(other.json()["run_id"])

        # Once the run ends, there is nothing active and a new run can start.
        runs._runs[run_id].task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertIsNone((await self.client.get("/api/assess/runs/active")).json()["run_id"])

    async def test_delete_then_immediate_post_is_not_a_409(self):
        async def slow():
            await asyncio.sleep(30)
            yield {}

        with patch.object(runs, "run_assessment_stream", slow):
            first = await self.client.post("/api/assess/runs", json={})
            run_id = first.json()["run_id"]
            stop = await self.client.delete(f"/api/assess/runs/{run_id}")
            again = await self.client.post("/api/assess/runs", json={})
            active = await self.client.get("/api/assess/runs/active")
            second_id = again.json().get("run_id")
            if second_id and second_id in runs._runs:
                runs._runs[second_id].task.cancel()

        self.assertEqual(stop.status_code, 200)
        self.assertEqual(again.status_code, 200)
        self.assertNotEqual(second_id, run_id)
        self.assertEqual(active.json()["run_id"], second_id)

    async def test_unknown_or_foreign_run_is_a_json_404(self):
        run = runs.AssessmentRun(id="theirs", principal="b@x")
        runs._runs[run.id] = run
        for path in ("/api/assess/runs/theirs/events", "/api/assess/runs/nope/events"):
            r = await self.client.get(path)
            self.assertEqual(r.status_code, 404)
            self.assertIn("no longer available", r.json()["error"])
        r = await self.client.delete("/api/assess/runs/theirs")
        self.assertEqual(r.status_code, 404)


class KeepGoingTest(unittest.IsolatedAsyncioTestCase):
    """A run with no browser attached: kept going when its result is saved, cancelled
    after ABANDON_SECONDS when it is not (history off, or a legacy stream)."""

    def tearDown(self):
        for r in list(runs._runs.values()):
            if r.task and not r.task.done():
                r.task.cancel()
        runs._runs.clear()

    async def _run(self, **kw):
        async def slow():
            await asyncio.sleep(30)
            yield {}

        with patch.object(runs, "run_assessment_stream", slow):
            run = runs.start_run("a@x", None, None, None, None, **kw)
            await asyncio.sleep(0)
        return run

    async def _sweep_after(self, run, seconds):
        runs._sweep(run.last_read + seconds)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def test_history_off_run_with_no_reader_is_cancelled_after_the_abandon_limit(self):
        run = await self._run(keep_going=False)
        await self._sweep_after(run, runs.ABANDON_SECONDS - 1)
        self.assertFalse(run.done)
        await self._sweep_after(run, runs.ABANDON_SECONDS + 1)
        self.assertTrue(run.done)
        self.assertEqual(run.events[-1]["reason"], "failed")
        self.assertIn("cannot be saved", run.events[-1]["error"])

    async def test_history_on_run_with_no_reader_keeps_going(self):
        run = await self._run(keep_going=True)
        await self._sweep_after(run, runs.ABANDON_SECONDS * 10)
        self.assertFalse(run.done)

    async def test_a_browser_reading_the_run_keeps_a_history_off_run_alive(self):
        run = await self._run(keep_going=False)
        agen = runs.stream_events(run)
        await agen.__anext__()  # attaches a reader
        await self._sweep_after(run, runs.ABANDON_SECONDS * 10)
        self.assertFalse(run.done)
        await agen.aclose()
        await self._sweep_after(run, runs.ABANDON_SECONDS + 1)
        self.assertTrue(run.done)


class StopAfterFinishTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        for r in list(runs._runs.values()):
            if r.task and not r.task.done():
                r.task.cancel()
        runs._runs.clear()

    async def test_cancel_returns_false_once_the_run_is_done(self):
        async def quick():
            yield {"type": "pillar", "pillar": {"key": "k"}}

        with patch.object(runs, "run_assessment_stream", quick):
            run = runs.start_run("a@x", None, None, None, None)
            await run.task
        self.assertTrue(run.done)
        self.assertFalse(runs.cancel_run(run.id, "a@x"))
        self.assertNotIn("stopped", json.dumps(run.events))

    async def test_a_stop_during_the_save_ends_the_run_frees_the_slot_and_leaves_no_row(self):
        save_started = asyncio.Event()
        release = asyncio.Event()

        async def completes():
            yield {"type": "complete", "overall": {"score": 1}, "pillars": [], "top_gaps": []}

        async def slow_save(*args, **kwargs):
            save_started.set()
            await release.wait()
            return 42

        delete = AsyncMock(return_value=True)
        with patch.object(runs, "run_assessment_stream", completes), \
             patch.object(runs.snapshots, "save_snapshot", slow_save), \
             patch.object(runs.snapshots, "delete_snapshot", delete):
            run = runs.start_run("a@x", None, None, None, None)
            await save_started.wait()
            self.assertTrue(runs.cancel_run(run.id, "a@x"))
            with self.assertRaises(asyncio.CancelledError):
                await run.task
            # The slot is free at once: a new run starts without a 409.
            second = runs.start_run("a@x", None, None, None, None)
            second.task.cancel()
            # The write finishes in the background, then its row is removed.
            release.set()
            for _ in range(20):
                await asyncio.sleep(0)
            await asyncio.gather(*list(runs._removals))  # the removal is held and awaited, not lost

        self.assertEqual(run.events[-1]["type"], "error")
        self.assertEqual(run.events[-1]["reason"], "cancelled")
        self.assertFalse(any(e.get("type") == "complete" for e in run.events))
        delete.assert_awaited_once_with(42, "a@x")

    async def test_a_hung_save_still_ends_at_the_run_cap(self):
        started = asyncio.Event()

        async def completes():
            yield {"type": "complete", "overall": {}, "pillars": [], "top_gaps": []}

        async def hangs(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        with patch.object(runs, "run_assessment_stream", completes), \
             patch.object(runs.snapshots, "save_snapshot", hangs):
            run = runs.start_run("a@x", None, None, None, None)
            await started.wait()
            run.started_at -= runs.MAX_RUN_SECONDS + 1  # past the hard cap
            runs._sweep()
            with self.assertRaises(asyncio.CancelledError):
                await run.task

        self.assertTrue(run.done)
        self.assertEqual(run.events[-1]["reason"], "failed")
        self.assertIn("run time limit", run.events[-1]["error"])

    async def test_a_save_that_never_returns_is_given_up_after_the_timeout(self):
        async def completes():
            yield {"type": "complete", "overall": {}, "pillars": [], "top_gaps": []}

        async def hangs(*args, **kwargs):
            await asyncio.Event().wait()

        with patch.object(runs, "run_assessment_stream", completes), \
             patch.object(runs.snapshots, "save_snapshot", hangs), \
             patch.object(runs, "SAVE_TIMEOUT_SECONDS", 0.05):
            run = runs.start_run("a@x", None, None, None, None)
            await asyncio.wait_for(run.task, 5)

        self.assertTrue(run.done)
        self.assertEqual(run.events[-1]["type"], "complete")
        self.assertFalse(run.events[-1]["snapshot_saved"])


class SharedIdentityTest(unittest.IsolatedAsyncioTestCase):
    """Without a forwarded identity every request is the shared bucket: its runs must
    not be offered across users (no /active, no 409)."""

    def setUp(self):
        import httpx
        from fastapi import FastAPI

        from server.routes import _shared
        from server.routes import assess as assess_routes
        from server.security import UNATTRIBUTED_PRINCIPAL

        app = FastAPI()
        app.include_router(assess_routes.router, prefix="/api")
        app.dependency_overrides[_shared.current_principal] = lambda: UNATTRIBUTED_PRINCIPAL
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        self.bucket = UNATTRIBUTED_PRINCIPAL

    async def asyncTearDown(self):
        await self.client.aclose()
        for r in list(runs._runs.values()):
            if r.task and not r.task.done():
                r.task.cancel()
        runs._runs.clear()

    async def test_shared_bucket_is_one_slot_for_the_whole_workspace(self):
        async def slow():
            await asyncio.sleep(30)
            yield {}

        with patch.object(runs, "run_assessment_stream", slow):
            first = await self.client.post("/api/assess/runs", json={})
            second = await self.client.post("/api/assess/runs", json={})
            active = await self.client.get("/api/assess/runs/active")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["run_id"], first.json()["run_id"])
        self.assertEqual(active.json()["run_id"], first.json()["run_id"])
        self.assertTrue(active.json()["shared"])

    async def test_shared_bucket_run_keeps_going_when_history_is_on(self):
        async def slow():
            await asyncio.sleep(30)
            yield {}

        with patch.object(runs, "run_assessment_stream", slow), \
             patch.object(runs.snapshots, "saves_history", AsyncMock(return_value=True)):
            r = await self.client.post("/api/assess/runs", json={})
        self.assertTrue(runs._runs[r.json()["run_id"]].keep_going)

        runs.cancel_run(r.json()["run_id"], "unattributed")
        with patch.object(runs, "run_assessment_stream", slow), \
             patch.object(runs.snapshots, "saves_history", AsyncMock(return_value=False)):
            off = await self.client.post("/api/assess/runs", json={})
        self.assertFalse(runs._runs[off.json()["run_id"]].keep_going)

    async def test_a_run_from_another_user_cannot_be_stopped_or_read_as_active(self):
        other = runs.AssessmentRun(id="theirs", principal="someone@x")
        other.exclusive = True
        runs._runs[other.id] = other
        self.assertIsNone((await self.client.get("/api/assess/runs/active")).json()["run_id"])
        self.assertEqual((await self.client.delete("/api/assess/runs/theirs")).status_code, 404)
        self.assertFalse(other.done)


class HistoryOffFlagTest(unittest.IsolatedAsyncioTestCase):
    """The route decides keep_going from whether history is saved."""

    def tearDown(self):
        runs._runs.clear()

    async def test_route_passes_keep_going_from_history_state(self):
        import httpx
        from fastapi import FastAPI

        from server.routes import _shared
        from server.routes import assess as assess_routes

        app = FastAPI()
        app.include_router(assess_routes.router, prefix="/api")
        app.dependency_overrides[_shared.current_principal] = lambda: "a@x"
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")

        async def slow():
            await asyncio.sleep(30)
            yield {}

        try:
            with patch.object(runs, "run_assessment_stream", slow), \
                 patch.object(assess_routes.snapshots, "saves_history", AsyncMock(return_value=False)):
                off = await client.post("/api/assess/runs", json={})
            runs.cancel_run(off.json()["run_id"], "a@x")
            with patch.object(runs, "run_assessment_stream", slow), \
                 patch.object(assess_routes.snapshots, "saves_history", AsyncMock(return_value=True)):
                on = await client.post("/api/assess/runs", json={})
            self.assertFalse(runs._runs[off.json()["run_id"]].keep_going)
            self.assertTrue(runs._runs[on.json()["run_id"]].keep_going)
        finally:
            await client.aclose()
            for r in list(runs._runs.values()):
                if r.task and not r.task.done():
                    r.task.cancel()


class SourcesResolvedOnceTest(unittest.IsolatedAsyncioTestCase):
    """The catalog set is resolved once per assessment: a later probe must not re-ask
    the bindings API (under the viewer token, which may have expired by then)."""

    async def test_later_probes_keep_the_first_catalog_set_when_bindings_fail(self):
        from server.assessment import probes
        from server.workspace_filter import set_workspace_filter

        # First call succeeds; a second call (token expired mid-run) would return None.
        bindings = AsyncMock(side_effect=[[{"name": "c1"}], None])

        async def assess_in_one_context():
            set_workspace_filter({"mode": "include", "workspace_ids": ["1"]})
            first = await probes.prime_request_sources()
            later = await probes._resolve_sources()  # what every later probe calls
            return first, later

        with patch.object(probes, "accessible_catalogs", bindings), \
             patch.object(probes, "execute_sql", AsyncMock(return_value=[])):
            first, later = await asyncio.create_task(assess_in_one_context())

        self.assertEqual(bindings.await_count, 1)
        self.assertIs(later, first)


class StopCancelsWorkTest(unittest.IsolatedAsyncioTestCase):
    """A stop reaches the work in flight: the probes, and the warehouse statement they poll."""

    async def test_stopping_the_run_cancels_its_probes(self):
        from server.assessment import scoring

        cancelled = []

        async def hang_probe(key):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(key)

        async def collect():
            return [e async for e in scoring.run_assessment_stream()]

        with patch.object(scoring, "PROBES", ["a", "b"]), patch.object(scoring, "_run_probe", hang_probe), \
             patch.object(scoring, "prime_request_sources", AsyncMock(return_value={})):
            task = asyncio.create_task(collect())
            await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
        self.assertEqual(sorted(cancelled), ["a", "b"])

    async def test_a_stop_during_the_submit_cancels_the_statement_the_submit_returns(self):
        from server import sql_client

        submitted = asyncio.Event()

        async def slow_submit(session, url, payload, headers, submit_kwargs):
            submitted.set()
            await asyncio.sleep(0.2)  # the reply (and its statement id) comes after the cancel
            return {"statement_id": "stmt-S", "status": {"state": "PENDING"}}

        cancel = AsyncMock()
        with patch.object(sql_client, "_post_statement", slow_submit), \
             patch.object(sql_client, "_cancel_statement", cancel), \
             patch.object(sql_client, "get_workspace_host", lambda: "https://h"), \
             patch.object(sql_client, "get_auth_headers", lambda **kw: {"Authorization": "t"}), \
             patch.object(sql_client, "_poll_statement", AsyncMock(return_value={})):
            task = asyncio.create_task(sql_client._execute_once("SELECT 1", None, True, wait_seconds=0))
            await submitted.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):  # never swallowed
                await task

        cancel.assert_awaited_once()
        self.assertEqual(cancel.await_args.args[1], "https://h/api/2.0/sql/statements/stmt-S")
        self.assertEqual(cancel.await_args.args[3], "stmt-S")

    async def test_a_submit_that_never_replies_does_not_hang_the_cancel(self):
        from server import sql_client

        submitted = asyncio.Event()

        async def hung_submit(*args, **kwargs):
            submitted.set()
            await asyncio.Event().wait()

        cancel = AsyncMock()
        with patch.object(sql_client, "_post_statement", hung_submit), \
             patch.object(sql_client, "_cancel_statement", cancel), \
             patch.object(sql_client, "SUBMIT_CANCEL_WAIT_SECONDS", 0.05), \
             patch.object(sql_client, "get_workspace_host", lambda: "https://h"), \
             patch.object(sql_client, "get_auth_headers", lambda **kw: {"Authorization": "t"}):
            task = asyncio.create_task(sql_client._execute_once("SELECT 1", None, True, wait_seconds=0))
            await submitted.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)  # returns promptly, does not hang
        cancel.assert_not_awaited()  # no id was ever named, so there is nothing to cancel

    async def test_a_cancelled_poll_cancels_its_warehouse_statement(self):
        from server.sql_client import _poll_statement

        class Resp:
            def __init__(self, body=None):
                self.status = 200
                self._body = body or {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def json(self):
                return self._body

            async def read(self):
                return b""

        class Session:
            def __init__(self):
                self.posts = []

            def get(self, url, **kwargs):
                return Resp({"status": {"state": "RUNNING"}})

            def post(self, url, **kwargs):
                self.posts.append(url)
                return Resp()

        session = Session()
        task = asyncio.create_task(_poll_statement(session, "https://h", {}, "stmt-1", 60))
        await asyncio.sleep(1.2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(any(url.endswith("/statements/stmt-1/cancel") for url in session.posts), session.posts)


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


class _Conn:
    def __init__(self, result="DELETE 1"):
        self.result = result
        self.calls = []

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return self.result


class SnapshotDeleteTest(unittest.IsolatedAsyncioTestCase):
    """delete_snapshot's SQL and results. The stopped-run removal depends on them:
    False only when nothing is saved, and an error whenever the removal is unconfirmed."""

    def _patched(self, lakebase=True, pool=None):
        from server import snapshots
        return (
            patch.object(snapshots, "USE_LAKEBASE", lakebase),
            patch.object(snapshots, "_table", lambda: "t_hist"),
            patch("server.lakebase_client.get_pool", AsyncMock(return_value=pool)),
        )

    async def test_deletes_only_the_given_row_of_the_given_owner(self):
        from server import snapshots
        conn = _Conn("DELETE 1")
        a, b, c = self._patched(pool=_Pool(conn))
        with a, b, c:
            self.assertTrue(await snapshots.delete_snapshot(7, "a@x"))
        sql, args = conn.calls[0]
        self.assertEqual(sql, "DELETE FROM t_hist WHERE id = $1 AND created_by = $2")
        self.assertEqual(args, (7, "a@x"))

    async def test_false_when_the_row_is_already_gone(self):
        from server import snapshots
        a, b, c = self._patched(pool=_Pool(_Conn("DELETE 0")))
        with a, b, c:
            self.assertFalse(await snapshots.delete_snapshot(7, "someone-else@x"))

    async def test_false_without_touching_the_database_when_history_is_off_by_configuration(self):
        from server import snapshots
        get_pool = AsyncMock()
        with patch.object(snapshots, "USE_LAKEBASE", False), patch("server.lakebase_client.get_pool", get_pool):
            self.assertFalse(await snapshots.delete_snapshot(7, "a@x"))
        get_pool.assert_not_awaited()

    async def test_raises_when_the_database_is_configured_but_unavailable(self):
        # Not "already gone": the row may still be there, so the caller must retry.
        from server import snapshots
        a, b, c = self._patched(pool=None)
        with a, b, c, self.assertRaises(RuntimeError):
            await snapshots.delete_snapshot(7, "a@x")

    async def test_a_database_error_is_raised_so_the_caller_can_retry(self):
        from server import snapshots

        class Broken(_Conn):
            async def execute(self, sql, *args):
                raise RuntimeError("lakebase down")

        a, b, c = self._patched(pool=_Pool(Broken()))
        with a, b, c, self.assertRaises(RuntimeError):
            await snapshots.delete_snapshot(7, "a@x")


class ListReturnsRunIdTest(unittest.IsolatedAsyncioTestCase):
    async def test_list_selects_the_run_id_and_returns_it_or_none_for_old_rows(self):
        from datetime import datetime, timezone
        from server import snapshots

        rows = [
            {"id": 2, "created_at": datetime(2026, 11, 20, 10, tzinfo=timezone.utc), "created_by": "a@x",
             "overall_score": 61.0, "overall_level": 2, "run_id": "run-new"},
            {"id": 1, "created_at": datetime(2026, 11, 19, 10, tzinfo=timezone.utc), "created_by": "a@x",
             "overall_score": 40.0, "overall_level": 1},  # saved before run ids were recorded
        ]

        class Conn:
            sql = None

            async def fetch(self, sql, *args):
                Conn.sql = sql
                return rows

        pool = _Pool(Conn())
        with patch.object(snapshots, "_enabled", AsyncMock(return_value=True)), \
             patch.object(snapshots, "_table", lambda: "t_hist"), \
             patch.object(snapshots, "_scope_columns", False), \
             patch("server.lakebase_client.get_pool", AsyncMock(return_value=pool)):
            out = await snapshots.list_snapshots(created_by="a@x")

        self.assertIn("scorecard->>'run_id' AS run_id", Conn.sql)
        self.assertEqual([r["run_id"] for r in out], ["run-new", None])


class RemovalNotConfirmedTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_false_return_is_treated_as_removed_or_not_saved_and_not_retried(self):
        delete = AsyncMock(return_value=False)  # history off, or the row already gone
        with patch.object(runs.snapshots, "delete_snapshot", delete):
            await runs._remove_snapshot(7, "a@x")
        self.assertEqual(delete.await_count, 1)


class RemovalRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_failed_removal_is_retried_then_logged_as_an_error(self):
        delete = AsyncMock(side_effect=RuntimeError("lakebase down"))
        with patch.object(runs.snapshots, "delete_snapshot", delete), \
             patch.object(runs, "REMOVE_RETRY_SECONDS", 0.001):
            with self.assertLogs("server.assessment.runs", level="ERROR") as logs:
                await runs._remove_snapshot(7, "a@x")
        self.assertEqual(delete.await_count, runs.REMOVE_ATTEMPTS)
        self.assertIn("still in history", logs.output[0])

    async def test_a_removal_that_succeeds_on_retry_is_not_an_error(self):
        delete = AsyncMock(side_effect=[RuntimeError("blip"), True])
        with patch.object(runs.snapshots, "delete_snapshot", delete), \
             patch.object(runs, "REMOVE_RETRY_SECONDS", 0.001):
            with self.assertNoLogs("server.assessment.runs", level="ERROR"):
                await runs._remove_snapshot(7, "a@x")
        self.assertEqual(delete.await_count, 2)

    async def test_the_removal_task_is_held_until_it_ends(self):
        gate = asyncio.Event()

        async def slow(*args):
            await gate.wait()
            return True

        with patch.object(runs.snapshots, "delete_snapshot", slow):
            run = runs.AssessmentRun(id="stopped1", principal="a@x")
            run.cancel_why = runs.USER_STOP
            runs._remove_if_stopped({"snapshot_id": 9}, run)
            self.assertEqual(len(runs._removals), 1)
            gate.set()
            await asyncio.gather(*list(runs._removals))
        self.assertEqual(len(runs._removals), 0)


class SavedResultNamesItsRunTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_saved_scorecard_carries_the_run_id(self):
        save = AsyncMock(return_value=5)
        event = {"overall": {}, "pillars": [], "top_gaps": []}
        with patch.object(runs.snapshots, "save_snapshot", save):
            await runs._save_snapshot(event, "a@x", None, "run-abc")
        self.assertEqual(save.await_args.args[0]["run_id"], "run-abc")
        self.assertEqual(event["snapshot_id"], 5)


class SubmitWithoutWaitTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_run_submits_without_a_server_wait_and_other_callers_keep_theirs(self):
        from server import sql_client

        execute_once = AsyncMock(return_value=[])

        async def in_run():
            sql_client.submit_without_wait()
            await sql_client.execute_sql("SELECT 1", record=False)

        async def not_a_run():
            await sql_client.execute_sql("SELECT 1", record=False)

        with patch.object(sql_client, "_execute_once", execute_once), \
             patch.object(sql_client, "FORCE_SP", False), \
             patch.object(sql_client, "get_user_token", lambda: None):
            await asyncio.create_task(in_run())
            await asyncio.create_task(not_a_run())
        waits = [c.kwargs["wait_seconds"] for c in execute_once.await_args_list]
        self.assertEqual(waits, [0, 50])

    async def test_a_run_keeps_the_total_budget_a_default_submit_gives(self):
        # The server wait a run skips is added to its poll budget: wait + poll is the same.
        from server import sql_client

        execute_once = AsyncMock(return_value=[])

        async def run_with(poll):
            sql_client.submit_without_wait()
            await sql_client.execute_sql("SELECT 1", record=False, poll_seconds=poll)

        async def default_with(poll):
            await sql_client.execute_sql("SELECT 1", record=False, poll_seconds=poll)

        async def budget(fn, poll):
            execute_once.reset_mock()
            await asyncio.create_task(fn(poll))
            kw = execute_once.await_args.kwargs
            return kw["wait_seconds"] + kw["poll_seconds"]

        with patch.object(sql_client, "_execute_once", execute_once), \
             patch.object(sql_client, "FORCE_SP", False), \
             patch.object(sql_client, "get_user_token", lambda: None):
            for poll in (sql_client.DEFAULT_POLL_SECONDS, 60):
                self.assertEqual(await budget(run_with, poll), await budget(default_with, poll), poll)
            self.assertEqual(await budget(run_with, sql_client.DEFAULT_POLL_SECONDS), 230)

    async def test_polling_starts_quickly_so_a_run_is_not_slowed(self):
        from server.sql_client import _poll_statement

        class Resp:
            status = 200

            def __init__(self, body):
                self._body = body

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def json(self):
                return self._body

        reads = {"n": 0}

        class Session:
            def get(self, url, **kwargs):
                reads["n"] += 1
                state = "SUCCEEDED" if reads["n"] >= 3 else "RUNNING"
                return Resp({"status": {"state": state}, "manifest": {}, "result": {}})

        loop = asyncio.get_running_loop()
        start = loop.time()
        await _poll_statement(Session(), "https://h", {}, "stmt-2", 60)
        elapsed = loop.time() - start
        self.assertEqual(reads["n"], 3)
        self.assertLess(elapsed, 1.0)  # 0.1 s + 0.2 s, not 2 x 1 s


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
