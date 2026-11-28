"""Reuse the deployed app's settings when a customer redeploys (stdlib only).

Used by scripts/post_deploy.py. Nothing here does I/O:
the callers read the deployed app (apps get, workspace export) and pass the
results in. That keeps every decision in this file testable without a workspace.

Each setting is resolved in this order:

    you set        a value given for this run: an env var that is SET (even when
                   empty)
    deployed app   the value in the deployed app's app.yaml, or in its resources
    default        the value in the new release's app/app.yaml

Two kinds of key never follow that order:

    GOR_VERSION                always comes from the new release
    LAKEBASE_USER / LAKEBASE_HOST  always come from the live app and instance

Keys that the new template has but the deployed app.yaml does not keep the
template default. That is how new secure defaults reach an upgraded app.
"""

import re
from collections import namedtuple

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
        elif name == LAKEBASE_RESOURCE and "postgres" in r:
            # Lakebase Autoscaling (the default path): the branch and database are resource
            # names, not Postgres names, so they are not app.yaml settings this script resolves.
            # They are recorded here only so an Autoscaling upgrade can tell a reuse (same
            # branch/database) from a repoint (different one) — see _autoscaling_problems.
            pg = r.get("postgres") or {}
            out["LAKEBASE_BRANCH"] = pg.get("branch") or ""
            out["LAKEBASE_DATABASE_RESOURCE"] = pg.get("database") or ""
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
                "env": {}, "resources": {}, "sp": "", "source_path": "", "git": False,
                "autoscaling": False}
    deployment = app.get("active_deployment")
    has_deployment = bool(deployment)
    env, readable = {}, True
    git = bool(deployment and deployment.get("git_source"))
    if git:
        # Deployed from Git (the Apps UI path): it runs the committed app.yaml of that commit,
        # so its settings are the release defaults plus its resources. A deployment sent with
        # custom env_vars is the exception: the API returns their names only, so the values
        # are unknown.
        readable = not deployment.get("env_vars")
    elif has_deployment:
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
            "source_path": (deployment or {}).get("source_code_path") or "",
            "git": git,
            # A Lakebase Autoscaling lakebase-db (the default for a new database).
            "autoscaling": any(r.get("name") == LAKEBASE_RESOURCE and "postgres" in r
                               for r in app.get("resources") or [])}


def choose_mode(app_exists, infra):
    """'upgrade' for a redeploy of an existing app; 'bundle' for a first install or an infra change.

    An upgrade syncs the source and runs `apps deploy`. It never runs `bundle deploy`,
    so it does not depend on who first deployed the bundle. `infra` is INFRA=true,
    which asks for the bundle path (for example after databricks.yml changed)."""
    return "bundle" if (infra or not app_exists) else "upgrade"


def effective_lakebase(value, instance, autoscaling=False):
    """Whether history is on for a run.

    `true`/`false` force it. `auto` (or empty) is on when a Provisioned instance is set, or
    when the app has a Lakebase Autoscaling database attached (the default path, which has no
    instance name)."""
    value = (value or "auto").strip().lower()
    if value in ("true", "false"):
        return value == "true"
    return bool((instance or "").strip()) or bool(autoscaling)


def uses_lakebase(live_env, live_resources, autoscaling=False):
    """True when the deployed app keeps its history in Lakebase (Provisioned or Autoscaling)."""
    return (live_env.get("USE_LAKEBASE", "").strip().lower() == "true"
            or "LAKEBASE_INSTANCE_NAME" in live_resources
            or bool(autoscaling))


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


AUTOSCALING_STOP = (
    "This run would detach or repoint the app's Lakebase Autoscaling history. The deployed app "
    "keeps its assessment history in a Lakebase Autoscaling database (now the default path); "
    "turning history off, switching to a Provisioned instance, or pointing at a different "
    "Autoscaling branch/endpoint/database would orphan that history. Keep USE_LAKEBASE on, leave "
    "LAKEBASE_INSTANCE_NAME unset, and set LAKEBASE_ENDPOINT_NAME to the DEPLOYED branch's endpoint "
    "(see the deployed lakebase-db resource). RESET_SETTINGS=true does NOT override this — to move "
    "history to a different database on purpose, detach the lakebase-db resource in the Apps UI first."
)


