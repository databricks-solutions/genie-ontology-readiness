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

    async def test_new_run_cancels_the_same_users_earlier_run(self):
        started = asyncio.Event()

        async def slow():
            started.set()
            await asyncio.sleep(30)
            yield {}

        with patch.object(runs, "run_assessment_stream", slow):
            first = runs.start_run("a@x", None, None, None, None)
            await started.wait()
            runs.start_run("a@x", None, None, None, None)
            with self.assertRaises(asyncio.CancelledError):
                await first.task

        self.assertEqual(first.events[-1]["reason"], "cancelled")
        for r in list(runs._runs.values()):
            if r.task and not r.task.done():
                r.task.cancel()

    async def test_other_users_cannot_read_a_run(self):
        run = runs.AssessmentRun(id="r9", principal="a@x")
        runs._runs[run.id] = run
        self.assertIs(runs.get_run("r9", "a@x"), run)
        self.assertIsNone(runs.get_run("r9", "b@x"))
        self.assertFalse(runs.cancel_run("r9", "b@x"))


if __name__ == "__main__":
    unittest.main()
