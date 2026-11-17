import unittest
from unittest.mock import patch

from server import sql_client


class _Resp:
    status = 200

    def __init__(self, body):
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._body

    async def read(self):
        return b""


class _Session:
    def __init__(self):
        self.gets = 0
        self.posts = []

    def get(self, url, headers=None, timeout=None):
        self.gets += 1
        return _Resp({"status": {"state": "RUNNING"}})

    def post(self, url, headers=None):
        self.posts.append(url)
        return _Resp({})


class PollBudgetTest(unittest.IsolatedAsyncioTestCase):
    async def test_times_out_after_the_budget_and_cancels_the_statement(self):
        session = _Session()
        import time
        t0 = time.monotonic()
        with self.assertRaises(sql_client.StatementTimeout):
            await sql_client._poll_statement(session, "https://h", {}, "st-1", poll_seconds=1)
        # The budget is wall-clock time, not loop passes.
        self.assertLess(time.monotonic() - t0, 3)
        self.assertEqual(session.posts, ["https://h/api/2.0/sql/statements/st-1/cancel"])

    async def test_default_budget_is_unchanged(self):
        self.assertEqual(sql_client.DEFAULT_POLL_SECONDS, 120)



class _DoneSession(_Session):
    def __init__(self, state):
        super().__init__()
        self.state = state

    def get(self, url, headers=None, timeout=None):
        self.gets += 1
        return _Resp({"status": {"state": self.state, "error": {"message": "boom"}}})


class _FlakySession(_Session):
    """First status read times out, the next one says SUCCEEDED."""

    def get(self, url, headers=None, timeout=None):
        import asyncio
        self.gets += 1
        if self.gets == 1:
            class _Slow:
                async def __aenter__(self_inner):
                    raise asyncio.TimeoutError()

                async def __aexit__(self_inner, *a):
                    return False
            return _Slow()
        return _Resp({"status": {"state": "SUCCEEDED"}})


class CancelAndPlumbingTest(unittest.IsolatedAsyncioTestCase):
    async def test_one_slow_status_read_is_retried_not_fatal(self):
        session = _FlakySession()
        result = await sql_client._poll_statement(session, "https://h", {}, "st-5", poll_seconds=10)
        self.assertEqual(result["status"]["state"], "SUCCEEDED")
        self.assertEqual(session.posts, [])  # not cancelled

    async def test_submit_payload_wait_timeout(self):
        sent = []

        class _Submit:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def post(self, url, json=None, headers=None, timeout=None):
                sent.append((json["wait_timeout"], timeout))
                return _Resp({"status": {"state": "SUCCEEDED"}, "manifest": {"schema": {"columns": []}},
                              "result": {"data_array": []}})

        with patch.object(sql_client.aiohttp, "ClientSession", _Submit), \
             patch.object(sql_client, "get_workspace_host", return_value="https://h"), \
             patch.object(sql_client, "get_auth_headers", return_value={"Authorization": "Bearer t"}):
            for wait in (0, 3, 50, 99):
                await sql_client._execute_once("q", None, force_sp=True, wait_seconds=wait)
        self.assertEqual([w for w, _ in sent], ["0s", "5s", "50s", "50s"])
        self.assertIsNotNone(sent[0][1])   # a 0 s submit is bounded
        self.assertIsNone(sent[2][1])      # a waiting submit keeps the session limits

    async def test_a_finished_statement_is_not_cancelled(self):
        for state in ("SUCCEEDED", "FAILED", "CANCELED"):
            session = _DoneSession(state)
            try:
                await sql_client._poll_statement(session, "https://h", {}, "st-3", poll_seconds=5)
            except Exception:
                pass
            self.assertEqual(session.posts, [], state)

    async def test_a_cancelled_task_cancels_its_statement(self):
        import asyncio
        session = _Session()
        task = asyncio.ensure_future(sql_client._poll_statement(session, "https://h", {}, "st-2", poll_seconds=60))
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(session.posts, ["https://h/api/2.0/sql/statements/st-2/cancel"])

    async def test_poll_seconds_reaches_every_identity_path(self):
        from unittest.mock import AsyncMock
        seen = []

        async def once(query, params, force_sp, poll_seconds=0, wait_seconds=50):
            seen.append((poll_seconds, wait_seconds))
            if not force_sp:
                raise Exception("[INSUFFICIENT_PERMISSIONS] Insufficient privileges: denied")  # OBO denied -> SP fallback
            return []

        with patch.object(sql_client, "_execute_once", AsyncMock(side_effect=once)), \
             patch.object(sql_client, "record_identity"):
            await sql_client.execute_sql("q", force_sp=True, record=False, poll_seconds=11, wait_seconds=0)
            with patch.object(sql_client, "get_user_token", return_value=None):
                await sql_client.execute_sql("q", record=False, poll_seconds=12, wait_seconds=0)
            with patch.object(sql_client, "get_user_token", return_value="t"), \
                 patch.object(sql_client, "FORCE_SP", False):
                await sql_client.execute_sql("q", record=False, poll_seconds=13, wait_seconds=0)  # OBO -> SP
        self.assertEqual(seen, [(11, 0), (12, 0), (13, 0), (13, 0)])

    async def test_cancel_helper_lets_a_run_cancellation_through(self):
        import asyncio

        class _Hanging(_Session):
            def post(self, url, headers=None):
                class _R:
                    async def __aenter__(self_inner):
                        await asyncio.sleep(10)
                    async def __aexit__(self_inner, *a):
                        return False
                return _R()

        task = asyncio.ensure_future(sql_client._cancel_statement(_Hanging(), "https://h/x", {}, "st-4"))
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


if __name__ == "__main__":
    unittest.main()