def _autoscaling_problems(values, live, target=None):
    """Safety stops for an app whose history is a Lakebase Autoscaling database.

    Autoscaling is the default path, so a correctly-triggered reuse run proceeds: it re-ensures
    the same project/branch/endpoint/database/role idempotently. The stops here preserve the
    original safety intent — never silently detach or repoint an existing history:

    - explicit USE_LAKEBASE=false would turn history off (detach);
    - LAKEBASE_INSTANCE_NAME set would switch to a Provisioned instance (repoint);
    - this run's intended destination (``target``: the branch + database RESOURCE names the caller
      will create/attach) differs from the DEPLOYED lakebase-db resource — it would repoint the
      compute branch or switch the database.

    ``target`` is ``(branch, database_resource)``. When it is not supplied (a direct
    ``find_problems`` call with no computed destination), fall back to comparing an explicit
    LAKEBASE_ENDPOINT_NAME against the deployed branch, so the branch-repoint stop still holds.
    """
    if (values["USE_LAKEBASE"].value or "").strip().lower() == "false":
        return [AUTOSCALING_STOP]
    if (values["LAKEBASE_INSTANCE_NAME"].value or "").strip():
        return [AUTOSCALING_STOP]
    deployed_branch = (live.get("resources", {}).get("LAKEBASE_BRANCH") or "").rstrip("/")
    deployed_db = (live.get("resources", {}).get("LAKEBASE_DATABASE_RESOURCE") or "").rstrip("/")
    if target is not None:
        want_branch = (target[0] or "").rstrip("/")
        want_db = (target[1] or "").rstrip("/")
        # Compare against the DEPLOYED resource. A derived destination (from the app name) that
        # differs from where the app is actually attached would silently repoint it; so would an
        # explicit endpoint on another branch, or a different database on the same branch.
        if deployed_branch and want_branch and want_branch != deployed_branch:
            return [AUTOSCALING_STOP]
        if deployed_db and want_db and want_db != deployed_db:
            return [AUTOSCALING_STOP]
        return []
    want_endpoint = (values["LAKEBASE_ENDPOINT_NAME"].value or "").strip()
    if want_endpoint and deployed_branch and not want_endpoint.startswith(deployed_branch + "/"):
        return [AUTOSCALING_STOP]
    return []


def find_problems(values, live, *, reset=False, autoscaling_target=None):
    """Return the plain-text reasons to stop before changing anything. Empty = go ahead.

    ``autoscaling_target`` is this run's intended ``(branch, database_resource)`` destination (the
    caller derives it from the deployed resource / an explicit endpoint / the app name). It is
    compared against the deployed Autoscaling resource so a mismatch stops before any change.

    RESET_SETTINGS (``reset``) re-renders the other settings from the release defaults, but it does
    NOT bypass the Autoscaling detach/repoint stops: resetting must never silently drop or move an
    attached history resource. With reset, only the unknown-settings stops (which reset is the
    documented escape for) are skipped."""
    if not reset:
        if live.get("git") and not live.get("yml_readable"):
            return [
                "The app is deployed from Git with custom settings (env_vars), and the API does not "
                "return their values, so its settings are unknown. Upgrade it the way it was deployed: "
                "in the Apps UI (Deploy from Git), or with the pipeline that set those env_vars."
            ]
        if live.get("has_deployment") and not live.get("yml_readable"):
            # The deployed settings are unknown, so no comparison against them is meaningful.
            return [
                f"The app is deployed from '{live.get('source_path', '')}'. Its app.yaml cannot be "
                "read, so its settings are unknown. Check that you can read that folder, then run again. "
                "To ignore the deployed settings, set RESET_SETTINGS=true."
            ]
    if live.get("autoscaling"):
        # These stops hold even under reset — never silently detach or repoint an attached history.
        return _autoscaling_problems(values, live, autoscaling_target)
    if reset:
        return []
    return _lakebase_problems(values, live.get("env", {}), live.get("resources", {}))


def plan(explicit, live, template_text, *, forced=None, extra_defaults=None, reset=False,
         autoscaling_target=None):
    """Resolve every setting for this run. Returns (values, problems).

    With reset=True the deployed values are ignored: each setting is either explicit
    or a release default."""
    defaults = {**parse_env(template_text), **DEFAULT_OVERRIDES, **(extra_defaults or {})}
    env = {} if reset else live.get("env", {})
    resources = {} if reset else live.get("resources", {})
    values = resolve(explicit, env, resources, defaults, forced)
    return values, find_problems(values, live, reset=reset, autoscaling_target=autoscaling_target)


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
