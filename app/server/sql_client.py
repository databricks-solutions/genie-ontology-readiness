"""SQL Warehouse client using Databricks Statement Execution API."""

import aiohttp
import asyncio
import contextvars
import logging
from typing import Any, Optional
from server.config import get_workspace_host, get_auth_headers, get_user_token, FORCE_SP, WAREHOUSE_ID, CATALOG, SCHEMA
from server.user_agent import with_ua

logger = logging.getLogger(__name__)


# --- Per-signal identity attribution ------------------------------------------
# Records which identity actually served each read, so the assessment output can
# tell the end user whether a signal reflects THEIR grants (OBO) or the app service
# principal's. Scoped to the current task's context: the scorer calls
# start_identity_capture() at the head of each probe task (contextvars are copied
# per task), execute_sql appends the resolved mode, and resolved_identity()
# summarizes it after the probe returns.
_identity_recorder: contextvars.ContextVar = contextvars.ContextVar("identity_recorder", default=None)


def start_identity_capture() -> None:
    """Begin (or reset) identity capture for the current context/task."""
    _identity_recorder.set([])


def record_identity(mode: str) -> None:
    """Record one resolved-identity event ('obo', 'sp_fallback', 'sp_forced_env',
    'sp_forced_call', 'sp_no_token'). No-op when capture isn't active."""
    rec = _identity_recorder.get()
    if rec is not None:
        rec.append(mode)


def record_rest_identity() -> None:
    """Record the identity for a REST read that ALREADY succeeded and bypassed
    execute_sql (so it can't be captured centrally). Mirrors execute_sql's base
    OBO-vs-SP decision in one place, so REST callers don't hand-duplicate the mode
    vocabulary: FORCE_SP → the SP (sp_forced_env; get_user_token() is already None
    under it), a forwarded viewer token → OBO, otherwise the SP. REST reads have no
    per-call force_sp or fallback, so those modes don't apply here."""
    if FORCE_SP:
        record_identity("sp_forced_env")
    elif get_user_token():
        record_identity("obo")
    else:
        record_identity("sp_no_token")


# --- Per-signal query capture -------------------------------------------------
# Records the exact SQL each probe runs so the assessment can show "the query
# behind this score" (explainability). Scoped per probe task like identity
# capture: the scorer calls start_query_capture() at the head of each probe task,
# execute_sql appends each statement, and captured_queries() returns them.
_query_recorder: contextvars.ContextVar = contextvars.ContextVar("query_recorder", default=None)
# Set for an assessment run: its statements are submitted with no server-side wait, so the
# statement id is known at once and a Stop can cancel the statement (see execute_sql).
_submit_without_wait: contextvars.ContextVar = contextvars.ContextVar("submit_without_wait", default=False)


def submit_without_wait() -> None:
    """Make the statements of this task (and the tasks it starts) submit without waiting."""
    _submit_without_wait.set(True)


def start_query_capture() -> None:
    """Begin (or reset) SQL capture for the current context/task."""
    _query_recorder.set([])


def record_query(sql: str, parameters: Optional[dict[str, Any]] = None) -> None:
    """Record one executed statement. No-op when capture isn't active."""
    rec = _query_recorder.get()
    if rec is not None:
        entry: dict[str, Any] = {"sql": " ".join(sql.split())}
        if parameters:
            entry["parameters"] = {k: str(v) for k, v in parameters.items()}
        rec.append(entry)


