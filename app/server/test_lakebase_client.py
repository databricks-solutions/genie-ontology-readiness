"""Lakebase connection: Provisioned (`database`) and Autoscaling (`postgres`) lakebase-db resources."""
import asyncio
import os
import ssl
import time
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg

from server import lakebase_client as lc

EP = "projects/gor/branches/production/endpoints/primary"
HOST = "ep-x.database.us-east-1.cloud.databricks.com"


def _endpoint(name, host, kind="ENDPOINT_TYPE_READ_WRITE"):
    return types.SimpleNamespace(name=name, status=types.SimpleNamespace(
        hosts=types.SimpleNamespace(host=host), endpoint_type=types.SimpleNamespace(value=kind)))


def _client(resources=(), endpoints=(), endpoint_host=HOST):
    w = MagicMock()
    w.apps.get.return_value = types.SimpleNamespace(resources=list(resources))
    w.postgres.list_endpoints.return_value = list(endpoints)
    w.postgres.get_endpoint.return_value = _endpoint(EP, endpoint_host)
    w.postgres.get_database.return_value = types.SimpleNamespace(
        status=types.SimpleNamespace(postgres_database="databricks_postgres"), spec=None)
    return w


def _postgres_resource(branch="projects/gor/branches/production"):
    return types.SimpleNamespace(name="lakebase-db", database=None, postgres=types.SimpleNamespace(
        branch=branch, database=f"{branch}/databases/databricks-postgres"))


def _database_resource():
    return types.SimpleNamespace(name="lakebase-db", postgres=None, database=types.SimpleNamespace(
        instance_name="ontos-lakebase", database_name="ontology_readiness_dev"))


class _Env:
    """Patch the app environment, the workspace client and the credential/pool calls."""

    def __init__(self, env, client):
        self.env, self.client = env, client

    def __enter__(self):
        lc._RESOURCE = None
        lc._TARGET = None
        lc._credential.update(token="", minted_at=0.0, retry_after=0.0)
        self.create_pool = AsyncMock(return_value=object())
        self.patches = [
            patch.dict(os.environ, {"DATABRICKS_APP_NAME": "gor-test", **self.env}, clear=True),
            patch("server.config._get_workspace_client", return_value=self.client),
            patch.object(lc, "_fetch_db_credential", AsyncMock(return_value="tok")),
            patch("asyncpg.create_pool", self.create_pool),
            patch.object(lc, "RETRY_DELAYS", (0.0, 0.0)),  # no real waits in the retry tests
        ]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in reversed(self.patches):
            p.stop()
        lc._RESOURCE = None
        lc._TARGET = None
        lc._pool = None
        lc._credential.update(token="", minted_at=0.0, retry_after=0.0)

    def run(self):
        asyncio.run(lc.init_pool())
        return lc._fetch_db_credential.await_args, self.create_pool.await_args


class EndpointNameTest(unittest.TestCase):
    def test_recognises_an_endpoint_resource_name(self):
        self.assertTrue(lc._is_endpoint_name(EP))
        self.assertFalse(lc._is_endpoint_name(HOST))
        self.assertFalse(lc._is_endpoint_name("projects/gor/branches/production"))


