"""Unit tests for the per-environment app-name resolution in scripts/post_deploy.py.

These pin the isolation guarantee behind the DABs hardening: a deploy to one
target must resolve ONLY that target's app name, never another environment's
(above all never the bare prod name for a dev/stg deploy), and an explicit
APP_NAME env must never redirect the run. They also pin the exact
`bundle summary` JSON key path resolve_app_name() depends on, so a CLI/shape
drift that would make the "read the name back from the bundle" guarantee inert
fails a test instead of silently always using the fallback.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import post_deploy  # noqa: E402


def _bundle_summary(name):
    return {"resources": {"apps": {"ontology_readiness": {"name": name}}}}


def test_valid_targets_matches_yaml():
    config = yaml.safe_load((Path(__file__).resolve().parent.parent / "databricks.yml").read_text())
    assert set(post_deploy.VALID_TARGETS) == set(config["targets"])
    assert "preview" in post_deploy.VALID_TARGETS


def test_fallback_prod_is_bare(monkeypatch):
    monkeypatch.setattr(post_deploy, "TARGET", "prod")
    assert post_deploy._fallback_app_name() == "genie-ontology-readiness"


def test_fallback_dev_stg_are_suffixed(monkeypatch):
    monkeypatch.setattr(post_deploy, "TARGET", "dev")
    assert post_deploy._fallback_app_name() == "genie-ontology-readiness-dev"
    monkeypatch.setattr(post_deploy, "TARGET", "stg")
    assert post_deploy._fallback_app_name() == "genie-ontology-readiness-stg"


def test_resolve_reads_bundle_summary(monkeypatch):
    """The bundle summary is the source of truth (pins the JSON key path)."""
    monkeypatch.setattr(post_deploy, "TARGET", "stg")
    monkeypatch.setattr(post_deploy, "WAREHOUSE_ID", "")
    monkeypatch.setattr(post_deploy, "cli_json",
                        lambda *a: _bundle_summary("genie-ontology-readiness-stg"))
    assert post_deploy.resolve_app_name() == "genie-ontology-readiness-stg"


def test_resolve_falls_back_to_target_never_prod(monkeypatch):
    """If bundle summary can't be read, a dev deploy must NOT resolve to prod."""
    monkeypatch.setattr(post_deploy, "TARGET", "dev")
    monkeypatch.setattr(post_deploy, "WAREHOUSE_ID", "")
    monkeypatch.setattr(post_deploy, "cli_json", lambda *a: None)
    assert post_deploy.resolve_app_name() == "genie-ontology-readiness-dev"


def test_resolve_ignores_explicit_app_name_env(monkeypatch):
    """A stale APP_NAME (e.g. the prod name) must never redirect a dev deploy."""
    monkeypatch.setattr(post_deploy, "TARGET", "dev")
    monkeypatch.setattr(post_deploy, "WAREHOUSE_ID", "")
    monkeypatch.setattr(post_deploy, "cli_json",
                        lambda *a: _bundle_summary("genie-ontology-readiness-dev"))
    monkeypatch.setenv("APP_NAME", "genie-ontology-readiness")  # stale prod name
    assert post_deploy.resolve_app_name() == "genie-ontology-readiness-dev"


@pytest.mark.parametrize("slug", ["159", "feature-test", "legit-pr", "a" * 23])
def test_preview_resolves_from_bundle_summary(monkeypatch, slug):
    monkeypatch.setattr(post_deploy, "TARGET", "preview")
    monkeypatch.setattr(post_deploy, "WAREHOUSE_ID", "")
    monkeypatch.setenv("APP_NAME", "genie-ontology-readiness")
    monkeypatch.setattr(post_deploy, "PREVIEW_SLUG", slug)
    name = f"gor-pr-{slug}"

    def summary(*args):
        assert args == ("bundle", "summary", "-t", "preview", f"--var=preview_slug={slug}")
        return _bundle_summary(name)

    monkeypatch.setattr(post_deploy, "cli_json", summary)
    assert post_deploy.resolve_app_name() == name


@pytest.mark.parametrize("summary", [None, {}, _bundle_summary("")])
def test_preview_requires_bundle_summary(monkeypatch, summary):
    monkeypatch.setattr(post_deploy, "PREVIEW_SLUG", "159")
    monkeypatch.setattr(post_deploy, "TARGET", "preview")
    monkeypatch.setattr(post_deploy, "WAREHOUSE_ID", "")
    monkeypatch.setattr(post_deploy, "cli_json", lambda *args: summary)
    with pytest.raises(SystemExit, match="preview app name must be resolved from bundle summary"):
        post_deploy.resolve_app_name()


