"""Reuse the deployed app's settings when a customer redeploys (stdlib only).

Shared by scripts/post_deploy.py and notebooks/install.py. Nothing here does I/O:
the callers read the deployed app (apps get, workspace export) and pass the
results in. That keeps every decision in this file testable without a workspace.

Each setting is resolved in this order:

    you set        a value given for this run: an env var that is SET (even when
                   empty), or a notebook widget that is not empty
    deployed app   the value in the deployed app's app.yaml, or in its resources
    default        the value in the new release's app/app.yaml

Two kinds of key never follow that order:

    GOR_VERSION                always comes from the new release
    LAKEBASE_USER / LAKEBASE_HOST  always come from the live app and instance

Keys that the new template has but the deployed app.yaml does not keep the
template default. That is how new secure defaults reach an upgraded app.
"""

import json
import re
import time
import urllib.request
from collections import namedtuple
from datetime import datetime, timezone

Setting = namedtuple("Setting", "value source")

SOURCE_YOU = "you set"
SOURCE_DEPLOYED = "deployed app"
SOURCE_DEFAULT = "default"
SOURCE_RELEASE = "new release"
SOURCE_LIVE = "live"

WAREHOUSE_RESOURCE = "sql-warehouse"
LAKEBASE_RESOURCE = "lakebase-db"

# Every app.yaml env key that the app reads, plus WAREHOUSE_ID (which lives in the
# sql-warehouse resource, not in app.yaml). A test fails if the template gains a
# value key that is missing here.
KEYS = (
    "WAREHOUSE_ID",
    "ASSESS_CATALOGS",
    "GENIE_SPACE_ID",
    "WORKSPACE_ID",
    "BRAND_NAME",
    "FORCE_SP",
    "GENIE_ALLOW_SP_FALLBACK",
    "MAX_REQUEST_BYTES",
    "LAKEBASE_SSL_MODE",
    "USE_LAKEBASE",
    "LAKEBASE_INSTANCE_NAME",
    "LAKEBASE_DATABASE",
    "LAKEBASE_ENDPOINT_NAME",
    "GOR_VERSION",
    "LAKEBASE_USER",
    "LAKEBASE_HOST",
)
ALWAYS_NEW = ("GOR_VERSION",)
# Defaults that differ from the committed app.yaml. The app.yaml value is empty so that a
# Git deploy falls back to the lakebase-db resource. The scripts still need a concrete name.
DEFAULT_OVERRIDES = {"LAKEBASE_DATABASE": "ontology_readiness"}
APP_CONFIG_NAME = "app.yaml"
LEGACY_APP_CONFIG_NAME = "app.yml"
ALWAYS_LIVE = ("LAKEBASE_USER", "LAKEBASE_HOST")
# Keys that the deployed app reads from its resources (authoritative), not app.yaml.
RESOURCE_KEYS = ("WAREHOUSE_ID", "LAKEBASE_INSTANCE_NAME", "LAKEBASE_DATABASE")


def parse_env(text):
    """Return {name: value} for the `env:` entries of an app.yaml (or its template).

    Only `value:` entries are returned. `valueFrom:` entries (such as the warehouse
    binding) are skipped on purpose: their value comes from a resource."""
    vals, name = {}, None
    for line in text.split("\n"):
        m = re.match(r"\s*-?\s*name:\s*(\S+)", line)
        if m:
            name = m.group(1)
            continue
        m = re.match(r"\s*value:\s*(.*)$", line)
        if m and name is not None:
            v = m.group(1).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            vals[name] = v
            name = None
        elif re.match(r"\s*valueFrom:", line):
            name = None
    return vals


def read_resources(app):
    """Return the settings held by the app's resources, from an apps-get dict.

    The sql-warehouse resource gives WAREHOUSE_ID. The lakebase-db resource gives
    LAKEBASE_INSTANCE_NAME and LAKEBASE_DATABASE. A key is returned only when its
    resource is attached, so `"LAKEBASE_INSTANCE_NAME" in result` means Lakebase is
    attached."""
    out = {}
    for r in (app or {}).get("resources") or []:
        name = r.get("name")
        if name == WAREHOUSE_RESOURCE and (r.get("sql_warehouse") or {}).get("id"):
            out["WAREHOUSE_ID"] = str(r["sql_warehouse"]["id"])
        elif name == LAKEBASE_RESOURCE:
            db = r.get("database") or {}
            out["LAKEBASE_INSTANCE_NAME"] = db.get("instance_name") or ""
            out["LAKEBASE_DATABASE"] = db.get("database_name") or ""
    return out