class AutoscalingTest(unittest.TestCase):
    def test_value_from_endpoint_is_the_credential_endpoint_and_pghost_the_host(self):
        # A Git deploy with an Autoscaling resource: valueFrom gives the endpoint name.
        env = {"LAKEBASE_HOST": EP, "PGHOST": HOST, "PGDATABASE": "databricks_postgres", "PGUSER": "sp-id"}
        with _Env(env, _client([_postgres_resource()])) as e:
            cred, pool = e.run()
        self.assertEqual(cred.kwargs["endpoint_name"], EP)
        self.assertEqual(pool.kwargs["host"], HOST)
        self.assertEqual(pool.kwargs["database"], "databricks_postgres")

    def test_without_pghost_the_host_is_read_from_the_endpoint(self):
        with _Env({"LAKEBASE_HOST": EP, "PGDATABASE": "d", "PGUSER": "u"}, _client([_postgres_resource()])) as e:
            _, pool = e.run()
        self.assertEqual(pool.kwargs["host"], HOST)

    def test_branch_endpoints_are_used_when_value_from_is_a_host(self):
        endpoints = [_endpoint("projects/gor/branches/production/endpoints/ro", "ro.host", "ENDPOINT_TYPE_READ_ONLY"),
                     _endpoint(EP, HOST)]
        with _Env({"LAKEBASE_HOST": HOST, "PGDATABASE": "d", "PGUSER": "u"},
                  _client([_postgres_resource()], endpoints)) as e:
            cred, pool = e.run()
        self.assertEqual(cred.kwargs["endpoint_name"], EP)  # the endpoint of the host we connect to
        self.assertEqual(pool.kwargs["host"], HOST)

    def test_an_explicit_endpoint_setting_wins(self):
        with _Env({"LAKEBASE_HOST": EP, "PGHOST": HOST, "LAKEBASE_ENDPOINT_NAME": "projects/o/branches/b/endpoints/e",
                   "PGDATABASE": "d", "PGUSER": "u"}, _client([_postgres_resource()])) as e:
            cred, _ = e.run()
        self.assertEqual(cred.kwargs["endpoint_name"], "projects/o/branches/b/endpoints/e")


class AutoscalingEndpointChoiceTest(unittest.TestCase):
    RO = _endpoint("projects/gor/branches/production/endpoints/ro", "ro.host", "ENDPOINT_TYPE_READ_ONLY")
    RW = _endpoint("projects/gor/branches/production/endpoints/rw", "rw.host")

    def _pick(self, endpoints, host="", error=None):
        w = _client(endpoints=endpoints)
        if error:
            w.postgres.list_endpoints.side_effect = error
        with patch("server.config._get_workspace_client", return_value=w):
            return lc._autoscaling_endpoint("projects/gor/branches/production", host)

    def test_a_host_match_counts_only_for_a_read_write_endpoint(self):
        self.assertEqual(self._pick([self.RW, self.RO], host="ro.host"), self.RW.name)
        rw2 = _endpoint("projects/gor/branches/production/endpoints/rw2", "rw2.host")
        self.assertEqual(self._pick([self.RW, rw2], host="rw2.host"), rw2.name)

    def test_read_write_beats_list_order(self):
        self.assertEqual(self._pick([self.RO, self.RW], host="other.host"), self.RW.name)

    def test_never_a_random_read_only_endpoint(self):
        self.assertEqual(self._pick([self.RO]), "")

    def test_empty_branch_or_list_error_gives_nothing(self):
        self.assertEqual(self._pick([]), "")
        self.assertEqual(self._pick([self.RW], error=RuntimeError("403")), "")
        self.assertEqual(lc._autoscaling_endpoint("", "x"), "")


