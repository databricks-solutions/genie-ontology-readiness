"""Runtime defaults for the app, used when the deploy did not set a value.

Pure functions (no SDK, no I/O) so they can be tested without a workspace. The
Databricks Apps runtime injects DATABRICKS_CLIENT_ID, DATABRICKS_WORKSPACE_ID and
DATABRICKS_APP_NAME; a Git deploy in the Apps UI sets no other values. Each
resolver returns (value, source). The source is logged. It never holds a secret.
"""

DEFAULT_DATABASE = "ontology_readiness"
LAKEBASE_RESOURCE = "lakebase-db"


def resolve_value(env_value, injected_value, default="", env_label="app setting",
                  injected_label="runtime"):
    """First non-empty of: the env value, the injected/resource value, the default.

    Returns (value, source). The labels say where each value came from, for the log."""
    if (env_value or "").strip():
        return env_value.strip(), env_label
    if (injected_value or "").strip():
        return injected_value.strip(), injected_label
    return default, "default"


def resolve_use_lakebase(raw, host):
    """USE_LAKEBASE: true | false | auto. `auto` (or empty) is on when a Lakebase host is set."""
    value = (raw or "auto").strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    return bool((host or "").strip())


def resolve_lakebase_user(env_value, client_id, env_label="app setting"):
    return resolve_value(env_value, client_id, "", env_label, "runtime (client id)")


def resolve_workspace_id(env_value, injected_workspace_id):
    return resolve_value(env_value, injected_workspace_id, "")


def resolve_database(env_value, resource_database, env_label="app setting",
                     injected_label="runtime"):
    return resolve_value(env_value, resource_database, DEFAULT_DATABASE, env_label, injected_label)


def resolve_instance(env_value, resource_instance):
    return resolve_value(env_value, resource_instance, "")
