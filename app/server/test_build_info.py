import asyncio
import contextvars
import threading
import unittest
from unittest.mock import AsyncMock, patch

from databricks.sdk.service.apps import App, AppDeployment, GitSource

from server import build_info
from server._async import run_sync


class BuildInfoTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        build_info._cached = None
        build_info._cached_at = 0.0

    def tearDown(self):
        build_info._cached = None
        build_info._cached_at = 0.0

    async def test_commit_from_the_apps_active_deployment(self):
        dep = {"deployment_id": "01f1", "update_time": "2026-11-17T17:14:51Z",
               "git_source": {"commit": "b5f83a77380e", "resolved_commit": "b5f83a77380e12ef", "branch": "fix/x"}}
        with patch.dict("os.environ", {"DATABRICKS_APP_NAME": "gor-dev", "GOR_GIT_SHA": ""}), \
             patch.object(build_info, "_active_deployment", AsyncMock(return_value=dep)) as get, \
             patch.object(build_info, "_git_sha", return_value="ffffffffffff"):
            info = await build_info.get_build_info()
            again = await build_info.get_build_info()

        get.assert_awaited_once_with("gor-dev")  # resolved once, then cached
        self.assertIs(info, again)
        self.assertEqual(info["commit"], "b5f83a77380e12ef")
        self.assertEqual(info["commit_short"], "b5f83a7")
        self.assertEqual(info["source"], "deployment")
        self.assertEqual(info["ref"], "fix/x")
        self.assertEqual(info["deployment_id"], "01f1")
        self.assertEqual(build_info.build_label(info), f"v{build_info.PRODUCT_VERSION} · b5f83a7")

    async def test_env_sha_wins_and_is_not_paired_with_the_deployments_branch(self):
        dep = {"deployment_id": "d1", "git_source": {"commit": "aaaaaaaaaaaa", "branch": "fix/x"}}
        with patch.dict("os.environ", {"DATABRICKS_APP_NAME": "gor-dev", "GOR_GIT_SHA": "1234567890ab"}), \
             patch.object(build_info, "_active_deployment", AsyncMock(return_value=dep)), \
             self.assertLogs(build_info.logger, "WARNING"):
            info = await build_info.get_build_info()
        self.assertEqual((info["commit"], info["source"], info["deployment_id"]), ("1234567890ab", "env", "d1"))
        self.assertIsNone(info["ref"])

    async def test_a_lookup_without_a_commit_is_retried_later(self):
        lookup = AsyncMock(side_effect=[{}, {"git_source": {"commit": "b5f83a77380e"}}])
        with patch.dict("os.environ", {"DATABRICKS_APP_NAME": "gor-dev", "GOR_GIT_SHA": ""}), \
             patch.object(build_info, "_active_deployment", lookup), \
             patch.object(build_info, "_git_sha", return_value=None):
            first = await build_info.get_build_info()
            self.assertIsNone(first["commit"])
            self.assertIs(await build_info.get_build_info(), first)  # within the retry window
            build_info._cached_at -= build_info._RETRY_SECONDS + 1
            second = await build_info.get_build_info()
        self.assertEqual(second["commit_short"], "b5f83a7")
        self.assertEqual(lookup.await_count, 2)

    async def test_falls_back_to_git_then_to_version_only(self):
        with patch.dict("os.environ", {"DATABRICKS_APP_NAME": "", "GOR_GIT_SHA": ""}), \
             patch.object(build_info, "_git_sha", return_value="cafebabe1234"):
            info = await build_info.get_build_info()
        self.assertEqual((info["commit_short"], info["source"]), ("cafebab", "git"))

        build_info._cached = None
        with patch.dict("os.environ", {"DATABRICKS_APP_NAME": "", "GOR_GIT_SHA": ""}), \
             patch.object(build_info, "_git_sha", return_value=None):
            info = await build_info.get_build_info()
        self.assertIsNone(info["commit"])
        self.assertEqual(build_info.build_label(info), f"v{build_info.PRODUCT_VERSION}")

    async def test_deployment_lookup_failure_is_not_fatal(self):
        with patch.object(build_info, "_get_workspace_client") as client:
            client.return_value.apps.get.side_effect = RuntimeError("lookup failed")
            self.assertEqual(await build_info._active_deployment("gor-dev"), {})
        client.return_value.apps.get.assert_called_once_with("gor-dev")

    async def test_slow_sdk_lookup_returns_empty_within_caller_deadline(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def slow_get(name):
            started.set()
            try:
                release.wait(timeout=15)
                return App(name=name)
            finally:
                finished.set()

        with patch.object(build_info, "_get_workspace_client") as client:
            client.return_value.apps.get.side_effect = slow_get
            try:
                with self.assertLogs(build_info.logger, "INFO") as logs:
                    result = await asyncio.wait_for(build_info._active_deployment("gor-dev"), timeout=6)
                self.assertEqual(result, {})
                self.assertTrue(started.is_set())
                self.assertFalse(finished.is_set())  # caller returned while the SDK was still blocked
                self.assertIn("TimeoutError", logs.output[0])
                client.return_value.apps.get.assert_called_once_with("gor-dev")
            finally:
                release.set()
                self.assertTrue(await run_sync(finished.wait, 1))

    async def test_sdk_active_deployment_preserves_fields_and_request_context(self):
        expected = {
            "deployment_id": "01f1",
            "create_time": "2026-11-17T17:00:00Z",
            "update_time": "2026-11-17T17:14:51Z",
            "git_source": {"resolved_commit": "b5f83a77380e12ef", "commit": "b5f83a77380e",
                           "branch": "fix/x", "tag": "v1.0"},
        }
        app = App(name="gor-dev", active_deployment=AppDeployment(
            deployment_id=expected["deployment_id"], create_time=expected["create_time"],
            update_time=expected["update_time"], git_source=GitSource(**expected["git_source"]),
        ))
        viewer = contextvars.ContextVar("viewer")
        token = viewer.set("request-context")
        loop_thread = threading.get_ident()

        def get_app(name):
            self.assertEqual(name, "gor-dev")
            self.assertNotEqual(threading.get_ident(), loop_thread)
            self.assertEqual(viewer.get(), "request-context")
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
            return app

        def get_client():
            self.assertNotEqual(threading.get_ident(), loop_thread)
            self.assertEqual(viewer.get(), "request-context")
            return client.return_value

        try:
            with patch.object(build_info, "_get_workspace_client") as client:
                client.side_effect = get_client
                client.return_value.apps.get.side_effect = get_app
                self.assertEqual(await build_info._active_deployment("gor-dev"), expected)
            client.assert_called_once_with()
            client.return_value.apps.get.assert_called_once_with("gor-dev")
        finally:
            viewer.reset(token)

    async def test_sdk_lookup_without_app_or_active_deployment(self):
        for app in (None, App(name="gor-dev")):
            with self.subTest(app=app), patch.object(build_info, "_get_workspace_client") as client:
                client.return_value.apps.get.return_value = app
                self.assertEqual(await build_info._active_deployment("gor-dev"), {})

    async def test_sdk_client_initialization_failure_is_not_fatal(self):
        with patch.object(build_info, "_get_workspace_client", side_effect=RuntimeError("no config")):
            self.assertEqual(await build_info._active_deployment("gor-dev"), {})

    async def test_bridge_supports_arguments_and_isolates_context_changes(self):
        value = contextvars.ContextVar("bridge-value", default="unset")
        token = value.set("caller")

        def blocking(prefix, *, suffix):
            result = prefix + value.get() + suffix
            value.set("worker")
            return result

        try:
            self.assertEqual(await run_sync(blocking, "[", suffix="]"), "[caller]")
            self.assertEqual(value.get(), "caller")
        finally:
            value.reset(token)


if __name__ == "__main__":
    unittest.main()
