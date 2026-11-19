"""Lakebase (PostgreSQL-compatible) async client using asyncpg."""

import os
import time
import logging
import ssl
import uuid
import asyncio
import aiohttp
from typing import Optional

from server.user_agent import with_ua
from server.history_schema import LEGACY_SCHEMA

logger = logging.getLogger(__name__)

# Will be set during app lifespan
_pool = None


async def _fetch_db_credential(instance_name: str = "", endpoint_name: str = "") -> Optional[str]:
    """Fetch a Lakebase database credential from the Databricks API.

    Autoscaling uses POST /api/2.0/postgres/credentials with an endpoint resource
    name. Provisioned Lakebase uses POST /api/2.0/database/credentials with an
    instance name. Both calls use the app service principal credentials.

    The returned token is typically valid for ~60 minutes.
    """
    from server.config import get_workspace_host, get_auth_headers

    if not endpoint_name and not instance_name:
        logger.warning("_fetch_db_credential: endpoint_name and instance_name are empty")
        return None

    try:
        host = get_workspace_host()
        if not host:
            logger.warning("_fetch_db_credential: could not resolve workspace host")
            return None

        # Use SP credentials (force_sp=True) since the SP owns the Postgres role
        auth_headers = get_auth_headers(force_sp=True)
        if not auth_headers:
            logger.warning("_fetch_db_credential: could not obtain auth headers")
            return None

        if endpoint_name:
            url = f"{host}/api/2.0/postgres/credentials"
            payload = {"endpoint": endpoint_name}
            resource = f"endpoint '{endpoint_name}'"
        else:
            url = f"{host}/api/2.0/database/credentials"
            payload = {
                "request_id": str(uuid.uuid4()),
                "instance_names": [instance_name],
            }
            resource = f"instance '{instance_name}'"

        headers = {
            **auth_headers,
            "Content-Type": "application/json",
        }

        logger.info(f"Fetching SP-scoped Lakebase credential for {resource}")

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), headers=with_ua()) as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.warning(
                        f"Failed to fetch Lakebase credential (status {response.status}): {error_text[:200]}"
                    )
                    return None

                resp_json = await response.json()
                token = resp_json.get("token")
                if not token:
                    # Log the response SHAPE only. The body of a credentials
                    # response is credential material by definition, and logging
                    # it verbatim wrote a live secret to the app log (CWE-532).
                    logger.warning("No token in Lakebase credential response (keys: %s)",
                                   sorted(resp_json.keys()) if isinstance(resp_json, dict) else type(resp_json).__name__)
                    return None

                logger.info("Successfully obtained instance-scoped Lakebase credential")
                return token

    except asyncio.TimeoutError:
        logger.warning("Timeout fetching Lakebase credential")
        return None
    except Exception as e:
        logger.warning(f"Error fetching Lakebase credential: {e}")
        return None


_RESOURCE: dict | None = None


def _lakebase_resource() -> dict:
    """This app's own ``lakebase-db`` resource (read once).

    A Git deploy in the Apps UI sets no database, instance or endpoint name, so they come
    from the attached resource. Two kinds:

    - Provisioned (``database``): ``instance`` and ``database`` names.
    - Autoscaling (``postgres``, the default for a new Lakebase database): ``branch``
      (``projects/<p>/branches/<b>``) and ``database_resource`` (the database's resource
      name, not its Postgres name).

    Empty when not in a Databricks App or when the read fails. Read as the app service
    principal (a blocking SDK call: run it off the event loop). Holds no secret.
    """
    global _RESOURCE
    if _RESOURCE is None:
        found = {"instance": "", "database": "", "branch": "", "database_resource": ""}
        app_name = os.environ.get("DATABRICKS_APP_NAME", "")
        ok = True
        if app_name:
            try:
                from server.config import _get_workspace_client
                app = _get_workspace_client().apps.get(app_name)
                for resource in (app.resources or []):
                    if resource.name != "lakebase-db":
                        continue
                    if resource.database:
                        found = {**found, "instance": resource.database.instance_name or "",
                                 "database": resource.database.database_name or ""}
                    elif getattr(resource, "postgres", None):
                        found = {**found, "branch": resource.postgres.branch or "",
                                 "database_resource": resource.postgres.database or ""}
            except Exception as e:
                ok = False  # not cached: the next startup attempt reads it again
                logger.warning(f"Could not read the lakebase-db resource ({type(e).__name__}); "
                               "using the app settings and defaults")
        if ok:
            _RESOURCE = found
        return found
    return _RESOURCE


def _is_endpoint_name(value: str) -> bool:
    """True for an Autoscaling endpoint resource name (projects/<p>/branches/<b>/endpoints/<e>)."""
    return value.startswith("projects/") and "/endpoints/" in value


