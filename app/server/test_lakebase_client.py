"""Lakebase connection: Provisioned (`database`) and Autoscaling (`postgres`) lakebase-db resources."""
import asyncio
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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
        lc._credential.update(token="", minted_at=0.0)
        self.patches = [
            patch.dict(os.environ, {"DATABRICKS_APP_NAME": "gor-test", **self.env}, clear=True),
            patch("server.config._get_workspace_client", return_value=self.client),
            patch.object(lc, "_fetch_db_credential", AsyncMock(return_value="tok")),
        ]
        for p in self.patches:
            p.start()
        self.create_pool = AsyncMock(return_value=object())
        self.asyncpg = patch.dict(sys.modules, {"asyncpg": types.SimpleNamespace(create_pool=self.create_pool)})
        self.asyncpg.start()
        return self

    def __exit__(self, *a):
        self.asyncpg.stop()
        for p in reversed(self.patches):
            p.stop()
        lc._RESOURCE = None
        lc._TARGET = None
        lc._pool = None
        lc._credential.update(token="", minted_at=0.0)

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

    def test_the_host_we_connect_to_wins_even_if_read_only(self):
        self.assertEqual(self._pick([self.RW, self.RO], host="ro.host"), self.RO.name)

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
