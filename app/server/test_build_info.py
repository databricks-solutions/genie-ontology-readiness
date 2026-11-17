import unittest
from unittest.mock import AsyncMock, patch

from server import build_info


class BuildInfoTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        build_info._cached = None

    def tearDown(self):
        build_info._cached = None

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

    async def test_env_sha_wins_but_deployment_fields_still_fill_in(self):
        dep = {"deployment_id": "d1", "git_source": {"commit": "aaaaaaaaaaaa"}}
        with patch.dict("os.environ", {"DATABRICKS_APP_NAME": "gor-dev", "GOR_GIT_SHA": "1234567890ab"}), \
             patch.object(build_info, "_active_deployment", AsyncMock(return_value=dep)):
            info = await build_info.get_build_info()
        self.assertEqual((info["commit"], info["source"], info["deployment_id"]), ("1234567890ab", "env", "d1"))

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
        with patch.object(build_info, "get_workspace_host", side_effect=RuntimeError("no host")):
            self.assertEqual(await build_info._active_deployment("gor-dev"), {})


if __name__ == "__main__":
    unittest.main()