def _host_of(endpoint) -> str:
    """The host name of an Autoscaling endpoint object ("" when it has none yet)."""
    return getattr(getattr(getattr(endpoint, "status", None), "hosts", None), "host", "") or ""


def _endpoint_host(endpoint: str) -> str:
    """The host name of an Autoscaling endpoint, or "" when it can't be read (logged)."""
    try:
        from server.config import _get_workspace_client
        return _host_of(_get_workspace_client().postgres.get_endpoint(endpoint))
    except Exception as e:
        logger.warning(f"Could not read the Lakebase endpoint host ({type(e).__name__})")
        return ""


def _autoscaling_endpoint(branch: str, host: str = "") -> str:
    """The compute endpoint of an Autoscaling branch to mint a credential for.

    The endpoint whose host is the one the app connects to, else the read-write endpoint.
    Never a read-only endpoint picked at random: history writes need read-write. Returns ""
    (logged) when the branch has no such endpoint or the list fails."""
    if not branch:
        return ""
    try:
        from server.config import _get_workspace_client
        endpoints = list(_get_workspace_client().postgres.list_endpoints(parent=branch))
    except Exception as e:
        logger.warning(f"Could not list the endpoints of the Lakebase branch ({type(e).__name__})")
        return ""

    def _read_write(e):
        kind = getattr(getattr(e, "status", None), "endpoint_type", None)
        return "READ_WRITE" in str(getattr(kind, "value", kind) or "")

    for match in ((lambda e: host and _host_of(e) == host), _read_write):
        for e in endpoints:
            if e.name and match(e):
                return e.name
    logger.warning(f"The Lakebase branch has no read-write endpoint ({len(endpoints)} endpoint(s) listed)")
    return ""


def _postgres_database_name(database_resource: str) -> str:
    """The Postgres name of an Autoscaling database resource, or "" (logged)."""
    if not database_resource:
        return ""
    try:
        from server.config import _get_workspace_client
        db = _get_workspace_client().postgres.get_database(database_resource)
        for part in (getattr(db, "status", None), getattr(db, "spec", None)):
            name = getattr(part, "postgres_database", None)
            if name:
                return name
    except Exception as e:
        logger.warning(f"Could not read the Lakebase database name ({type(e).__name__})")
    return ""


_TARGET: dict | None = None


def _resolve_target() -> dict:
    """Where and how to connect: host, port, database, user, and the endpoint (Autoscaling) or
    instance (Provisioned) to mint a credential for. Resolved once per process (it makes
    blocking SDK calls, so init_pool runs it in a thread) and reused on every token refresh.

    Sources, first non-empty wins:
    - credential target: LAKEBASE_ENDPOINT_NAME; an Autoscaling `valueFrom` (LAKEBASE_HOST is
      then the endpoint name); the Autoscaling branch's read-write endpoint;
      LAKEBASE_INSTANCE_NAME; the Provisioned resource's instance. An attached Autoscaling
      resource is never paired with an instance credential.
    - host: LAKEBASE_HOST (unless it is an endpoint name), then PGHOST, then the endpoint's host.
    - database: LAKEBASE_DATABASE, PGDATABASE, the resource's database, the default.
    """
    from server.runtime_config import resolve_database, resolve_instance, resolve_lakebase_user
    res = _lakebase_resource()
    raw_host = os.environ.get("LAKEBASE_HOST") or os.environ.get("PGHOST", "")

    host, value_from_endpoint = raw_host, ""
    if _is_endpoint_name(raw_host):
        # `valueFrom: lakebase-db` on an Autoscaling resource gives the endpoint's resource
        # name, not a host name. The host is in PGHOST, which Apps injects for the resource.
        value_from_endpoint = raw_host
        pg_host = os.environ.get("PGHOST", "")
        host = pg_host if pg_host and not _is_endpoint_name(pg_host) else _endpoint_host(raw_host)

    # Credential endpoint, first non-empty: the app setting, the valueFrom endpoint, the
    # branch's endpoint for the host we connect to (or its read-write endpoint).
    endpoint, source = "", ""
    for candidate, label in (
        (lambda: os.environ.get("LAKEBASE_ENDPOINT_NAME", ""), "app setting LAKEBASE_ENDPOINT_NAME"),
        (lambda: value_from_endpoint, "lakebase-db resource (Autoscaling)"),
        (lambda: _autoscaling_endpoint(res["branch"], host), "lakebase-db resource (Autoscaling branch)"),
    ):
        endpoint = candidate()
        if endpoint:
            source = label
            break
    autoscaling = bool(endpoint or res["branch"])

    instance = ""
    if autoscaling:
        if endpoint:
            logger.info(f"Lakebase Autoscaling endpoint from {source}")
        else:
            logger.warning("A Lakebase Autoscaling database is attached, but no read-write endpoint was found "
                           "for it. Check that the branch has a running read-write compute endpoint.")
    else:
        instance, instance_source = resolve_instance(
            os.environ.get("LAKEBASE_INSTANCE_NAME", ""),
            res["instance"] if not os.environ.get("LAKEBASE_INSTANCE_NAME") else "",
        )
        logger.info(f"Lakebase instance from {instance_source}")

    # Labels say where a value came from. PGDATABASE and PGUSER are what Apps injects for
    # an attached database resource, so they are labelled "database resource".
    app_db, pg_db = os.environ.get("LAKEBASE_DATABASE", ""), os.environ.get("PGDATABASE", "")
    resource_db = "" if (app_db or pg_db) else (
        res["database"] or _postgres_database_name(res["database_resource"]))
    database, database_source = resolve_database(
        app_db or pg_db, resource_db,
        env_label="app setting" if app_db else "database resource",
        injected_label="lakebase-db resource lookup",
    )
    app_user, pg_user = os.environ.get("LAKEBASE_USER", ""), os.environ.get("PGUSER", "")
    user, user_source = resolve_lakebase_user(
        app_user or pg_user,
        os.environ.get("DATABRICKS_CLIENT_ID", ""),
        env_label="app setting" if app_user else "database resource",
    )
    logger.info(f"Lakebase database from {database_source}; Lakebase user from {user_source}")
    return {
        "host": host or "localhost",
        "port": int(os.environ.get("LAKEBASE_PORT") or os.environ.get("PGPORT", "5432")),
        "database": database,
        "user": user,
        "endpoint": endpoint,
        "instance": instance,
    }


