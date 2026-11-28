"""Unit tests for the per-environment app-name resolution in scripts/post_deploy.py.

These pin the isolation guarantee behind the DABs hardening: a deploy to one
target must resolve ONLY that target's app name, never another environment's
(above all never the bare prod name for a dev/stg deploy), and an explicit
APP_NAME env must never redirect the run. They also pin the exact
`bundle summary` JSON key path resolve_app_name() depends on, so a CLI/shape
drift that would make the "read the name back from the bundle" guarantee inert
fails a test instead of silently always using the fallback.
"""
import sys

import pytest
import yaml
from pathlib import Path

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


@pytest.mark.parametrize("slug", ["159", "feature-test"])
def test_preview_resolves_from_bundle_summary(monkeypatch, slug):
    monkeypatch.setattr(post_deploy, "TARGET", "preview")
    monkeypatch.setattr(post_deploy, "WAREHOUSE_ID", "")
    monkeypatch.setenv("APP_NAME", "genie-ontology-readiness")
    name = f"genie-ontology-readiness-pr-{slug}"

    def summary(*args):
        assert args == ("bundle", "summary", "-t", "preview")
        return _bundle_summary(name)

    monkeypatch.setattr(post_deploy, "cli_json", summary)
    assert post_deploy.resolve_app_name() == name


@pytest.mark.parametrize("summary", [None, {}, _bundle_summary("")])
def test_preview_requires_bundle_summary(monkeypatch, summary):
    monkeypatch.setattr(post_deploy, "TARGET", "preview")
    monkeypatch.setattr(post_deploy, "WAREHOUSE_ID", "")
    monkeypatch.setattr(post_deploy, "cli_json", lambda *args: summary)
    with pytest.raises(SystemExit, match="preview app name must be resolved from bundle summary"):
        post_deploy.resolve_app_name()


def test_preview_bundle_is_parameterized_and_stateless():
    config = yaml.safe_load((Path(__file__).resolve().parent.parent / "databricks.yml").read_text())
    assert config["variables"]["preview_slug"]["default"] == ""
    assert config["variables"]["lakebase_instance_name"]["default"] == ""
    preview = config["targets"]["preview"]
    assert preview == {
        "workspace": {"root_path": "~/.bundle/${bundle.name}/preview-${var.preview_slug}"},
        "resources": {"apps": {"ontology_readiness": {
            "name": "genie-ontology-readiness-pr-${var.preview_slug}",
        }}},
    }
