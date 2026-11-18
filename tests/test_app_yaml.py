"""The committed app config and build files for a Git deploy in the Apps UI.

The Git deploy uses app/app.yaml exactly as committed, so it must hold no workspace
values, and its valueFrom keys must match the resource keys the README tells customers
to add.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_YAML = (ROOT / "app" / "app.yaml").read_text()
README = (ROOT / "README.md").read_text()

# Values that are safe to commit: empty, or a product default. Anything else is a
# workspace value and must not be in the file.
SAFE_VALUES = {"", "auto", "false", "true", "verify-full", "Databricks", "2097152"}


def _entries():
    out = []
    for block in re.split(r"\n  - name: ", APP_YAML)[1:]:
        name = block.split("\n", 1)[0].strip()
        value = re.search(r'^\s+value: "(.*)"$', block, re.M)
        value_from = re.search(r"^\s+valueFrom: (\S+)$", block, re.M)
        out.append((name, value.group(1) if value else None, value_from.group(1) if value_from else None))
    return out


def test_app_yaml_has_no_workspace_values():
    for name, value, value_from in _entries():
        if value is not None:
            assert value in SAFE_VALUES, f"{name} has a deploy-time value committed: {value!r}"
        assert value is None or value_from is None


def test_value_from_keys_match_resource_keys():
    refs = {(name, ref) for name, _, ref in _entries() if ref}
    assert refs == {("DATABRICKS_WAREHOUSE_ID", "sql-warehouse"), ("LAKEBASE_HOST", "lakebase-db")}


def test_readme_names_the_same_resource_keys():
    assert "`sql-warehouse`" in README and "`lakebase-db`" in README


def test_use_lakebase_is_auto():
    values = {name: value for name, value, _ in _entries()}
    assert values["USE_LAKEBASE"] == "auto"


def test_command_runs_the_app():
    assert "  - app.py" in APP_YAML and "  - python" in APP_YAML


def test_every_key_the_settings_module_knows_is_listed():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import deploy_settings as ds
    names = {name for name, _, _ in _entries()}
    expected = (set(ds.KEYS) - {"WAREHOUSE_ID"}) | {"DATABRICKS_WAREHOUSE_ID"}
    assert names == expected


def test_package_json_builds_the_frontend_into_the_path_the_server_serves():
    pkg = json.loads((ROOT / "app" / "package.json").read_text())
    assert pkg["scripts"]["build"] == "cd frontend && npm ci --no-audit --no-fund && npm run build"
    assert not pkg.get("dependencies") and not pkg.get("devDependencies")
    app_py = (ROOT / "app" / "app.py").read_text()
    assert 'Path(__file__).parent / "frontend" / "dist"' in app_py
    assert (ROOT / "app" / "frontend" / "package.json").exists()


def test_no_root_package_json_is_written_by_the_notebook():
    nb = (ROOT / "notebooks" / "install.py").read_text()
    assert 'write_text(\n    \'{' not in nb and 'Path(STAGE_DIR, "package.json")' not in nb