async def _target() -> dict:
    """The connection target, resolved in a thread. Cached only when it names an endpoint or
    instance (or a password is set), so a transient failure is retried on the next start."""
    global _TARGET
    if _TARGET is not None:
        return _TARGET
    target = await asyncio.to_thread(_resolve_target)
    if target["endpoint"] or target["instance"] or os.environ.get("LAKEBASE_PASSWORD"):
        _TARGET = target
    return target


# `require` encrypts the connection but performs NO certificate or hostname check,
# so it does not protect the assessment history against an in-path attacker.
# `verify-full` is the default here; the escape hatch exists only for a deployment
# whose Lakebase endpoint presents a certificate outside the container's CA bundle.
LAKEBASE_SSL_MODE = os.environ.get("LAKEBASE_SSL_MODE", "verify-full")


def _ssl_arg():
    """The asyncpg ``ssl`` argument for LAKEBASE_SSL_MODE.

    asyncpg's string modes ``verify-ca``/``verify-full`` look for a CA cert at
    ``~/.postgresql/root.crt``, which the Databricks Apps runtime does not ship — so
    passing the bare string makes pool creation fail and silently disables history.
    The Lakebase endpoint presents a publicly-signed certificate, so for the verify
    modes we build an SSLContext from a trusted CA bundle (certifi if present, else
    the system store) and let asyncpg verify against it: real certificate (and, for
    verify-full, hostname) verification without needing a bundled root.crt. The
    non-verifying modes (``require``/``prefer``/``allow``/``disable``) pass through
    to asyncpg as-is."""
    mode = (LAKEBASE_SSL_MODE or "").lower()
    if mode not in ("verify-ca", "verify-full"):
        return LAKEBASE_SSL_MODE
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    if mode == "verify-ca":
        # verify the chain but not the hostname (verify-full does both).
        ctx.check_hostname = False
    return ctx


# A Lakebase OAuth credential is valid for about 60 minutes. A new one is minted for new
# connections after this age; open connections stay authenticated.
CREDENTIAL_MAX_AGE = 45 * 60
_credential = {"token": "", "minted_at": 0.0}


async def _mint_credential(config: dict) -> str:
    token = await _fetch_db_credential(instance_name=config["instance"], endpoint_name=config["endpoint"]) or ""
    if token:
        _credential.update(token=token, minted_at=time.time())
    return token


async def _connection_password() -> str:
    """The password for a new pool connection (asyncpg calls this for each one).

    Reuses the current credential while it is fresh, else mints a new one. If minting fails,
    the current credential is used: it stays valid for about 15 more minutes, and the next new
    connection tries again."""
    if _credential["token"] and time.time() - _credential["minted_at"] < CREDENTIAL_MAX_AGE:
        return _credential["token"]
    token = await _mint_credential(await _target())
    if not token:
        logger.warning("Could not mint a new Lakebase credential; using the current one and retrying "
                       "on the next connection")
    return token or _credential["token"]