class AutoscalingResolutionTest(unittest.TestCase):
    def test_database_name_comes_from_the_resource_when_pgdatabase_is_missing(self):
        with _Env({"LAKEBASE_HOST": EP, "PGHOST": HOST, "PGUSER": "u"}, _client([_postgres_resource()])) as e:
            _, pool = e.run()
        self.assertEqual(pool.kwargs["database"], "databricks_postgres")
        e.client.postgres.get_database.assert_called_once_with(
            "projects/gor/branches/production/databases/databricks-postgres")

    def test_a_leftover_instance_setting_doesnt_beat_an_autoscaling_resource(self):
        with _Env({"LAKEBASE_HOST": HOST, "LAKEBASE_INSTANCE_NAME": "ontos-lakebase", "PGDATABASE": "d",
                   "PGUSER": "u"}, _client([_postgres_resource()], [_endpoint(EP, HOST)])) as e:
            cred, _ = e.run()
        self.assertEqual((cred.kwargs["endpoint_name"], cred.kwargs["instance_name"]), (EP, ""))

    def test_the_target_is_resolved_once_and_reused_on_refresh(self):
        with _Env({"LAKEBASE_HOST": HOST, "PGDATABASE": "d", "PGUSER": "u"},
                  _client([_postgres_resource()], [_endpoint(EP, HOST)])) as e:
            asyncio.run(lc.init_pool())
            asyncio.run(lc.init_pool())
            self.assertEqual(e.client.postgres.list_endpoints.call_count, 1)

    def test_an_autoscaling_resource_without_an_endpoint_gets_no_instance_credential(self):
        with _Env({"LAKEBASE_HOST": HOST, "LAKEBASE_INSTANCE_NAME": "ontos-lakebase", "PGDATABASE": "d",
                   "PGUSER": "u"}, _client([_postgres_resource()], [])):
            asyncio.run(lc.init_pool())
            lc._fetch_db_credential.assert_not_awaited()  # never an instance credential
            self.assertIsNone(lc._pool)

    def test_a_failed_start_forgets_the_target(self):
        with _Env({"LAKEBASE_HOST": EP, "PGHOST": HOST, "PGDATABASE": "d", "PGUSER": "u"},
                  _client([_postgres_resource()])) as e:
            e.create_pool.side_effect = OSError("Name or service not known")
            asyncio.run(lc.init_pool())
            self.assertIsNone(lc._pool)
            self.assertIsNone(lc._TARGET)

    def test_a_failed_lookup_is_not_cached(self):
        w = _client([_postgres_resource()], [_endpoint(EP, HOST)])
        w.postgres.list_endpoints.side_effect = [RuntimeError("starting"), [_endpoint(EP, HOST)]]
        with _Env({"LAKEBASE_HOST": HOST, "PGDATABASE": "d", "PGUSER": "u"}, w):
            asyncio.run(lc.init_pool())
            self.assertIsNone(lc._pool)
            asyncio.run(lc.init_pool())  # e.g. the next start: resolved again, now it works
            self.assertIsNotNone(lc._pool)

    def test_pghost_holding_an_endpoint_name_is_not_used_as_the_host(self):
        with _Env({"PGHOST": EP, "PGDATABASE": "d", "PGUSER": "u"}, _client([_postgres_resource()])) as e:
            cred, pool = e.run()
        self.assertEqual(cred.kwargs["endpoint_name"], EP)
        self.assertEqual(pool.kwargs["host"], HOST)  # read from the endpoint


class CredentialTest(unittest.TestCase):
    ENV = {"LAKEBASE_HOST": EP, "PGHOST": HOST, "PGDATABASE": "d", "PGUSER": "u"}

    def test_the_pool_mints_a_credential_per_new_connection(self):
        with _Env(self.ENV, _client([_postgres_resource()])) as e:
            _, pool = e.run()
            self.assertIs(pool.kwargs["password"], lc._connection_password)
            self.assertEqual(pool.kwargs["max_inactive_connection_lifetime"], 300)
            self.assertEqual(asyncio.run(lc._connection_password()), "tok")
            self.assertEqual(lc._fetch_db_credential.await_count, 1)  # still fresh: reused

    def test_an_old_credential_is_replaced_for_new_connections(self):
        with _Env(self.ENV, _client([_postgres_resource()])) as e:
            e.run()
            lc._credential["minted_at"] -= lc.CREDENTIAL_MAX_AGE + 1
            lc._fetch_db_credential.return_value = "tok2"
            self.assertEqual(asyncio.run(lc._connection_password()), "tok2")

    def test_a_failed_re_mint_keeps_the_current_credential(self):
        with _Env(self.ENV, _client([_postgres_resource()])) as e:
            e.run()
            lc._credential["minted_at"] -= lc.CREDENTIAL_MAX_AGE + 1
            lc._fetch_db_credential.return_value = None
            self.assertEqual(asyncio.run(lc._connection_password()), "tok")

    def test_no_first_credential_means_no_pool(self):
        with _Env(self.ENV, _client([_postgres_resource()])) as e:
            lc._fetch_db_credential.return_value = None
            asyncio.run(lc.init_pool())
            self.assertIsNone(lc._pool)
            e.create_pool.assert_not_awaited()