def read_live(app, read_text):
    """Describe the live app. `app` is its apps-get dict, or None if it does not exist.

    `read_text(path)` returns the text of a workspace file, or None if it cannot be
    read. It is called only when the app has an active deployment."""
    if not app:
        return {"exists": False, "has_deployment": False, "yml_readable": True,
                "env": {}, "resources": {}, "sp": "", "source_path": ""}
    deployment = app.get("active_deployment")
    has_deployment = bool(deployment)
    env, readable = {}, True
    if has_deployment:
        path = (deployment.get("source_code_path") or "").rstrip("/")
        text = None
        if path:  # app.yaml is current; app.yml is what releases before the Git path deployed
            text = read_text(f"{path}/{APP_CONFIG_NAME}") or read_text(f"{path}/{LEGACY_APP_CONFIG_NAME}")
        if text is None:
            readable = False
        else:
            env = parse_env(text)
    return {"exists": True, "has_deployment": has_deployment, "yml_readable": readable,
            "env": env, "resources": read_resources(app),
            "sp": app.get("service_principal_client_id") or "",
            "source_path": (deployment or {}).get("source_code_path") or ""}


def choose_mode(app_exists, infra):
    """'upgrade' for a redeploy of an existing app; 'bundle' for a first install or an infra change.

    An upgrade syncs the source and runs `apps deploy`. It never runs `bundle deploy`,
    so it does not depend on who first deployed the bundle. `infra` is INFRA=true,
    which asks for the bundle path (for example after databricks.yml changed)."""
    return "bundle" if (infra or not app_exists) else "upgrade"


def effective_lakebase(value, instance):
    """Whether history is on for a run. `auto` (or empty) is on when an instance is set."""
    value = (value or "auto").strip().lower()
    if value in ("true", "false"):
        return value == "true"
    return bool((instance or "").strip())


def uses_lakebase(live_env, live_resources):
    """True when the deployed app keeps its history in Lakebase."""
    return (live_env.get("USE_LAKEBASE", "").strip().lower() == "true"
            or "LAKEBASE_INSTANCE_NAME" in live_resources)


def _deployed_value(key, live_env, live_resources):
    if key in RESOURCE_KEYS and key in live_resources:
        return live_resources[key]
    return live_env.get(key, "")


def resolve(explicit, live_env, live_resources, defaults, forced=None):
    """Return {key: Setting(value, source)} for every key in KEYS.

    explicit        values the customer set for this run (only keys that are set)
    live_env        env values of the deployed app.yaml ({} when unknown or reset)
    live_resources  values from the deployed app's resources ({} when unknown or reset)
    defaults        the new release's template defaults (plus any extra defaults)
    forced          values for ALWAYS_NEW and ALWAYS_LIVE keys; these ignore explicit"""
    forced = forced or {}
    out = {}
    for key in KEYS:
        if key in ALWAYS_NEW:
            out[key] = Setting(forced.get(key, ""), SOURCE_RELEASE)
        elif key in ALWAYS_LIVE:
            out[key] = Setting(forced.get(key, ""), SOURCE_LIVE)
        elif key in explicit:
            out[key] = Setting(explicit[key], SOURCE_YOU)
        elif key in live_resources or key in live_env:
            out[key] = Setting(_deployed_value(key, live_env, live_resources), SOURCE_DEPLOYED)
        else:
            out[key] = Setting(defaults.get(key, ""), SOURCE_DEFAULT)
    return out


def _lakebase_problems(values, live_env, live_resources):
    if not uses_lakebase(live_env, live_resources):
        return []
    old_instance = _deployed_value("LAKEBASE_INSTANCE_NAME", live_env, live_resources)
    old_database = _deployed_value("LAKEBASE_DATABASE", live_env, live_resources)
    if not effective_lakebase(values["USE_LAKEBASE"].value, values["LAKEBASE_INSTANCE_NAME"].value):
        return [
            f"The deployed app saves history in Lakebase (instance '{old_instance}', "
            f"database '{old_database}'). This run turns Lakebase off. Then the app stops "
            "saving history. Keep USE_LAKEBASE=true, or leave it unset. "
            "To start fresh on purpose, set RESET_SETTINGS=true."
        ]
    problems = []
    for key, old, label in (
        ("LAKEBASE_INSTANCE_NAME", old_instance, "instance"),
        ("LAKEBASE_DATABASE", old_database, "database"),
    ):
        new = values[key].value
        if new != old:
            problems.append(
                f"This run points the app at Lakebase {label} '{new}'. "
                f"The deployed app uses '{old}', and its history is there. "
                f"Set {key}={old} to keep it. To start fresh on purpose, set RESET_SETTINGS=true."
            )
    return problems


