"""Offline tests of contributor preview selection and command orchestration."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import preview  # noqa: E402


@pytest.mark.parametrize("inputs, expected", [
    ({"pr": "00159", "slug": "ignored", "branch": "ignored"}, "159"),
    ({"slug": " feature-pr "}, "feature-pr"),
    ({"slug": "a"}, "a"),
    ({"branch": "Feature/Test_Branch"}, "feature-test-branch"),
    ({"branch": "--Fix///BUG--"}, "fix-bug"),
    ({"slug": "a" * 23}, "a" * 23),
])
def test_derive_slug(inputs, expected):
    assert preview.derive_slug(**inputs) == expected


@pytest.mark.parametrize("inputs", [
    {"pr": "0"}, {"pr": "-1"}, {"pr": "12x"}, {"slug": ""}, {"slug": " \t"},
    {"slug": "-"}, {"slug": "-a"}, {"slug": "a-"}, {"slug": "ABC"}, {"slug": "a_b"}, {"slug": "Feature/X"}, {"slug": "a" * 24},
    {"branch": "HEAD"}, {"branch": ""}, {"branch": "///"}, {"branch": "long/" * 10},
])
def test_invalid_slug(inputs):
    with pytest.raises(ValueError):
        preview.derive_slug(**inputs)


def _commands(monkeypatch, env, *, fail=None, auth_ok=True):
    # Orchestration tests never remove the real checkout cache.
    monkeypatch.setattr(preview, "clear_local_preview_state", lambda: None)
    monkeypatch.setattr(preview.os, "environ", dict(env))
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if fail and fail(cmd):
            raise subprocess.CalledProcessError(1, cmd)
        output = ""
        rc = 0
        if cmd[:3] == ["databricks", "current-user", "me"]:
            rc = 0 if auth_ok else 1
        if cmd[:3] == ["git", "branch", "--show-current"]:
            output = "Feature/My_PR\n"
        if cmd[:3] == ["databricks", "apps", "get"]:
            output = json.dumps({"url": "https://preview.example.test"})
        return subprocess.CompletedProcess(cmd, rc, stdout=output, stderr="auth unavailable" if rc else "")

    monkeypatch.setattr(preview.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("profile_args, env, expected", [
    (["--profile", "contributor"], {"DATABRICKS_PROFILE": "other"}, ["--profile", "contributor"]),
    ([], {"DATABRICKS_PROFILE": "env-profile"}, ["--profile", "env-profile"]),
    ([], {"DATABRICKS_HOST": "https://workspace.example.test", "DATABRICKS_TOKEN": "test"}, []),
    ([], {}, []),
])
def test_deploy_auth_and_stateless_environment(monkeypatch, capsys, profile_args, env, expected):
    env.update(WAREHOUSE_ID="wh-in-chosen-workspace", USE_LAKEBASE="true",
               LAKEBASE_INSTANCE_NAME="stale", LAKEBASE_ENDPOINT_NAME="stale", TARGET="prod")
    calls = _commands(monkeypatch, env)
    preview.main(["deploy", "--pr", "159", *profile_args])
    cmds = [cmd for cmd, kwargs in calls]
    assert cmds[0] == ["databricks", "current-user", "me", *expected, "--output", "json"]
    assert cmds[1:3] == [["npm", "ci"], ["npm", "run", "build"]]
    assert cmds[3] == [sys.executable, str(preview.ROOT / "scripts" / "post_deploy.py")]
    deployed = calls[3][1]["env"]
    assert deployed["TARGET"] == "preview"
    assert deployed["PREVIEW_SLUG"] == "159"
    assert deployed["WAREHOUSE_ID"] == "wh-in-chosen-workspace"
    assert deployed["USE_LAKEBASE"] == "false"
    assert deployed["LAKEBASE_INSTANCE_NAME"] == deployed["LAKEBASE_ENDPOINT_NAME"] == ""
    assert deployed["BUNDLE_VAR_lakebase_instance_name"] == ""
    assert deployed["ASSESS_CATALOGS"] == deployed["GENIE_SPACE_ID"] == ""
    assert deployed["INFRA"] == deployed["RESET_SETTINGS"] == "true"
    assert cmds[-1] == ["databricks", "apps", "get", "gor-pr-159", *expected, "--output", "json"]
    assert capsys.readouterr().out.rstrip().endswith("https://preview.example.test")
    if "DATABRICKS_HOST" in env:
        assert deployed["DATABRICKS_HOST"] == env["DATABRICKS_HOST"]


def test_deploy_preserves_optional_inputs(monkeypatch):
    calls = _commands(monkeypatch, {"WAREHOUSE_ID": "wh", "ASSESS_CATALOGS": "my-catalog", "GENIE_SPACE_ID": "space"})
    preview.main(["deploy", "--slug", "my-preview"])
    deployed = calls[3][1]["env"]
    assert deployed["ASSESS_CATALOGS"] == "my-catalog"
    assert deployed["GENIE_SPACE_ID"] == "space"


@pytest.mark.parametrize("args, slug", [(["--pr", "159"], "159"), (["--slug", "feature"], "feature"),
                                       ([], "feature-my-pr")])
def test_destroy_selects_only_preview_without_build_or_warehouse(monkeypatch, args, slug):
    calls = _commands(monkeypatch, {})
    preview.main(["destroy", *args, "--profile", "contributor"])
    assert calls[-1][0] == ["databricks", "bundle", "destroy", "-t", "preview", "--var",
                            f"preview_slug={slug}", "--profile", "contributor"]
    assert all(cmd[0] != "npm" and "post_deploy.py" not in " ".join(cmd) for cmd, kwargs in calls)
    assert "--auto-approve" not in calls[-1][0]


@pytest.mark.parametrize("args, env, auth_ok, message", [
    (["deploy", "--pr", "159"], {}, True, "WAREHOUSE_ID"),
    (["destroy", "--pr", "159", "--profile", ""], {}, True, "--profile must not be empty"),
    (["deploy", "--pr", "159"], {"WAREHOUSE_ID": "wh"}, False, "Cannot authenticate"),
])
def test_missing_inputs_stop_before_mutations(monkeypatch, capsys, args, env, auth_ok, message):
    calls = _commands(monkeypatch, env, auth_ok=auth_ok)
    with pytest.raises(SystemExit) as exc:
        preview.main(args)
    assert exc.value.code != 0
    assert message in capsys.readouterr().err
    assert all(cmd[:3] == ["databricks", "current-user", "me"] for cmd, kwargs in calls)


def test_failed_publish_does_not_report_success_url(monkeypatch, capsys):
    calls = _commands(monkeypatch, {"WAREHOUSE_ID": "wh"}, fail=lambda cmd: cmd[0] == sys.executable)
    with pytest.raises(SystemExit) as exc:
        preview.main(["deploy", "--pr", "159"])
    assert exc.value.code == 1
    assert not any(cmd[:3] == ["databricks", "apps", "get"] for cmd, kwargs in calls)
    assert "https://preview.example.test" not in capsys.readouterr().out


def test_shell_entry_point_help_from_another_directory(tmp_path):
    result = subprocess.run(["bash", str(preview.ROOT / "scripts" / "preview.sh"), "--help"],
                            cwd=tmp_path, env={**preview.os.environ, "PYTHON": sys.executable},
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert "scripts/preview.sh" in result.stdout
    assert "--profile" in result.stdout and "WAREHOUSE_ID" in result.stdout


@pytest.mark.parametrize("approval", [[], ["--yes"], ["--auto-approve"]])
def test_destroy_clears_previous_slug_state_before_each_call(monkeypatch, tmp_path, approval):
    monkeypatch.setattr(preview, "ROOT", tmp_path)
    monkeypatch.setattr(preview.os, "environ", {})
    cache = tmp_path / ".databricks" / "bundle" / "preview"
    other = cache.parent / "dev" / "terraform.tfstate"
    other.parent.mkdir(parents=True)
    other.write_text("shared target must survive")
    calls = []

    def run(cmd, **kwargs):
        if cmd[:3] == ["databricks", "bundle", "destroy"]:
            assert not cache.exists(), "Destroy must not load a different slug's cached state"
            calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(preview.subprocess, "run", run)
    for previous, selected in (("old", "159"), ("159", "160")):
        cache.mkdir(parents=True)
        (cache / "terraform.tfstate").write_text(f"gor-pr-{previous}")
        preview.main(["destroy", "--pr", selected, *approval])
        expected = ["databricks", "bundle", "destroy", "-t", "preview", "--var", f"preview_slug={selected}"]
        if approval:
            expected.append("--auto-approve")
        assert calls[-1] == expected
        assert other.read_text() == "shared target must survive"
    # A fresh slug/cache must also work without any prior local directory.
    preview.main(["destroy", "--pr", "161", *approval])


def test_shell_entry_point_forwards_unattended_destroy_flag(tmp_path):
    interpreter = tmp_path / "record-args"
    interpreter.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    interpreter.chmod(0o755)
    result = subprocess.run(["bash", str(preview.ROOT / "scripts" / "preview.sh"), "destroy", "--pr", "159", "--yes"],
                            cwd=tmp_path, env={**preview.os.environ, "PYTHON": str(interpreter)},
                            capture_output=True, text=True, check=True)
    assert result.stdout.splitlines()[1:] == ["destroy", "--pr", "159", "--yes"]