class _Conn:
    """A connection object the fake pool hands out (only identity matters)."""


class _FakePool:
    """Stands in for an asyncpg pool: each acquire() gives the next outcome (a connection, or an
    exception to raise). Records the acquire timeouts and the released connections. ``wait``
    is how long acquire blocks before it yields (a busy pool); a ``timeout`` shorter than that
    fails it, as asyncpg does."""

    def __init__(self, outcomes, wait=0.0, password=None):
        self.outcomes = list(outcomes)
        self.wait = wait
        self.password = password  # asyncpg calls it inside the connect timeout for a new connection
        self.timeouts, self.released = [], []

    def acquire(self, timeout=None):
        self.timeouts.append(timeout)
        return _FakeAcquire(self, self.outcomes.pop(0))


class _FakeAcquire:
    def __init__(self, pool, outcome):
        self.pool, self.outcome, self.conn = pool, outcome, None

    async def __aenter__(self):
        timeout = self.pool.timeouts[-1]
        if timeout is not None and self.pool.wait > timeout:
            raise asyncio.TimeoutError()
        await asyncio.sleep(self.pool.wait)
        if self.pool.password is not None:
            await asyncio.wait_for(self.pool.password(), lc.CONNECT_TIMEOUT)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        self.conn = self.outcome
        return self.conn

    async def __aexit__(self, *exc):
        self.pool.released.append(self.conn)