def captured_queries() -> list[dict]:
    """The distinct statements recorded since start_query_capture() (may be empty).

    Deduplicated by SQL text + parameters, preserving first-seen order, so the
    "SQL behind this score" disclosure shows each representative query once
    instead of one row per identical repeated statement."""
    rec = _query_recorder.get()
    if not rec:
        return []
    seen: set = set()
    out: list[dict] = []
    for e in rec:
        key = (e.get("sql"), tuple(sorted((e.get("parameters") or {}).items())))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def resolved_identity() -> Optional[dict]:
    """Summarize the identities recorded since start_identity_capture().

    Returns None when nothing was recorded (e.g. a signal that did no instrumented
    read), else {ran_as, via, label, detail} describing the identity that served it.
    """
    rec = _identity_recorder.get()
    if not rec:
        return None
    modes = set(rec)
    obo = "obo" in modes
    fell_back = "sp_fallback" in modes
    sp_only = bool(modes & {"sp_forced_call", "sp_no_token"})

    if "sp_forced_env" in modes:
        return {"ran_as": "service_principal", "via": "sp_forced_env",
                "label": "App service principal (FORCE_SP)",
                "detail": "This deploy runs in SP-only mode (FORCE_SP); the signal reflects the app "
                          "service principal's grants, not any individual viewer."}
    if obo and (fell_back or sp_only):
        return {"ran_as": "mixed", "via": "sp_fallback",
                "label": "You + app service principal",
                "detail": "Some reads ran on-behalf-of-you and others as the app service principal "
                          "(fallback or an SP-only read), so this signal is a mix of both identities."}
    if fell_back:
        return {"ran_as": "service_principal", "via": "sp_fallback",
                "label": "App service principal (fell back)",
                "detail": "Your account couldn't read this, so it fell back to the app service "
                          "principal — the signal reflects the SP's grants, not yours."}
    if sp_only:
        return {"ran_as": "service_principal", "via": "sp_only",
                "label": "App service principal",
                "detail": "Read as the app service principal, so the signal reflects the SP's grants "
                          "rather than any individual viewer."}
    return {"ran_as": "user", "via": "obo",
            "label": "Your account (on-behalf-of-you)",
            "detail": "Read on-behalf-of-you; this signal reflects your own Unity Catalog grants."}


# How long (wall-clock) to poll a statement that is still running after the initial
# 50 s wait. The old loop counted 120 passes of a 1 s sleep plus a status round-trip
# (~160-180 s in practice); 180 s keeps that, so slow probes such as the Genie audit
# scan on a large account don't start timing out.
DEFAULT_POLL_SECONDS = 180
# The server-side wait on submit, when a caller does not choose one.
DEFAULT_SUBMIT_WAIT_SECONDS = 50


class StatementTimeout(Exception):
    """A statement did not finish within its poll budget (it was cancelled)."""


class _TransientRead(Exception):
    """A status read the proxy/gateway answered with a retryable HTTP status."""


# Proxy/gateway statuses worth retrying on a status read; any other 4xx/5xx
# (e.g. an expired token's 401/403) fails the statement at once.
_TRANSIENT_HTTP = frozenset({429, 500, 502, 503, 504})