async def init_pool() -> None:
    """Create the asyncpg connection pool. Call during app startup."""
    global _pool
    try:
        import asyncpg

        config = await _target()
        password = os.environ.get("LAKEBASE_PASSWORD", "")

        # In a Databricks App with no explicit password, each new connection gets an OAuth
        # credential minted as the app service principal: for the Autoscaling endpoint, else
        # the Provisioned instance. The first one is minted now, so a failure shows at startup.
        if not password and os.environ.get("DATABRICKS_APP_NAME"):
            if not (config["endpoint"] or config["instance"]):
                logger.warning(
                    "Running in Databricks App but no Lakebase endpoint or instance was found "
                    "(LAKEBASE_ENDPOINT_NAME / LAKEBASE_INSTANCE_NAME empty, and the lakebase-db "
                    "resource gave neither); no Lakebase credential available"
                )
            elif await _mint_credential(config):
                password = _connection_password

        if not password:
            logger.warning(
                "Lakebase password not set and no instance credential available — "
                "assessment history will not persist"
            )
            _pool = None
            return

        logger.info(f"Connecting to Lakebase at {config['host']}:{config['port']}/{config['database']}")
        _pool = await asyncio.wait_for(
            asyncpg.create_pool(
                host=config["host"],
                port=config["port"],
                database=config["database"],
                user=config["user"],
                password=password,
                ssl=_ssl_arg(),
                min_size=2,
                max_size=10,
                command_timeout=30,
                # Idle connections are closed after 5 minutes, so reconnects pick up the
                # current credential well within its lifetime.
                max_inactive_connection_lifetime=300,
            ),
            timeout=10,  # Don't hang for more than 10s on pool creation
        )
        logger.info("Lakebase connection pool initialized successfully")
    except Exception as e:
        logger.warning(f"Failed to initialize Lakebase pool (will fall back to SQL Warehouse): {e}")
        _pool = None


async def close_pool() -> None:
    """Close the connection pool. Call during app shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Lakebase connection pool closed")


def is_available() -> bool:
    """Check if Lakebase pool is available."""
    return _pool is not None


async def get_pool():
    """Get the connection pool. Returns None if unavailable."""
    return _pool


# The schema the history tables live in. Chosen once at startup (see prepare_history_schema).
_HISTORY_SCHEMA = LEGACY_SCHEMA


def history_table(table: str) -> str:
    """The schema-qualified name of a history table, in the schema chosen at startup."""
    from server.history_schema import qualified_table
    return qualified_table(_HISTORY_SCHEMA, table)


async def prepare_history_schema() -> str:
    """Choose the history schema and create it if it is new. Returns the schema name.

    Existing tables in public are kept (their history is already there). A new database
    gets its own schema, because the app's service principal may lack CREATE on public."""
    global _HISTORY_SCHEMA
    from server.history_schema import (HISTORY_SCHEMA, HISTORY_TABLES, choose_history_schema,
                                       legacy_probe_sql, quote_ident)
    pool = await get_pool()
    if pool is None:
        raise RuntimeError("Lakebase pool is not available")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(legacy_probe_sql())
        schema, reason = choose_history_schema(HISTORY_TABLES if (row and row["legacy_present"]) else [])
        if schema != LEGACY_SCHEMA:
            await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {quote_ident(HISTORY_SCHEMA)}")
    _HISTORY_SCHEMA = schema
    logger.info(f"Lakebase history schema: {schema} ({reason})")
    return schema


async def start_history() -> bool:
    """Start Lakebase history. Never raises: any failure leaves history off and the app running.

    Returns True when history is ready. Called at startup, so an error here must not stop
    the app from starting (the UI must still load)."""
    from server.config import USE_LAKEBASE
    if not USE_LAKEBASE:
        return False
    try:
        await init_pool()
        if not is_available():
            logger.warning("Lakebase pool unavailable — history/plans disabled")
            return False
        await prepare_history_schema()
        from server.snapshots import ensure_table as ensure_snapshots
        from server.plans import ensure_table as ensure_plans
        await ensure_snapshots()
        await ensure_plans()
        logger.info("Lakebase pool ready — assessment history + plans enabled")
        return True
    except Exception as e:
        logger.warning(f"Lakebase history is off: startup failed ({type(e).__name__}). "
                       "The app runs without history. Check the grants and the database.")
        try:
            await close_pool()
        except Exception:
            pass
        return False


async def execute_query(query: str, params: Optional[list] = None) -> list[dict]:
    """Execute a SQL query against Lakebase and return results as list of dicts."""
    if _pool is None:
        raise RuntimeError("Lakebase pool not initialized")

    async with _pool.acquire() as conn:
        if params:
            rows = await conn.fetch(query, *params)
        else:
            rows = await conn.fetch(query)
        return [dict(row) for row in rows]
