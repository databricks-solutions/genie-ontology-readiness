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
        with patch.object(sql_client.asyncio, "sleep", return_value=None):
            with self.assertRaisesRegex(Exception, "timed out after 7 seconds"):
                await sql_client._poll_statement(session, "https://h", {}, "st-1", poll_seconds=7)
        self.assertEqual(session.gets, 7)
        self.assertEqual(session.posts, ["https://h/api/2.0/sql/statements/st-1/cancel"])

    async def test_default_budget_is_unchanged(self):
        self.assertEqual(sql_client.DEFAULT_POLL_SECONDS, 120)


if __name__ == "__main__":
    unittest.main()