class ConnectionRetryTest(unittest.TestCase):
    def setUp(self):
        self.conn = _Conn()
        self.patch = patch.object(lc, "RETRY_DELAYS", (0.0, 0.0))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        lc._pool = None

    def _run_op(self, pool):
        async def op():
            async with lc.lakebase_connection(pool) as conn:
                return conn
        return asyncio.run(op())

    def test_a_connect_refused_while_the_endpoint_wakes_is_retried_then_succeeds(self):
        pool = _FakePool([ConnectionRefusedError("waking"), self.conn])
        with self.assertLogs(lc.logger, "INFO") as logs:
            got = self._run_op(pool)
        self.assertIs(got, self.conn)
        self.assertEqual(pool.released, [self.conn])  # released after use
        self.assertEqual(pool.timeouts, [None, None])  # acquire itself is never bounded
        warnings = [r for r in logs.records if r.levelname == "WARNING"]
        self.assertEqual(len(warnings), 1)  # one WARNING per retried failure
        self.assertIn("attempt 2 of 3", warnings[0].getMessage())
        self.assertTrue(any("succeeded on attempt 2" in r.getMessage() for r in logs.records))

    def test_each_transient_kind_is_retried(self):
        for error in (asyncio.TimeoutError(), OSError("reset"), asyncpg.CannotConnectNowError("starting"),
                      asyncpg.TooManyConnectionsError("full"), asyncpg.ConnectionDoesNotExistError("gone")):
            with self.subTest(error=type(error).__name__):
                pool = _FakePool([error, self.conn])
                with self.assertLogs(lc.logger, "WARNING"):
                    self.assertIs(self._run_op(pool), self.conn)
                self.assertEqual(len(pool.timeouts), 2)

    def test_an_auth_failure_is_not_retried(self):
        pool = _FakePool([asyncpg.InvalidPasswordError("bad token"), self.conn])
        with self.assertRaises(asyncpg.InvalidPasswordError):
            self._run_op(pool)
        self.assertEqual(len(pool.timeouts), 1)

    def test_sql_and_tls_errors_are_not_retried(self):
        for error in (asyncpg.InsufficientPrivilegeError("no grant"), asyncpg.PostgresSyntaxError("bad sql"),
                      ssl.SSLError("certificate verify failed")):
            with self.subTest(error=type(error).__name__):
                pool = _FakePool([error, self.conn])
                with self.assertRaises(type(error)):
                    self._run_op(pool)
                self.assertEqual(len(pool.timeouts), 1)

    def test_exhausted_retries_raise_after_three_attempts(self):
        pool = _FakePool([ConnectionRefusedError("down")] * 3)
        with self.assertLogs(lc.logger, "WARNING") as warnings:
            with self.assertRaises(ConnectionRefusedError):
                self._run_op(pool)
        self.assertEqual(len(pool.timeouts), 3)
        self.assertEqual(len(warnings.records), 2)  # the last failure is raised, not logged here

    def test_a_slow_credential_mint_does_not_fail_the_connect(self):
        # The cached credential is stale and the credentials call is slower than the connect bound.
        # The mint runs before the connect, so the connect still succeeds.
        async def slow_mint(**kwargs):
            await asyncio.sleep(0.05)
            return "fresh"

        pool = _FakePool([self.conn], password=lc._connection_password)
        env = {"LAKEBASE_HOST": EP, "PGHOST": HOST, "PGDATABASE": "d", "PGUSER": "u"}
        with _Env(env, _client([_postgres_resource()])), \
                patch.object(lc, "CONNECT_TIMEOUT", 0.01), patch.object(lc, "_fetch_db_credential", slow_mint):
            lc._TARGET = {"host": HOST, "port": 5432, "database": "d", "user": "u", "endpoint": EP, "instance": ""}
            lc._credential.update(token="old", minted_at=0.0)  # older than CREDENTIAL_MAX_AGE
            self.assertIs(self._run_op(pool), self.conn)
            self.assertEqual(lc._credential["token"], "fresh")

    def test_a_failed_mint_is_not_retried_during_the_cooldown(self):
        mint = AsyncMock(return_value=None)
        env = {"LAKEBASE_HOST": EP, "PGHOST": HOST, "PGDATABASE": "d", "PGUSER": "u"}
        with _Env(env, _client([_postgres_resource()])), patch.object(lc, "_fetch_db_credential", mint):
            lc._TARGET = {"host": HOST, "port": 5432, "database": "d", "user": "u", "endpoint": EP, "instance": ""}
            lc._credential.update(token="old", minted_at=0.0)
            asyncio.run(lc._refresh_credential())
            asyncio.run(lc._refresh_credential())  # within MINT_RETRY_SECONDS: no second call
            self.assertEqual(mint.await_count, 1)
            self.assertEqual(lc._credential["token"], "old")  # a failed mint keeps the current one

    def test_a_new_connection_does_not_repeat_a_failed_mint_during_the_cooldown(self):
        mint = AsyncMock(side_effect=[None, "fresh"])
        env = {"LAKEBASE_HOST": EP, "PGHOST": HOST, "PGDATABASE": "d", "PGUSER": "u"}
        with _Env(env, _client([_postgres_resource()])), patch.object(lc, "_fetch_db_credential", mint):
            lc._TARGET = {"host": HOST, "port": 5432, "database": "d", "user": "u", "endpoint": EP, "instance": ""}
            lc._credential.update(token="old", minted_at=0.0)  # stale
            self.assertEqual(asyncio.run(lc._connection_password()), "old")  # the mint fails: current kept
            self.assertEqual(asyncio.run(lc._connection_password()), "old")  # cooldown: no second mint
            self.assertEqual(mint.await_count, 1)
            lc._credential["retry_after"] = time.time() - 1  # the cooldown has passed
            self.assertEqual(asyncio.run(lc._connection_password()), "fresh")
            self.assertEqual(mint.await_count, 2)

    def test_a_busy_pool_is_waited_for_not_treated_as_a_connection_failure(self):
        # The pool yields a connection only after longer than the connect bound: pool contention.
        # It must succeed on the first attempt, with no retry and no WARNING.
        pool = _FakePool([self.conn], wait=0.05)
        with patch.object(lc, "CONNECT_TIMEOUT", 0.01), patch.object(lc.logger, "warning") as warning:
            self.assertIs(self._run_op(pool), self.conn)
        self.assertEqual(pool.timeouts, [None])
        warning.assert_not_called()

    def test_a_caller_still_degrades_to_empty_when_retries_are_exhausted(self):
        from server import snapshots
        lc._pool = _FakePool([ConnectionRefusedError("down")] * 3)
        with patch.object(snapshots, "USE_LAKEBASE", True), self.assertLogs("server.snapshots", "WARNING") as logs:
            self.assertEqual(asyncio.run(snapshots.list_snapshots("someone@example.com")), [])
        self.assertIn("list_snapshots failed", logs.output[0])


    def test_a_query_without_a_pool_raises(self):
        lc._pool = None
        with self.assertRaises(RuntimeError):
            asyncio.run(lc.execute_query("SELECT 1"))