@pytest.mark.parametrize("slug, message", [
    ("", "non-empty"), (" ", "non-empty"), ("\t\n", "non-empty"),
    ("Feature/X", "lowercase"), ("a_b", "lowercase"), ("ABC", "lowercase"),
    ("a" * 24, "at most 23 characters"),
    ("-", "start/end"), ("a-", "start/end"), ("-a", "start/end"),
])
def test_preview_rejects_invalid_slug(monkeypatch, slug, message):
    monkeypatch.setattr(post_deploy, "PREVIEW_SLUG", slug)
    monkeypatch.setattr(post_deploy, "TARGET", "preview")
    monkeypatch.setattr(post_deploy, "WAREHOUSE_ID", "")
    def unexpected_summary(*args):
        pytest.fail("Invalid slug must be rejected before calling bundle summary")

    monkeypatch.setattr(post_deploy, "cli_json", unexpected_summary)
    with pytest.raises(SystemExit, match=message):
        post_deploy.resolve_app_name()


def test_preview_bundle_is_parameterized_and_stateless():
    config = yaml.safe_load((Path(__file__).resolve().parent.parent / "databricks.yml").read_text())
    assert config["variables"]["preview_slug"]["default"] == ""
    assert config["variables"]["lakebase_instance_name"]["default"] == ""
    preview = config["targets"]["preview"]
    assert preview == {
        "workspace": {"root_path": "~/.bundle/${bundle.name}/preview-${var.preview_slug}"},
        "resources": {"apps": {"ontology_readiness": {
            "name": "gor-pr-${var.preview_slug}",
        }}},
    }


@pytest.mark.parametrize("target", ["dev", "stg", "prod"])
def test_non_preview_summary_ignores_slug(monkeypatch, target):
    monkeypatch.setattr(post_deploy, "TARGET", target)
    monkeypatch.setattr(post_deploy, "PREVIEW_SLUG", "INVALID/ignored")
    monkeypatch.setattr(post_deploy, "WAREHOUSE_ID", "warehouse")
    name = "genie-ontology-readiness" + (f"-{target}" if target != "prod" else "")

    def summary(*args):
        assert args == ("bundle", "summary", "-t", target, "--var", "warehouse_id=warehouse")
        return _bundle_summary(name)

    monkeypatch.setattr(post_deploy, "cli_json", summary)
    assert post_deploy.resolve_app_name() == name


@pytest.mark.parametrize("target", ["preview", "dev", "stg", "prod"])
@pytest.mark.parametrize("run_rc", [0, 1])
def test_bundle_publish_threads_slug_only_for_preview(monkeypatch, target, run_rc):
    """Exercise the real publish path with all external calls stubbed."""
    for key in ("APP_NAME", "WAREHOUSE_ID", "USE_LAKEBASE", "LAKEBASE_INSTANCE",
                "LAKEBASE_DATABASE", "LAKEBASE_SSL_MODE"):
        monkeypatch.setattr(post_deploy, key, getattr(post_deploy, key))
    monkeypatch.setattr(post_deploy, "PROFILE", "" if target == "preview" else "test-profile")
    monkeypatch.setattr(post_deploy, "TARGET", target)
    monkeypatch.setattr(post_deploy, "PREVIEW_SLUG", "159")
    monkeypatch.setattr(post_deploy, "WAREHOUSE_ID", "warehouse")
    name = "gor-pr-159" if target == "preview" else (
        "genie-ontology-readiness" + (f"-{target}" if target != "prod" else ""))
    monkeypatch.setattr(post_deploy, "cli_json", lambda *args: _bundle_summary(name))
    monkeypatch.setattr(post_deploy, "_get_app", lambda name: None)
    values = post_deploy.deploy_settings.resolve(
        {"WAREHOUSE_ID": "warehouse"}, {}, {},
        {"USE_LAKEBASE": "false", "LAKEBASE_SSL_MODE": "verify-full"})
    monkeypatch.setattr(post_deploy, "resolve_settings", lambda live: values)
    monkeypatch.setattr(post_deploy, "render_app_yml", lambda values: None)
    grants = []
    monkeypatch.setattr(post_deploy.subprocess, "run", lambda *args, **kwargs: grants.append(args))
    calls = []
    def cli(*args, **kwargs):
        calls.append(args)
        return run_rc if args[:2] == ("bundle", "run") else 0

    monkeypatch.setattr(post_deploy, "cli", cli)
    if target == "preview" and run_rc:
        with pytest.raises(SystemExit):
            post_deploy._main()
    else:
        post_deploy._main()
    assert len(grants) == (0 if target == "preview" else 1)

    var_args = ("--var=warehouse_id=warehouse",)
    if target == "preview":
        var_args += ("--var=preview_slug=159",)
    assert [args for args in calls if args[0] == "bundle"] == [
        ("bundle", "deploy", "-t", target, *var_args),
        ("bundle", "run", "ontology_readiness", "-t", target, *var_args),
    ]


