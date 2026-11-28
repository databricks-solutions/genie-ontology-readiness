"""Preview command contracts; all runtime values are placeholders."""
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pr_preview  # noqa: E402

NAME = "genie-ontology-readiness-pr-123"
REMOTE = f"/Workspace/Shared/.gor-pr-previews/{NAME}"
RESOURCES = [{"name": "sql-warehouse", "sql_warehouse": {"id": "<warehouse-id>", "permission": "CAN_USE"}}]


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    for key in ("DATABRICKS_PROFILE", "DATABRICKS_CONFIG_PROFILE", "WAREHOUSE_ID", "DATABRICKS_WAREHOUSE_ID"):
        monkeypatch.delenv(key, raising=False)


def commands(output):
    return [shlex.split(line[2:]) for line in output.splitlines() if line.startswith("$ databricks ")]


def test_up_dry_run(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("dry-run executed a subprocess"))
    assert pr_preview.main(["up", "--pr-number", "00123", "--profile", "<profile>",
                            "--warehouse-id", "<warehouse-id>", "--dry-run"]) == 0
    output = capsys.readouterr().out
    actual = commands(output)
    suffix = ["--profile", "<profile>"]
    stage = actual[2][3]
    assert actual == [
        ["databricks", "apps", "get", NAME, "--output", "json", *suffix],
        ["databricks", "apps", "create", NAME, "--json", json.dumps({"name": NAME, "resources": RESOURCES}), *suffix],
        ["databricks", "sync", "--full", stage, REMOTE, *suffix],
        ["databricks", "apps", "deploy", NAME, "--source-code-path", REMOTE,
         "--mode", "SNAPSHOT", "--auto-approve", *suffix],
        ["databricks", "apps", "get", NAME, "--output", "json", *suffix],
    ]
    assert "npm run build" in output
    assert "otherwise skip create" in output


@pytest.mark.parametrize("profile_env", ["DATABRICKS_PROFILE", "DATABRICKS_CONFIG_PROFILE"])
@pytest.mark.parametrize("warehouse_env", ["WAREHOUSE_ID", "DATABRICKS_WAREHOUSE_ID"])
def test_env_and_arg_precedence(monkeypatch, capsys, profile_env, warehouse_env):
    monkeypatch.setenv(profile_env, "<profile>")
    monkeypatch.setenv(warehouse_env, "<warehouse-id>")
    assert pr_preview.main(["up", "--pr-number", "123", "--dry-run"]) == 0
    assert commands(capsys.readouterr().out)[1][-1] == "<profile>"
    assert pr_preview.main(["up", "--pr-number", "123", "--dry-run", "--profile", "<override-profile>",
                            "--warehouse-id", "<override-warehouse-id>"]) == 0
    create = commands(capsys.readouterr().out)[1]
    assert create[-1] == "<override-profile>"
    assert json.loads(create[5])["resources"][0]["sql_warehouse"]["id"] == "<override-warehouse-id>"


def test_down_dry_run(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("dry-run executed a subprocess"))
    assert pr_preview.main(["down", "--pr-number", "123", "--profile", "<profile>", "--dry-run"]) == 0
    assert commands(capsys.readouterr().out) == [
        ["databricks", "apps", "delete", NAME, "--profile", "<profile>"],
        ["databricks", "workspace", "delete", REMOTE, "--recursive", "--profile", "<profile>"],
    ]


@pytest.mark.parametrize("value", ["0", "-1", "dev", "1/../../prod", "1.5"])
def test_reject_invalid_pr(value):
    with pytest.raises(SystemExit):
        pr_preview.main(["down", "--pr-number", value, "--profile", "<profile>", "--dry-run"])


@pytest.mark.parametrize("args", [["down"], ["up", "--profile", "<profile>"]])
def test_required_settings(args):
    with pytest.raises(SystemExit):
        pr_preview.main([*args, "--pr-number", "123", "--dry-run"])


@pytest.mark.parametrize("exists", [True, False])
def test_up_idempotency_and_staging(monkeypatch, tmp_path, capsys, exists):
    source = tmp_path / "app"
    (source / "frontend" / "dist").mkdir(parents=True)
    (source / "frontend" / "dist" / "index.html").write_text("built")
    (source / "package.json").write_text("{}")
    (source / "node_modules").mkdir()
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["databricks", "apps", "get"]:
            if not exists and len(calls) == 2:
                return subprocess.CompletedProcess(cmd, 1, "", "RESOURCE_DOES_NOT_EXIST")
            return subprocess.CompletedProcess(cmd, 0, json.dumps({
                "name": NAME, "resources": RESOURCES, "url": "<app-url>",
            }), "")
        if cmd[:2] == ["databricks", "sync"]:
            stage = Path(cmd[3])
            assert (stage / "frontend/dist/index.html").read_text() == "built"
            assert not (stage / "node_modules").exists()
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert pr_preview.main(["up", "--pr-number", "123", "--profile", "<profile>",
                            "--warehouse-id", "<warehouse-id>", "--source-path", str(source)]) == 0
    assert sum(cmd[:3] == ["databricks", "apps", "create"] for cmd in calls) == (0 if exists else 1)
    assert calls[0] == ["npm", "run", "build"]
    assert capsys.readouterr().out.rstrip().endswith("<app-url>")


@pytest.mark.parametrize("error,expected", [("RESOURCE_DOES_NOT_EXIST", 0), ("PERMISSION_DENIED", 1)])
def test_down_missing_and_failure(monkeypatch, error, expected):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", error))
    assert pr_preview.main(["down", "--pr-number", "123", "--profile", "<profile>"]) == expected


def test_get_failure_is_not_absence(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "PERMISSION_DENIED"))
    with pytest.raises(RuntimeError):
        pr_preview.CLI("<profile>", False).get_app(NAME)


@pytest.mark.parametrize("error", ["RESOURCE_DOES_NOT_EXIST", f"App {NAME} does not exist"])
def test_get_absent(monkeypatch, error):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", error))
    assert pr_preview.CLI("<profile>", False).get_app(NAME) is None


def test_profile_not_found_is_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw:
                        subprocess.CompletedProcess(cmd, 1, "", "profile <profile> not found"))
    with pytest.raises(RuntimeError):
        pr_preview.CLI("<profile>", False).get_app(NAME)


def test_existing_resources_checked():
    assert pr_preview.matching_resources(RESOURCES, "<warehouse-id>")
    assert not pr_preview.matching_resources(RESOURCES, "<other-warehouse-id>")
    assert not pr_preview.matching_resources([*RESOURCES, {"name": "<extra-resource>"}], "<warehouse-id>")


def test_bad_source_fails_before_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("invalid source executed a subprocess"))
    assert pr_preview.main(["up", "--pr-number", "123", "--profile", "<profile>",
                            "--warehouse-id", "<warehouse-id>", "--source-path", str(tmp_path)]) == 1