async def execute_sql(query: str, parameters: Optional[dict[str, Any]] = None, force_sp: bool = False,
                      record: bool = True, poll_seconds: int = DEFAULT_POLL_SECONDS,
                      wait_seconds: Optional[int] = None) -> list[dict]:
    """Execute a SQL query against the Databricks SQL Warehouse.

    Identity model — every signal defaults to on-behalf-of-user (OBO):

    * ``force_sp=True`` is the **override**: run as the app service principal and
      never attempt OBO (for reads the OBO token can't do — e.g. the Genie REST
      API, which the ``sql`` scope doesn't cover, or Lakebase credential minting).
    * Otherwise, when a forwarded end-user token is present, run **as the viewer**
      (OBO). If that read fails (typically because the viewer lacks a grant the
      app SP holds — e.g. system tables), transparently **fall back to the SP**.
    * With no forwarded token (local dev / unattended / scheduled), there is no
      OBO identity, so the read runs as the SP directly.
    * The deploy-time ``FORCE_SP`` knob (config) forces SP-only mode for the whole
      app — OBO is never attempted — regardless of a forwarded token.

    NOTE: the SP fallback trades a little of the "reflects exactly what *you* can
    see" OBO promise for completeness — a viewer may see a signal via the SP that
    they couldn't read themselves. That is the intended behaviour here.
    """
    # Record the statement for the "SQL behind this score" disclosure. Best-effort,
    # scoped to the probe task; a no-op when capture isn't active (e.g. unit tests).
    # record=False lets a chunked scan (e.g. per-catalog batches) opt out and record
    # a single representative query instead of one row per near-identical batch.
    if record:
        record_query(query, parameters)
    if wait_seconds is None:
        if _submit_without_wait.get():
            # An assessment run submits with no wait (its statement id is known, so Stop can
            # cancel it). The server wait it skips is added to the poll budget, so the
            # statement keeps the total time a default submit gives it.
            wait_seconds = 0
            poll_seconds = poll_seconds + DEFAULT_SUBMIT_WAIT_SECONDS
        else:
            wait_seconds = DEFAULT_SUBMIT_WAIT_SECONDS

    # Override: SP only, no OBO attempt, no fallback. Either the per-call override
    # (force_sp) or the deploy-time FORCE_SP knob (SP-only mode for the whole app).
    if force_sp or FORCE_SP:
        record_identity("sp_forced_env" if FORCE_SP else "sp_forced_call")
        return await _execute_once(query, parameters, force_sp=True, poll_seconds=poll_seconds, wait_seconds=wait_seconds)

    # No forwarded viewer token → nothing to run OBO as; go straight to the SP.
    if not get_user_token():
        record_identity("sp_no_token")
        return await _execute_once(query, parameters, force_sp=True, poll_seconds=poll_seconds, wait_seconds=wait_seconds)

    # Default: try OBO (as the viewer). Fall back to the SP only when the failure
    # looks like an AUTHORIZATION gap (the case the SP can actually cover). Other
    # failures (timeout, bad SQL, warehouse down) would fail identically as the SP,
    # so re-raise them instead of doubling latency with a pointless retry.
    try:
        rows = await _execute_once(query, parameters, force_sp=False, poll_seconds=poll_seconds, wait_seconds=wait_seconds)
    except Exception as obo_err:
        if not _is_authz_error(obo_err):
            raise
        logger.warning(f"OBO read denied ({str(obo_err)[:160]}); falling back to app SP")
        try:
            sp_rows = await _execute_once(query, parameters, force_sp=True, poll_seconds=poll_seconds, wait_seconds=wait_seconds)
        except Exception as sp_err:
            # The SP fallback failed too (neither the viewer nor the app SP can
            # read this). Chain the original OBO denial so it stays visible in the
            # traceback instead of being masked by the SP's error. Do NOT record an
            # identity here: the read was served by no one, so the signal is
            # unavailable and must not be attributed to the SP.
            raise sp_err from obo_err
        # Record the fallback only once the SP has actually served the read.
        record_identity("sp_fallback")
        return sp_rows
    else:
        record_identity("obo")
        return rows


# Substrings that mark a failure as an authorization/permission problem — the only
# case where retrying as the app SP can succeed. Matched case-insensitively against
# the SQL Warehouse / Statement Execution error text.
_AUTHZ_MARKERS = (
    # Explicit permission / authorization denials.
    "permission_denied", "does not have", "not authorized", "access denied",
    "forbidden", "insufficient priv", "unauthorized", "no permission",
    "privilege",            # "requires ... privilege", "insufficient privileges" (narrower than bare "requires")
    "(401)", "(403)",       # HTTP status is wrapped as "SQL Warehouse error (403): ..." — no leading space
    # UC often surfaces a missing catalog/schema grant as the object simply not
    # existing (e.g. [TABLE_OR_VIEW_NOT_FOUND] "... cannot be found", "Catalog system
    # not found"). For the fixed system / information_schema probes here, not-found
    # almost always means a grant the app SP holds is missing for the viewer — so
    # treat it as an authz gap worth the SP fallback rather than dropping the signal.
    # Cover both the error-class tokens ("not_found", "does_not_exist") and the prose forms.
    "not found", "not_found", "cannot be found", "does not exist", "does_not_exist",
)


def _is_authz_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _AUTHZ_MARKERS)