@pytest.mark.parametrize("target", ["preview", "dev", "stg", "prod"])
def test_render_preview_has_no_unbound_lakebase_reference(monkeypatch, tmp_path, target):
    original = (post_deploy.APP_DIR / "app.yaml").read_text()
    config = tmp_path / "app.yaml"
    config.write_text(original)
    monkeypatch.setattr(post_deploy, "APP_CONFIG", config)
    monkeypatch.setattr(post_deploy, "ORIGINAL_APP_CONFIG", original)
    monkeypatch.setattr(post_deploy, "TARGET", target)
    monkeypatch.setattr(post_deploy, "USE_LAKEBASE", False)
    values = post_deploy.deploy_settings.resolve({}, {}, {}, {})
    post_deploy.render_app_yml(values)
    rendered = yaml.safe_load(config.read_text())
    entries = {entry["name"]: entry for entry in rendered["env"]}
    assert entries["USE_LAKEBASE"]["value"] == "false"
    assert entries["DATABRICKS_WAREHOUSE_ID"]["valueFrom"] == "sql-warehouse"
    if target == "preview":
        assert entries["LAKEBASE_HOST"] == {"name": "LAKEBASE_HOST", "value": ""}
    else:
        assert entries["LAKEBASE_HOST"]["valueFrom"] == "lakebase-db"


def test_preview_bundle_calls_discard_previous_slug_local_state(monkeypatch, tmp_path):
    """The real CLI builders clear cache even when a new slug has no remote state."""
    monkeypatch.setattr(post_deploy, "TARGET", "preview")
    monkeypatch.setattr(post_deploy, "PROFILE", "")
    monkeypatch.setattr(post_deploy, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(post_deploy, "WAREHOUSE_ID", "")
    cache = tmp_path / ".databricks" / "bundle" / "preview"
    other = cache.parent / "prod" / "terraform.tfstate"
    other.parent.mkdir(parents=True)
    other.write_text("prod state")

    def run(cmd, **kwargs):
        assert not cache.exists(), "Every bundle call must start without another slug's state"
        assert other.read_text() == "prod state"
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_bundle_summary(f"gor-pr-{post_deploy.PREVIEW_SLUG}")))

    monkeypatch.setattr(post_deploy.subprocess, "run", run)
    for slug in ("159", "160"):
        monkeypatch.setattr(post_deploy, "PREVIEW_SLUG", slug)
        for action in ("summary", "deploy", "destroy"):
            # Simulate a previous slug's cached Terraform state with no matching remote state.
            cache.mkdir(parents=True)
            (cache / "terraform.tfstate").write_text("gor-pr-other-slug")
            if action == "summary":
                assert post_deploy.resolve_app_name() == f"gor-pr-{slug}"
            else:
                assert post_deploy.cli("bundle", action, "-t", "preview", *post_deploy._preview_var_args()) == 0


@pytest.mark.parametrize("target", ["dev", "stg", "prod"])
def test_non_preview_bundle_calls_preserve_local_cache(monkeypatch, tmp_path, target):
    monkeypatch.setattr(post_deploy, "TARGET", target)
    monkeypatch.setattr(post_deploy, "APP_DIR", tmp_path / "app")
    cache = tmp_path / ".databricks" / "bundle" / "preview"
    cache.mkdir(parents=True)
    state = cache / "terraform.tfstate"
    state.write_text("preview state")
    for action in ("summary", "deploy", "destroy"):
        post_deploy._cli_cmd("bundle", action, "-t", target)
        assert state.read_text() == "preview state"
