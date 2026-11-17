import unittest
from unittest.mock import patch

from server import sql_client


class _Resp:
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

    def get(self, url, headers=None):
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
        with self.assertRaisesRegex(Exception, "timed out after 1 seconds"):
            await sql_client._poll_statement(session, "https://h", {}, "st-1", poll_seconds=1)
        # The budget is wall-clock time, not loop passes.
        self.assertLess(time.monotonic() - t0, 3)
        self.assertGreaterEqual(session.gets, 1)
        self.assertEqual(session.posts, ["https://h/api/2.0/sql/statements/st-1/cancel"])

    async def test_default_budget_is_unchanged(self):
        self.assertEqual(sql_client.DEFAULT_POLL_SECONDS, 120)



class CancelAndPlumbingTest(unittest.IsolatedAsyncioTestCase):
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

        async def once(query, params, force_sp, poll_seconds=0):
            seen.append(poll_seconds)
            if not force_sp:
                raise Exception("[INSUFFICIENT_PERMISSIONS] Insufficient privileges: denied")  # OBO denied -> SP fallback
            return []

        with patch.object(sql_client, "_execute_once", AsyncMock(side_effect=once)), \
             patch.object(sql_client, "record_identity"):
            await sql_client.execute_sql("q", force_sp=True, record=False, poll_seconds=11)        # forced SP
            with patch.object(sql_client, "get_user_token", return_value=None):
                await sql_client.execute_sql("q", record=False, poll_seconds=12)                   # no token
            with patch.object(sql_client, "get_user_token", return_value="t"), \
                 patch.object(sql_client, "FORCE_SP", False):
                await sql_client.execute_sql("q", record=False, poll_seconds=13)                   # OBO -> SP
        self.assertEqual(seen, [11, 12, 13, 13])


if __name__ == "__main__":
    unittest.main()