def find_problems(values, live, *, reset=False):
    """Return the plain-text reasons to stop before changing anything. Empty = go ahead.

    With reset=True the deployed settings were ignored on purpose, so the checks that
    compare against them are skipped."""
    if reset:
        return []
    if live.get("has_deployment") and not live.get("yml_readable"):
        # The deployed settings are unknown, so no comparison against them is meaningful.
        return [
            f"The app is deployed from '{live.get('source_path', '')}'. Its app.yaml cannot be "
            "read, so its settings are unknown. Check that you can read that folder, then run again. "
            "To ignore the deployed settings, set RESET_SETTINGS=true."
        ]
    return _lakebase_problems(values, live.get("env", {}), live.get("resources", {}))


def plan(explicit, live, template_text, *, forced=None, extra_defaults=None, reset=False):
    """Resolve every setting for this run. Returns (values, problems).

    With reset=True the deployed values are ignored: each setting is either explicit
    or a release default."""
    defaults = {**parse_env(template_text), **DEFAULT_OVERRIDES, **(extra_defaults or {})}
    env = {} if reset else live.get("env", {})
    resources = {} if reset else live.get("resources", {})
    values = resolve(explicit, env, resources, defaults, forced)
    return values, find_problems(values, live, reset=reset)


def format_table(values):
    """Return the settings as lines of text: setting, value and where it came from."""
    rows = [("SETTING", "VALUE", "SOURCE")]
    for key in KEYS:
        s = values[key]
        shown = s.value
        if not shown:
            shown = "(from the instance)" if key == "LAKEBASE_HOST" else "(empty)"
        elif len(shown) > 60:
            shown = shown[:57] + "..."
        rows.append((key, shown, s.source))
    width = max(len(r[0]) for r in rows) + 2
    vwidth = max(len(r[1]) for r in rows) + 2
    return [f"  {k:<{width}}{v:<{vwidth}}{src}" for k, v, src in rows]


def _yaml_safe(value):
    return str(value).replace('"', "").replace("\n", " ").strip()


def render(template_text, values):
    """Return the app.yaml text: the template with each env `value:` set from values.

    Keys that the template does not have as a `value:` entry are skipped. Returns
    (text, missing), where missing lists template keys that could not be set."""
    text = template_text
    template_keys = set(parse_env(template_text))
    missing = []
    for name, value in values.items():
        if name not in template_keys:
            continue
        pattern = rf'(- name: {re.escape(name)}\n\s+value: ")[^"]*(")'
        safe = _yaml_safe(value)
        text, n = re.subn(pattern, lambda m, s=safe: m.group(1) + s + m.group(2), text)
        if n == 0:
            missing.append(name)
    return text, missing


WIDGET_KEYS = (
    "app_name", "release", "staging_folder", "warehouse_id", "assess_catalogs", "genie_space_id",
    "brand_name", "force_sp", "lakebase_instance_name", "lakebase_database", "reset_settings",
)
REPO_OWNER_REPO = "databricks-solutions/genie-ontology-readiness"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPO_OWNER_REPO}/releases/latest"
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_IDENT = re.compile(r"[a-z_][a-z0-9_]*")


def validate_widgets(widgets):
    """Return plain-text reasons why the notebook widgets are not valid. Empty = valid."""
    problems = []
    if not re.fullmatch(r"[a-z0-9-]{2,30}", widgets.get("app_name", "")):
        problems.append("app_name must be 2-30 characters: lowercase letters, numbers and dashes.")
    if widgets.get("force_sp", "") not in ("", "true", "false"):
        problems.append("force_sp must be true, false, or empty (reuse the deployed value).")
    release = widgets.get("release", "latest")
    if release not in ("latest", "current") and not _TAG.fullmatch(release):
        problems.append("release must be latest, current, or a release tag such as v1.3.0.")
    staging = widgets.get("staging_folder", "")
    if staging and not staging.startswith("/Workspace/"):
        problems.append("staging_folder must start with /Workspace/ (or be empty).")
    if widgets.get("reset_settings", "false") not in ("false", "true"):
        problems.append("reset_settings must be false or true.")
    database = widgets.get("lakebase_database", "")
    if database and not _IDENT.fullmatch(database):
        problems.append("lakebase_database must be a lowercase Postgres name (letters, digits, underscore).")
    return problems