class PoolStartRetryTest(unittest.TestCase):
    ENV = {"LAKEBASE_HOST": EP, "PGHOST": HOST, "PGDATABASE": "d", "PGUSER": "u"}

    def test_a_pool_start_that_fails_while_the_endpoint_wakes_is_retried(self):
        pool = object()
        with _Env(self.ENV, _client([_postgres_resource()])) as e:
            e.create_pool.side_effect = [ConnectionRefusedError("waking"), pool]
            asyncio.run(lc.init_pool())
            self.assertIs(lc._pool, pool)
        self.assertEqual(e.create_pool.await_count, 2)

    def test_new_connections_are_bounded_by_the_connect_timeout(self):
        with _Env(self.ENV, _client([_postgres_resource()])) as e:
            asyncio.run(lc.init_pool())
        self.assertEqual(e.create_pool.await_args.kwargs["timeout"], lc.CONNECT_TIMEOUT)

    def test_a_pool_start_with_bad_auth_is_not_retried(self):
        with _Env(self.ENV, _client([_postgres_resource()])) as e:
            e.create_pool.side_effect = asyncpg.InvalidPasswordError("bad token")
            asyncio.run(lc.init_pool())
            self.assertIsNone(lc._pool)
        self.assertEqual(e.create_pool.await_count, 1)

    def test_a_pool_that_never_starts_is_forgotten_after_the_retries(self):
        with _Env(self.ENV, _client([_postgres_resource()])) as e:
            e.create_pool.side_effect = ConnectionRefusedError("down")
            asyncio.run(lc.init_pool())
            self.assertIsNone(lc._pool)
            self.assertIsNone(lc._TARGET)
            self.assertEqual(e.create_pool.await_count, 3)


class ProvisionedTest(unittest.TestCase):
    def test_provisioned_resource_still_uses_the_instance(self):
        with _Env({"LAKEBASE_HOST": "instance-x.database.cloud.databricks.com", "PGDATABASE": "ontology_readiness_dev",
                   "PGUSER": "u"}, _client([_database_resource()])) as e:
            cred, pool = e.run()
        self.assertEqual(cred.kwargs["instance_name"], "ontos-lakebase")
        self.assertEqual(cred.kwargs["endpoint_name"], "")
        self.assertEqual(pool.kwargs["host"], "instance-x.database.cloud.databricks.com")

    def test_no_resource_means_no_credential(self):
        with _Env({"LAKEBASE_HOST": HOST, "PGDATABASE": "d", "PGUSER": "u"}, _client([])):
            asyncio.run(lc.init_pool())
            lc._fetch_db_credential.assert_not_awaited()
            self.assertIsNone(lc._pool)


if __name__ == "__main__":
    unittest.main()