async def _execute_once(query: str, parameters: Optional[dict[str, Any]], force_sp: bool,
                        poll_seconds: int = DEFAULT_POLL_SECONDS,
                        wait_seconds: int = DEFAULT_SUBMIT_WAIT_SECONDS) -> list[dict]:
    """Run the query once with a single resolved identity (OBO or SP).

    ``wait_seconds`` is the server-side wait on submit (0-50). 0 returns the
    statement id at once, so the statement can always be cancelled (a task
    cancelled during a 50 s submit wait can't cancel what it can't name)."""
    host = get_workspace_host()
    auth_headers = get_auth_headers(force_sp=force_sp)

    if not host:
        raise Exception("DATABRICKS_HOST not configured")
    if not auth_headers:
        raise Exception("No authentication headers available")

    url = f"{host}/api/2.0/sql/statements"
    headers = {
        **auth_headers,
        "Content-Type": "application/json",
    }

    logger.info(f"Executing SQL against warehouse {WAREHOUSE_ID}, host: {host}")

    payload = {
        "warehouse_id": WAREHOUSE_ID,
        "statement": query,
        "catalog": CATALOG,
        "schema": SCHEMA,
        # 0 s or 5-50 s (the API rejects 1-4 s).
        "wait_timeout": f"{0 if wait_seconds <= 0 else min(50, max(5, int(wait_seconds)))}s",
        "disposition": "INLINE",
        "format": "JSON_ARRAY",
    }

    if parameters:
        params_list = []
        for name, value in parameters.items():
            param_type = "STRING"
            if isinstance(value, int):
                param_type = "INT"
            elif isinstance(value, float):
                param_type = "DOUBLE"
            params_list.append({"name": name, "value": str(value), "type": param_type})
        payload["parameters"] = params_list

    # `wait_timeout` bounds the warehouse side; this bounds the HTTP side, so a
    # stalled connection cannot hold a worker slot indefinitely (CWE-400). Reads
    # are bounded per-socket rather than in total because _poll_statement reuses
    # this session for up to two minutes of polling.
    timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10, sock_read=90)
    async with aiohttp.ClientSession(timeout=timeout, headers=with_ua()) as session:
        # A 0 s submit returns at once, so bound it tightly; a waiting submit keeps the
        # session's per-read limit (the server itself holds it up to wait_timeout).
        # (timeout=None would REMOVE the session's limits, so pass it only when set.)
        submit_kwargs = {"timeout": aiohttp.ClientTimeout(total=30)} if wait_seconds <= 0 else {}
        # The submit runs as its own task, so a Stop that lands while it is in flight can
        # still find the statement it creates (see _cancel_submit_in_flight).
        submit = asyncio.ensure_future(_post_statement(session, url, payload, headers, submit_kwargs))
        try:
            result = await asyncio.shield(submit)
        except asyncio.CancelledError:
            if wait_seconds <= 0:
                # A no-wait submit names its statement at once, so a cancel can cancel it.
                await _cancel_submit_in_flight(session, host, auth_headers, submit)
            else:
                # A waiting submit only names its statement after its server wait, so there
                # is nothing to cancel in time: stop it and let the cancel through at once.
                submit.cancel()
            raise

        status = result.get("status", {}).get("state", "")
        if status == "FAILED":
            error_msg = result.get("status", {}).get("error", {}).get("message", "Unknown error")
            logger.error(f"SQL query failed: {error_msg}")
            raise Exception(f"SQL query failed: {error_msg}")

        # Handle PENDING/RUNNING -- poll until complete
        if status in ("PENDING", "RUNNING"):
            statement_id = result.get("statement_id")
            result = await _poll_statement(session, host, auth_headers, statement_id, poll_seconds)

        return _parse_result(result)


async def _post_statement(session: aiohttp.ClientSession, url: str, payload: dict, headers: dict,
                          submit_kwargs: dict) -> dict:
    """Submit one statement and return the reply. A non-200 reply raises."""
    async with session.post(url, json=payload, headers=headers, **submit_kwargs) as response:
        if response.status != 200:
            error_text = await response.text()
            # Truncated for the log only; the exception keeps the full text so
            # execute_sql's authorization-marker matching still sees it.
            logger.error(f"SQL Warehouse error ({response.status}): {error_text[:2000]}")
            raise Exception(f"SQL Warehouse error ({response.status}): {error_text}")
        return await response.json()


# How long a Stop waits for a submit still in flight to name its statement. A run's
# submit has no server wait, so the reply normally takes about a second.
SUBMIT_CANCEL_WAIT_SECONDS = 10.0