def explicit_from_widgets(widgets):
    """Turn notebook widget values into the `explicit` settings for plan().

    An empty widget is not explicit: it means "reuse the deployed value". So only
    non-empty widgets are returned. Naming a Lakebase instance turns history on."""
    mapping = {
        "warehouse_id": "WAREHOUSE_ID",
        "assess_catalogs": "ASSESS_CATALOGS",
        "genie_space_id": "GENIE_SPACE_ID",
        "brand_name": "BRAND_NAME",
        "force_sp": "FORCE_SP",
        "lakebase_instance_name": "LAKEBASE_INSTANCE_NAME",
        "lakebase_database": "LAKEBASE_DATABASE",
    }
    out = {}
    for widget, key in mapping.items():
        value = (widgets.get(widget) or "").strip()
        if value:
            out[key] = value
    if out.get("LAKEBASE_INSTANCE_NAME"):
        out["USE_LAKEBASE"] = "true"
    return out


# --- release choice, Git folder move, deploy check, marker, exit payload ------------

def fetch_json(url, opener=urllib.request.urlopen, retries=3, timeout=10, sleep=time.sleep):
    """GET a JSON document. Retries with backoff (1s, 2s, 4s). Raises the last error."""
    last = None
    for attempt in range(retries + 1):
        try:
            with opener(urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"}),
                        timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # network or HTTP error: retry, then report
            last = e
            if attempt < retries:
                sleep(2 ** attempt)
    raise last


def latest_release_tag(fetch=fetch_json):
    """Return (tag, error). tag is None when GitHub cannot be reached."""
    try:
        tag = (fetch(LATEST_RELEASE_URL) or {}).get("tag_name", "")
        return (tag, "") if tag else (None, "GitHub returned no release tag")
    except Exception as e:
        return None, type(e).__name__  # class name only: no raw error text is shown


def decide_release(choice, latest_tag, latest_error=""):
    """Return {"release": "current" | <tag>, "warning": str | None}.

    latest: the latest release tag. If GitHub cannot be reached, deploy the current
    Git folder state and warn. current: deploy what the Git folder has now. Any
    other value is a tag, used as given."""
    if choice == "current":
        return {"release": "current", "warning": None}
    if choice == "latest":
        if latest_tag:
            return {"release": latest_tag, "warning": None}
        return {"release": "current",
                "warning": f"Could not reach GitHub to find the latest release ({latest_error}). "
                           "Deploying the current Git folder state instead."}
    return {"release": choice, "warning": None}


def tag_commit_sha(tag, fetch=fetch_json):
    """Return the commit sha a release tag points to (follows an annotated tag), or None."""
    try:
        ref = fetch(f"https://api.github.com/repos/{REPO_OWNER_REPO}/git/ref/tags/{tag}")
        obj = ref.get("object", {})
        if obj.get("type") == "tag":
            obj = fetch(f"https://api.github.com/repos/{REPO_OWNER_REPO}/git/tags/{obj['sha']}").get("object", {})
        return obj.get("sha") or None
    except Exception:
        return None


def needs_move(branch, head_sha, tag, tag_sha):
    """True when the Git folder is not at the wanted tag. Matches on the tag name or the commit."""
    if branch == tag:
        return False
    if tag_sha and head_sha == tag_sha:
        return False
    return True


def moved_payload(tag):
    """The exit value when the Git folder was moved to `tag` and the notebook must run again."""
    return {"status": "moved", "release": tag}


def moved_message(tag):
    return f"The Git folder is now at {tag}. Click Run all again to deploy it."


def app_ready(app_status, compute_status):
    """True when the app is RUNNING and its compute is ACTIVE (both as plain strings)."""
    return app_status == "RUNNING" and compute_status == "ACTIVE"


def deployment_succeeded(state):
    return state == "SUCCEEDED"


def version_marker(git_sha, release, now=None):
    """The JSON text written to <staging_folder>/.deployed_version."""
    when = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps({"git_sha": git_sha, "release": release, "deployed_at": when}, sort_keys=True)


def exit_payload(app_name, url, release, git_sha, staging_folder, values):
    """The notebook exit value for a finished deploy. Holds no secrets."""
    return {
        "app_name": app_name,
        "url": url,
        "release": release,
        "git_sha": git_sha,
        "staging_folder": staging_folder,
        "settings": {key: {"value": values[key].value, "source": values[key].source} for key in KEYS},
    }