async def _cancel_submit_in_flight(session: aiohttp.ClientSession, host: str, auth_headers: dict,
                                   submit: "asyncio.Future") -> None:
    """A cancel landed while the statement was being submitted, so its id was not known.
    Let the submit finish (bounded), then cancel the statement it made. The caller still
    re-raises the cancel; this never swallows it."""
    try:
        result = await asyncio.wait_for(asyncio.shield(submit), SUBMIT_CANCEL_WAIT_SECONDS)
    except asyncio.TimeoutError:
        submit.cancel()
        logger.info("submit did not reply within %ss of a cancel; giving up on its statement",
                    SUBMIT_CANCEL_WAIT_SECONDS)
        return
    except Exception:
        return  # the submit failed: no statement was made
    statement_id = result.get("statement_id") if isinstance(result, dict) else None
    if statement_id:
        await _cancel_statement(session, f"{host}/api/2.0/sql/statements/{statement_id}", auth_headers,
                                statement_id)


async def _poll_statement(session: aiohttp.ClientSession, host: str, auth_headers: dict, statement_id: str,
                          poll_seconds: int = DEFAULT_POLL_SECONDS) -> dict:
    """Poll for statement completion until a ``poll_seconds`` wall-clock deadline.

    If the statement doesn't finish (deadline passed, or the calling task was
    cancelled: a stalled scan, the run cap, an abandoned run), it is cancelled on
    the warehouse so it doesn't keep running there."""
    url = f"{host}/api/2.0/sql/statements/{statement_id}"
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, float(poll_seconds))
    finished = False
    # Start quickly (a statement that is already done is read sooner), then settle at 1 s.
    poll_delay = 0.1
    try:
        while True:
            await asyncio.sleep(poll_delay)
            poll_delay = min(poll_delay * 2, 1.0)
            # Bound each status read by the time left (the session allows 90 s per
            # read), so a query ends near its deadline; there is always one read at
            # or after the deadline, so a statement that just finished isn't lost.
            # A TRANSIENT read failure (slow read, dropped connection) is retried; an
            # HTTP error (e.g. an expired token's 401/403 page) is raised at once.
            last = loop.time() >= deadline
            remaining = max(2.0, deadline - loop.time())
            try:
                async with session.get(url, headers=auth_headers,
                                       timeout=aiohttp.ClientTimeout(total=min(30.0, remaining))) as resp:
                    code = getattr(resp, "status", 200)
                    if code in _TRANSIENT_HTTP:
                        raise _TransientRead(f"HTTP {code}")
                    if code >= 400:
                        raise Exception(f"SQL Warehouse error ({code}) reading statement {statement_id}")
                    result = await resp.json()
            except (asyncio.TimeoutError, aiohttp.ClientConnectionError, aiohttp.ClientPayloadError,
                    _TransientRead) as e:
                if last:
                    break
                logger.info(f"status read for statement {statement_id} failed ({type(e).__name__}: {e}); retrying")
                continue
            state = result.get("status", {}).get("state", "")
            if state == "SUCCEEDED":
                finished = True
                return result
            elif state == "FAILED":
                finished = True
                error_msg = result.get("status", {}).get("error", {}).get("message", "Unknown")
                raise Exception(f"SQL query failed: {error_msg}")
            elif state in ("CANCELED", "CLOSED"):
                finished = True
                raise Exception(f"SQL query {state.lower()}")
            if last:
                break
        raise StatementTimeout(f"SQL query timed out after {int(poll_seconds)} seconds")
    finally:
        if not finished:
            await _cancel_statement(session, url, auth_headers, statement_id)


async def _cancel_statement(session: aiohttp.ClientSession, url: str, auth_headers: dict, statement_id: str) -> None:
    """Best-effort cancel; bounded so a cancelled task isn't held up by it."""
    try:
        async def _post():
            async with session.post(f"{url}/cancel", headers=auth_headers) as resp:
                await resp.read()
        await asyncio.wait_for(_post(), timeout=5)
    except Exception as e:  # incl. asyncio.TimeoutError; a CancelledError propagates (never swallow a run cancel)
        logger.info(f"could not cancel statement {statement_id}: {type(e).__name__}")


def _parse_result(result: dict) -> list[dict]:
    """Parse Statement Execution API response into list of dicts."""
    manifest = result.get("manifest", {})
    columns = manifest.get("schema", {}).get("columns", [])
    col_names = [c["name"] for c in columns]

    data_array = result.get("result", {}).get("data_array", [])

    rows = []
    for row_data in data_array:
        row = {}
        for i, col_name in enumerate(col_names):
            row[col_name] = row_data[i] if i < len(row_data) else None
        rows.append(row)

    return rows
