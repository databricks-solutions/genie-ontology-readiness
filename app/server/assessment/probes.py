"""Read-only probes that inspect the customer's environment.

Every probe is defensive: if a view is missing or the service principal lacks
access, the probe returns ``available=False`` with a human-readable note rather
than raising. Each probe returns a uniform shape:

    {
        "available": bool,
        "score": float,          # 0-100 (technical signal only)
        "signals": [ {"label", "value", "detail"} ],
        "gaps": [str, ...],
        "note": str | None,
        "metrics": { ... },
    }

DATA SOURCE RESILIENCE: an assessment ideally reads the metastore-wide
``system.information_schema`` (one query covers everything). But that requires
the app service principal to be an account admin / granted on system schemas,
which customers often won't do for an app SP. So we resolve the source: if
``system.information_schema`` is readable we use it; otherwise we fall back to
unioning each accessible catalog's own ``<catalog>.information_schema`` — which
only needs catalog-level SELECT (grantable by any catalog owner). SP-only runs
cache this for the process lifetime; under on-behalf-of-user auth it is re-run
per request (the viewer's grants differ from the SP's). Within one assessment it
is resolved once and shared across all probes via a request-scoped contextvar —
see ``prime_request_sources``.
"""

import asyncio
import contextvars
import datetime
import functools
import json
import logging
import aiohttp

from server.signal_spec import (
    METADATA as METADATA_SPEC,
    UC_FOUNDATION as UC_SPEC,
    REL_MODELING as REL_SPEC,
    REL_GOLD_POINTS,
    REL_FK_POINTS,
    REL_PK_POINTS,
    GOLD_SCHEMA_PATTERN,
    GOLD_TABLE_PATTERN,
    METRICS,
    GENIE_AGENTS as GENIE_SPEC,
    GENIE_AUDIT_LOOKBACK_DAYS,
    GENIE_ACTIVE_WINDOW_DAYS,
    GENIE_NAME_LOOKBACK_DAYS,
    GENIE_CONVERSATION_ACTIONS,
    GENIE_DASHBOARD_ACTIONS,
    genie_usage_score,
    ADOPTION as ADOPTION_SPEC,
    ADOPTION_QUERY_POINTS,
    ADOPTION_WINDOW_DAYS,
    adoption_band_points,
    METRIC_VIEW_TYPES,
    METRIC_BASE_POINTS,
    METRIC_RAMP_POINTS,
    METRIC_RAMP_VIEWS,
    METRIC_QUALITY_POINTS,
    METRIC_FEW_VIEWS,
    TABLE_COMMENT_TARGET_PCT,
    COLUMN_COMMENT_TARGET_PCT,
    METADATA_TABLE_WEIGHT,
    METADATA_COLUMN_WEIGHT,
    METADATA_COLUMN_BATCH_SIZE,
    METADATA_NO_PROGRESS_TIMEOUT_S,
    UC_CATALOG_POINTS,
    UC_TABLE_POINTS,
    UC_FOOTPRINT_POINTS,
    UC_SCHEMA_POINTS,
    UC_FOOTPRINT_TABLES,
    UC_SCHEMA_MIN,
    gap_text,
    render_sql,
)
from server.security import quote_ident, quote_literal, safe_error
from server.sql_client import (
    StatementTimeout, execute_sql, record_identity, record_query, record_rest_identity, _is_authz_error,
)
from server.user_agent import with_ua
from server.config import (
    get_workspace_host,
    get_auth_headers,
    ASSESS_CATALOGS,
    get_user_token,
)
from server.workspace_filter import (
    workspace_predicate,
    is_multi_workspace,
    get_workspace_filter,
    get_catalog_scope,
    get_schema_scope,
)
from server.bindings import accessible_catalogs

logger = logging.getLogger(__name__)

# Bound every probe REST read. A probe that never returns holds its worker slot
# and its connection open, and the assessment fan-out multiplies that (CWE-400).
_PROBE_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10, sock_connect=10, sock_read=20)

# Catalogs that are never part of a customer's own data estate.
# Excluded from the *Unity Catalog* footprint. `hive_metastore` is the legacy
# workspace-local metastore (not UC) — it is counted separately for UC coverage.
_INTERNAL_CATALOGS = ("system", "__databricks_internal", "samples", "hive_metastore")


def _empty(note: str, reason: str | None = None) -> dict:
    """Unavailable-signal result. ``reason`` distinguishes an access/scan failure
    from a genuine empty result so the UI never shows a swallowed failure as a
    confident 0 (issue #20): one of 'insufficient_permission', 'scan_failed',
    'not_enabled', or None."""
    return {"available": False, "score": 0.0, "signals": [], "gaps": [], "note": note,
            "metrics": {}, "unavailable_reason": reason}


def _is_not_enabled(exc: Exception) -> bool:
    """Heuristic: the read failed because the table/schema doesn't exist — e.g. a
    system schema (system.access / system.query) that isn't enabled on the
    metastore — rather than an access denial. Used to render the neutral
    'not available' state instead of a red 'insufficient permission' lock (#20)."""
    m = str(exc).lower()
    return any(k in m for k in (
        "table_or_view_not_found", "schema_not_found", "does not exist",
        "not found", "no such table", "no such schema", "cannot be found",
    ))


def _reason_for(exc: Exception) -> str:
    """Classify a probe read failure for the availability state (issue #20).

    Reuses execute_sql's authorization detection: an authz/permission failure ->
    'insufficient_permission' (the SP or viewer lacks a grant); a missing
    table/schema (system tables not enabled) -> 'not_enabled'; anything else
    (timeout, warehouse down, bad response) -> 'scan_failed'."""
    if _is_authz_error(exc):
        return "insufficient_permission"
    if _is_not_enabled(exc):
        return "not_enabled"
    return "scan_failed"


# Column keys that the drill-down modal offers as slice-by filters.
_DIMENSION_KEYS = ("workspace", "catalog", "schema", "agent", "type")


def _drill(title: str, columns: list[dict], rows: list[dict], total_rows: int | None = None) -> dict | None:
    """Uniform per-asset drill-down payload, or None when there's nothing to show.
    ``columns`` is [{key,label,unit?}]; ``rows`` are dicts keyed by column key.
    ``dimensions`` (auto-derived from the columns) names the columns the UI can
    filter by (workspace / catalog / schema / agent)."""
    if not rows:
        return None
    dimensions = [c["key"] for c in columns if c["key"] in _DIMENSION_KEYS]
    out = {"title": title, "columns": columns, "rows": rows, "dimensions": dimensions}
    if total_rows is not None and total_rows > len(rows):
        out["total_rows"] = total_rows  # rows were capped server-side (UI says so)
    return out


def _failed(exc: Exception, what: str, remedy: str = "") -> dict:
    """An unavailable-pillar result for a probe that raised.

    The probe's note is returned to the browser AND persisted inside the saved
    snapshot, so it must never carry the upstream message: SQL Warehouse errors
    quote the failing statement, the object names involved and sometimes a literal
    value from a column (CWE-209). The detail goes to the log under `reference`.
    """
    reference, _ = safe_error(exc, f"probe: {what}", logger)
    note = f"{what} could not be read."
    if remedy:
        note += f" {remedy}"
    # Carry the availability reason (#20) so the UI still distinguishes a permission
    # gap / not-enabled / scan failure — sanitized message, but not a flat "failed".
    return _empty(f"{note} (reference {reference})", reason=_reason_for(exc))


def _no_catalogs_note(what: str) -> str:
    """Zero-readable-catalogs message. Under on-behalf-of-user the reads run as the
    viewer and fall back to the app SP on authorization errors, so when nothing is
    readable the fix may be a grant on either identity — name both. Without a viewer
    token the reads run as the app SP only, so blame that."""
    if get_user_token():
        return (f"No catalogs are readable for the {what}. Ensure your user account — or the "
                f"app service principal — has USE CATALOG + SELECT on the catalogs to assess.")
    return (f"No readable catalogs for the {what}. Grant the app service principal "
            f"USE CATALOG + SELECT on the catalogs to assess.")


async def _scalar(query: str, force_sp: bool = False, parameters: dict | None = None):
    """First column of the first row, coercing numeric strings.

    The Statement Execution API returns every value as a string in JSON_ARRAY
    format, so COUNT(*) comes back as e.g. "8" — coerce to int/float so callers
    can compare numerically.

    ``force_sp=True`` is the SP-only override; by default reads run OBO with an
    automatic SP fallback (see ``execute_sql``).
    """
    rows = await execute_sql(query, parameters=parameters or None, force_sp=force_sp)
    if not rows:
        return None
    val = list(rows[0].values())[0]
    if isinstance(val, str):
        s = val.strip()
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return val
    return val


def _pct(num, den) -> float:
    num = float(num or 0)
    den = float(den or 0)
    return round(100.0 * num / den, 1) if den else 0.0


def _spec_gap(pillar: dict, gap_id: str, gaps: list, gap_ids: list, **values) -> None:
    """Append a spec gap's App text and its id together, so ``gaps`` and ``gap_ids``
    stay aligned one-to-one (the shared contract for #88)."""
    gaps.append(gap_text(pillar, gap_id, **values))
    gap_ids.append(gap_id)


def _with_gap_ids(probe):
    """Every migrated probe result carries ``gap_ids`` next to ``gaps``. Results with no
    gaps (unavailable, or nothing to flag) get an empty list."""
    @functools.wraps(probe)
    async def wrapped():
        result = await probe()
        result.setdefault("gap_ids", [])
        # A probe that adds a gap string must add its id too; fail loudly rather than drift.
        if len(result["gap_ids"]) != len(result.get("gaps") or []):
            raise AssertionError(f"{probe.__name__}: gap_ids and gaps differ in length")
        return result
    return wrapped


# ---------------------------------------------------------------------------
# Progress channel
# ---------------------------------------------------------------------------
# A probe can report intra-probe progress (e.g. the metadata scan working through
# catalog batches) by pushing events onto a queue the streaming assessment drains.
# run_assessment_stream primes this contextvar with a queue before dispatching
# probes; contextvars are copied into each probe task, so a probe can find the sink
# and emit without any change to its call signature. When no queue is primed
# (non-streaming run_assessment, or unit tests) emitting is a no-op.
_progress_sink: contextvars.ContextVar = contextvars.ContextVar("assessment_progress_sink", default=None)


def _emit_progress(key: str, done: int, total: int, detail: str) -> None:
    """Best-effort: push a pillar-progress event to the streaming sink if primed.
    Never blocks and never raises into the probe (progress is advisory)."""
    sink = _progress_sink.get()
    if sink is None:
        return
    try:
        sink.put_nowait({"type": "pillar_progress", "key": key, "done": done, "total": total, "detail": detail})
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------
# SP-only cache: used when no user token is present (scheduled/unattended mode).
_sources_sp = None
_sources_sp_lock = asyncio.Lock()

# Request-scoped resolution: primed once per assessment (prime_request_sources)
# and shared across the probes the scorer dispatches concurrently, so a single
# assessment resolves sources ONCE instead of once per probe. contextvars are
# copied into each task at gather()/ensure_future()-time, so priming in the parent
# context before dispatch propagates the value to every probe task.
_request_sources: contextvars.ContextVar = contextvars.ContextVar("request_sources", default=None)


async def _resolve_sources() -> dict:
    """Determine whether system.information_schema is readable and which
    catalogs to assess.

    If a resolution was primed for this assessment (prime_request_sources), return
    it so every probe in the assessment shares one resolution. Otherwise: when a
    forwarded end-user token is present (on-behalf-of-user), resolve fresh — the
    user's Unity Catalog grants may differ from the SP's; when no user token is
    present (SP-only), the result is cached for the process lifetime.
    """
    primed = _request_sources.get()
    if primed is not None:
        return primed

    global _sources_sp
    has_user_token = get_user_token() is not None

    # A workspace/catalog scope makes the resolved catalog set request-specific, so
    # the process-wide SP cache (which is unscoped) must not be used or populated.
    scoped = bool(get_catalog_scope()) or bool(get_schema_scope()) or _has_workspace_scope()
    if scoped:
        return await _do_resolve_sources()

    # For SP-only requests, use the global cache.
    if not has_user_token:
        if _sources_sp is not None:
            return _sources_sp
        async with _sources_sp_lock:
            if _sources_sp is not None:
                return _sources_sp
            _sources_sp = await _do_resolve_sources()
            return _sources_sp

    # For OBO (user-token) requests, resolve fresh every time so the result
    # reflects the viewing user's permissions.
    return await _do_resolve_sources()


async def prime_request_sources() -> dict:
    """Resolve the assessment's data sources ONCE and cache them in a request-scoped
    contextvar so the probes dispatched concurrently by the scorer reuse a single
    resolution. Without this, each probe calls _resolve_sources independently — and
    under OBO (where the SP cache is bypassed) that meant every probe re-ran the
    system probe + catalog enumeration + per-catalog readability checks, an N× fan-out.

    Call this in the assessment context BEFORE dispatching probes: contextvars set
    here are inherited by each probe task created by asyncio.gather/ensure_future.
    """
    s = await _resolve_sources()
    _request_sources.set(s)
    return s


def _has_workspace_scope() -> bool:
    """True when an include-mode workspace filter with specific ids is active —
    the case where the catalog-metadata pillars scope to the bound catalogs."""
    f = get_workspace_filter()
    return bool(f and f.get("mode") == "include" and f.get("workspace_ids"))


async def _scoped_catalogs() -> list[str] | None:
    """The explicit catalog list for this assessment, or None to enumerate.

    Precedence: per-request catalog scope (catalog filter) > catalogs bound to the
    selected workspaces (UC bindings) > ASSESS_CATALOGS env. None means "no explicit
    scope — enumerate all visible catalogs" (today's default)."""
    override = get_catalog_scope()
    if override:
        return override
    f = get_workspace_filter()
    if f and f.get("mode") == "include" and f.get("workspace_ids"):
        accessible = await accessible_catalogs(set(f["workspace_ids"]))
        if accessible is not None:  # None → bindings unreadable; fall through
            return [c["name"] for c in accessible]
    if ASSESS_CATALOGS:
        return list(ASSESS_CATALOGS)
    return None


async def _do_resolve_sources() -> dict:
    """Core source resolution logic (called by _resolve_sources)."""
    system_ok = False
    try:
        await execute_sql("SELECT 1 FROM system.information_schema.tables LIMIT 1")
        system_ok = True
    except Exception:
        system_ok = False

    explicit = await _scoped_catalogs()
    catalogs = list(explicit) if explicit is not None else []
    if explicit is None:
        rows = []
        try:
            if system_ok:
                rows = await execute_sql("SELECT catalog_name AS c FROM system.information_schema.catalogs")
                catalogs = [r.get("c") for r in rows]
            else:
                rows = await execute_sql("SHOW CATALOGS")
                catalogs = [list(r.values())[0] for r in rows]
        except Exception as e:
            logger.warning(f"catalog enumeration failed: {e}")
            catalogs = []
        catalogs = [
            c for c in catalogs
            if c and c not in _INTERNAL_CATALOGS and not c.startswith("__")
        ]

    # When restricted to an explicit catalog list (catalog filter / bindings /
    # ASSESS_CATALOGS), prefer per-catalog reads even if system is readable, so we
    # scope precisely and never depend on system grants we can't assume.
    use_system = system_ok and explicit is None
    # Catalogs dropped because their information_schema couldn't be read (or isn't
    # UC-shaped). Reported on the Metadata pillar when the user chose them.
    excluded: list[str] = []

    # In per-catalog mode, SHOW CATALOGS may list catalogs the SP can only
    # BROWSE (not SELECT) — querying their information_schema would fail and
    # break the UNION. Keep only catalogs whose information_schema is
    # actually readable, tested concurrently.
    if not use_system and catalogs:
        # Bound concurrency so a wide metastore (many catalogs) can't fan out an
        # unbounded burst of readability probes that trips the warehouse's
        # max-concurrent-queries / API rate limits — which would make _readable
        # spuriously return False and silently drop a catalog the user can read.
        _sem = asyncio.Semaphore(10)
        async def _readable(c: str) -> bool:
            async with _sem:
                try:
                    # Name UC columns (not SELECT 1): a Lakebase database catalog has a
                    # Postgres-shaped information_schema (no `comment`, other columns,
                    # missing views), which would break every `SELECT * … UNION ALL`
                    # source below and take down whole pillars.
                    await execute_sql(
                        "SELECT table_catalog, table_schema, table_name, table_type, comment "
                        f"FROM {quote_ident(c)}.information_schema.tables LIMIT 1"
                    )
                    return True
                except Exception:
                    return False
        checks = await asyncio.gather(*(_readable(c) for c in catalogs))
        accessible = [c for c, ok in zip(catalogs, checks) if ok]
        if accessible:
            excluded = [c for c, ok in zip(catalogs, checks) if not ok]
            catalogs = accessible
        logger.info(f"accessible catalogs: {len(catalogs)} of {len(checks)} discovered")

    # Schema allowlist is only meaningful for a single catalog (the UI only
    # offers it then). Ignore it otherwise so a leftover list can't silently
    # drop objects from other catalogs.
    schemas = get_schema_scope()
    if schemas and len(catalogs) != 1:
        schemas = None
    sources = {"system_ok": use_system, "catalogs": catalogs, "schemas": schemas,
               "excluded": excluded if explicit is not None else []}
    logger.info(
        f"assessment sources: system_ok={use_system}, catalogs={len(catalogs)}, "
        f"schemas={len(schemas) if schemas else 'all'}"
    )
    return sources


def schema_predicate(schemas: list[str] | None, column: str = "table_schema") -> str:
    """SQL fragment restricting *column* to an optional schema allowlist.

    With no allowlist, only ``information_schema`` is excluded (today's default).
    With an allowlist, match those names exactly — ``information_schema`` cannot
    appear unless the caller passed it.
    """
    if schemas:
        in_list = ", ".join(quote_literal(schema) for schema in schemas)
        return f"{column} IN ({in_list})"
    return f"{column} <> {quote_literal('information_schema')}"


def _schema_sql(sources: dict, column: str = "table_schema") -> str:
    """Predicate for the default information_schema exclusion, or a schema allowlist."""
    return schema_predicate(sources.get("schemas"), column)


def _schema_and(sources: dict, column: str = "table_schema") -> str:
    """Optional ``AND …`` fragment used on queries that have no default schema clause."""
    schemas = sources.get("schemas")
    if not schemas:
        return ""
    return f"AND {schema_predicate(schemas, column)} "


def _src(view: str, sources: dict) -> str | None:
    """FROM-able source for an information_schema view, aliased as _t."""
    if sources["system_ok"]:
        return f"system.information_schema.{view} AS _t"
    cats = sources["catalogs"]
    if not cats:
        return None
    union = " UNION ALL ".join(
        f"SELECT * FROM {quote_ident(c)}.information_schema.{view}" for c in cats
    )
    return f"({union}) AS _t"


def _internal_catalog_filter(sources: dict, column: str = "table_catalog") -> str:
    """Extra WHERE clause to exclude internal catalogs when grouping by
    ``table_catalog`` over the metastore-wide system view — the named internal
    catalogs plus any ``__``-prefixed system catalog (e.g. Databricks-internal
    lakeview/materialization catalogs). Per-catalog union mode is already scoped to
    the enumerated non-internal catalogs, so it needs none."""
    if not sources.get("system_ok"):
        return ""
    in_list = ", ".join("'" + c + "'" for c in _INTERNAL_CATALOGS)
    return f"AND {column} NOT IN ({in_list}) AND {column} NOT RLIKE '^__' "


# ---------------------------------------------------------------------------
# 1. Unity Catalog foundation
# ---------------------------------------------------------------------------
@_with_gap_ids
async def probe_uc_foundation() -> dict:
    s = await _resolve_sources()
    n_catalogs = len(s["catalogs"])
    if n_catalogs == 0 and not s["system_ok"]:
        return _empty(_no_catalogs_note("assessment"))
    q = UC_SPEC["queries_by_id"]
    label = UC_SPEC["signal_labels"]
    try:
        tbl = _src("tables", s)
        sch = _src("schemata", s)
        n_schemas = await _scalar(render_sql(q["schemas"]["sql"], {"<SCHEMATA>": sch},
                                             schema_filter=_schema_sql(s, "schema_name")).rstrip(";"))
        rows = await execute_sql(
            render_sql(q["tables"]["sql"], {"<TABLES>": tbl}, schema_filter=_schema_sql(s)).rstrip(";")
        )
        total = int(rows[0].get("total") or 0)
        managed = int(rows[0].get("managed") or 0)

        # Legacy (non-UC) footprint: tables still in the workspace-local Hive
        # metastore. Best-effort — hive_metastore exposes its own
        # information_schema in current runtimes; if it isn't readable we simply
        # omit the coverage signal rather than fail the pillar.
        non_uc = None
        # A single-schema assessment is about that schema, not the workspace's
        # leftover Hive footprint — skip the hive_metastore signal in that case.
        if not s.get("schemas"):
            try:
                non_uc = int(await _scalar(q["hive_tables"]["sql"].rstrip(";")) or 0)
            except Exception as e:
                logger.info(f"hive_metastore not readable for UC-coverage signal: {str(e)[:80]}")
                non_uc = None
        uc_coverage_pct = _pct(total, total + non_uc) if non_uc is not None else None

        score = 0.0
        if n_catalogs:
            score += UC_CATALOG_POINTS
        if total > 0:
            score += UC_TABLE_POINTS
        if total >= UC_FOOTPRINT_TABLES:
            score += UC_FOOTPRINT_POINTS
        if n_schemas and n_schemas >= UC_SCHEMA_MIN:
            score += UC_SCHEMA_POINTS
        score = min(score, 100.0)

        gaps: list[str] = []
        gap_ids: list[str] = []
        if n_catalogs == 0:
            _spec_gap(UC_SPEC, "no_user_catalogs", gaps, gap_ids)
        if total < UC_FOOTPRINT_TABLES:
            _spec_gap(UC_SPEC, "small_table_footprint", gaps, gap_ids)
        if non_uc:
            _spec_gap(UC_SPEC, "hive_metastore_tables", gaps, gap_ids,
                      non_uc=non_uc, not_uc_pct=round(100 - uc_coverage_pct, 1))

        signals = [
            {"label": label["catalogs"], "value": n_catalogs, "detail": "User catalogs assessed"},
            {"label": label["schemas"], "value": n_schemas, "detail": "Excluding information_schema"},
            {"label": label["managed"], "value": _pct(managed, total), "unit": "%", "detail": "% of tables that are UC-managed"},
        ]
        # Only surface the not-in-UC footprint (never the raw in-UC table count).
        if non_uc is not None:
            signals.append({
                "label": label["in_uc"], "value": uc_coverage_pct, "unit": "%",
                "detail": "Share of tables in Unity Catalog vs. legacy hive_metastore",
            })
            signals.append({
                "label": label["not_in_uc"], "value": non_uc,
                "detail": "Tables still in legacy hive_metastore",
            })

        # Proactively run the not-in-UC breakdown so the customer sees where the
        # legacy (non-UC) tables still sit — surfaced click-to-expand in the UI so
        # a long list doesn't dominate the pillar. Best-effort and bounded.
        legacy_by_schema = []
        if non_uc:
            try:
                lrows = await execute_sql(
                    "SELECT table_schema AS sch, COUNT(*) AS n "
                    "FROM hive_metastore.information_schema.tables "
                    "WHERE table_schema <> 'information_schema' GROUP BY table_schema ORDER BY n DESC LIMIT 50"
                )
                legacy_by_schema = [{"schema": r.get("sch"), "tables": int(r.get("n") or 0)} for r in lrows]
            except Exception as e:
                logger.info(f"legacy_by_schema breakdown failed: {str(e)[:80]}")

        # Per-schema drill-down (#10): where the UC table footprint sits and how
        # much of each schema is managed, sliceable by catalog/schema in the UI.
        drill_rows = []
        try:
            crows = await execute_sql(
                f"SELECT table_catalog AS catalog, table_schema AS schema, COUNT(*) AS tables, "
                f"       SUM(CASE WHEN table_type IN ('MANAGED','MANAGED_SHALLOW_CLONE') THEN 1 ELSE 0 END) AS managed "
                f"FROM {tbl} WHERE {_schema_sql(s)} {_internal_catalog_filter(s)}"
                f"GROUP BY table_catalog, table_schema ORDER BY tables DESC LIMIT 500"
            )
            drill_rows = [
                {"catalog": r.get("catalog"), "schema": r.get("schema"), "tables": int(r.get("tables") or 0),
                 "managed_pct": _pct(int(r.get("managed") or 0), int(r.get("tables") or 0))}
                for r in crows if r.get("catalog")
            ]
        except Exception as e:
            logger.info(f"uc_foundation per-schema drill-down failed: {str(e)[:80]}")

        return {
            "available": True,
            "score": score,
            "signals": signals,
            "gaps": gaps,
            "gap_ids": gap_ids,
            "note": None,
            "metrics": {
                "catalogs": n_catalogs, "schemas": n_schemas, "tables": total,
                "managed_pct": _pct(managed, total),
                "uc_tables": total, "non_uc_tables": non_uc, "uc_coverage_pct": uc_coverage_pct,
                "legacy_by_schema": legacy_by_schema,
            },
            "drill_down": _drill(
                "Tables by schema",
                [{"key": "catalog", "label": "Catalog"},
                 {"key": "schema", "label": "Schema"},
                 {"key": "tables", "label": "Tables"},
                 {"key": "managed_pct", "label": "Managed", "unit": "%"}],
                drill_rows,
            ),
        }
    except Exception as e:
        return _failed(e, "The Unity Catalog footprint",
                       "The assessing identity may lack USE CATALOG + SELECT on the catalogs to assess.")


# ---------------------------------------------------------------------------
# 2. Metadata richness (comments + tags)
# ---------------------------------------------------------------------------
# --- Metadata scan chunking -------------------------------------------------
# Column comment coverage is the heaviest metadata read: information_schema.columns
# has a row per column of every table. On a wide metastore (thousands of catalogs)
# one metastore-wide scan runs for many minutes and times out. Instead we scan in
# per-catalog batches with bounded concurrency, accumulating coverage and emitting
# progress as batches land. Tuned against a 1068-catalog workspace (a 30-catalog
# batch returns in ~7s): 25/batch × 6 concurrent finishes ~1068 catalogs in <1 min.
# Batch size and the no-progress timeout live in signal_spec, so the skill's
# documented batching (#112) matches this scan.
_METADATA_BATCH_SIZE = METADATA_COLUMN_BATCH_SIZE
_METADATA_SCAN_CONCURRENCY = 6
# Stall only if NO batch completes within this window (not a fixed total deadline),
# so a slow-but-progressing scan of a huge metastore is allowed to finish.
_METADATA_NO_PROGRESS_TIMEOUT = METADATA_NO_PROGRESS_TIMEOUT_S
# Time budget for ONE column-coverage query, longer than the client default (120 s)
# so a batch of large catalogs can finish. Scan queries submit without waiting (a
# <=30 s call that returns the statement id, so it is always cancellable), then
# poll: 30 s submit + 230 s + a <=30 s last status read + a 5 s cancel ≈ 295 s, at
# or under the 300 s no-progress stop. A query past it is cancelled and its
# catalogs retried alone (see _column_coverage_chunked).
_METADATA_QUERY_POLL_SECONDS = 230
# The per-schema drill-down keeps the worst N schemas. One row per schema is
# unbounded on a large metastore (2,000 catalogs ≈ 23k rows ≈ 3 MB per event).
_METADATA_DRILL_MAX_ROWS = 500
# Retries (split halves + single-catalog queries) per original batch, summed over
# the scan. Isolating one bad catalog in a batch of 25 by halving costs <= 10, so
# every batch can afford that; a statement timeout (25 singles) or widespread
# failures (a saturated warehouse) draw down the shared pool. When too little is
# left for singles a batch is halved instead; when nothing is left it's skipped.
# Total extra load is bounded at ~10x the original queries.
_METADATA_RETRY_BUDGET_PER_BATCH = 10

_COV_SELECT = ("SELECT COUNT(*) AS total, "
               "SUM(CASE WHEN comment IS NOT NULL AND comment <> '' THEN 1 ELSE 0 END) AS commented FROM ")


def column_coverage_batch_sql(
    catalogs_batch: list[str],
    system_ok: bool,
    schemas: list[str] | None = None,
) -> str:
    """Column comment coverage for one batch of catalogs, grouped by catalog/schema.

    The SQL is the shared spec query (``signal_spec.METADATA`` ``column_comments``,
    #112), so the skill runs the identical per-batch statement. system_ok: the
    metastore-wide view, pruned by ``table_catalog IN (...)``. Per-catalog: a UNION of
    just this batch's catalog views (the IN filter is then a no-op). Grouping by
    schema gives both the totals AND the drill-down rows in one pass.
    """
    q = METADATA_SPEC["queries_by_id"]["column_comments"]
    if system_ok:
        source = "system.information_schema.columns"
    else:
        # Name the three columns used (not SELECT *): a catalog whose information_schema
        # has other columns can then no longer break the UNION for the whole batch.
        union = " UNION ALL ".join(
            f"SELECT table_catalog, table_schema, comment FROM {quote_ident(c)}.information_schema.columns"
            for c in catalogs_batch
        )
        source = f"({union}) AS _c"
    # information_schema stores catalog names in lower case, and the comparison is
    # case-sensitive, so a configured name like "Sales" must match as 'sales'.
    in_list = ", ".join(quote_literal(c.lower()) for c in catalogs_batch)
    return render_sql(q["sql"], {"<COLUMNS>": source, "<CATALOG_BATCH>": in_list},
                      schema_filter=schema_predicate(schemas)).rstrip(";")


async def _column_coverage_chunked(
    catalogs: list[str],
    system_ok: bool,
    schemas: list[str] | None = None,
) -> tuple[int, int, dict, list[str]]:
    """(columns, commented-columns, per_schema, skipped) computed in per-catalog
    batches with bounded concurrency, where per_schema maps (catalog, schema) ->
    (columns, commented) and skipped lists the catalogs that could not be read.
    Emits pillar_progress as each batch lands and raises TimeoutError only if no
    batch completes within _METADATA_NO_PROGRESS_TIMEOUT."""
    batches = [catalogs[i:i + _METADATA_BATCH_SIZE] for i in range(0, len(catalogs), _METADATA_BATCH_SIZE)]
    total_cats = len(catalogs)
    # Record ONE representative column-coverage query for the "View SQL" disclosure;
    # the real scan below runs this per catalog batch (record=False) so the disclosure
    # shows the query shape once rather than one near-identical row per batch.
    _cov_view = "system.information_schema.columns" if system_ok else "<catalog>.information_schema.columns"
    record_query(
        render_sql(METADATA_SPEC["queries_by_id"]["column_comments"]["sql"],
                   {"<COLUMNS>": _cov_view, "<CATALOG_BATCH>": ":catalogs"},
                   schema_filter=schema_predicate(schemas)).rstrip(";"),
        {"catalogs": f"the {total_cats} catalog(s) in scope, scanned in batches of {_METADATA_BATCH_SIZE}"},
    )
    sem = asyncio.Semaphore(_METADATA_SCAN_CONCURRENCY)
    c_total = c_commented = scanned = ok_cats = 0
    # Floor: even a one-batch scan can retry one timed-out batch catalog by catalog.
    retry_budget = max(_METADATA_RETRY_BUDGET_PER_BATCH * len(batches), 2 * _METADATA_BATCH_SIZE)
    # ORIGINAL-batch failures before the first success are held, not retried. When
    # the queue drains with nothing read yet, up to 6 single catalogs (evenly spread
    # over the held batches) are tried as a canary. Once every canary has returned:
    # if any read, the held batches are released and retried normally (minus the
    # canaries, so nothing is counted twice); if none did, the failure is systemic
    # (warehouse down, access lost) and the scan ends with "all failed".
    held: list[tuple[list[str], Exception]] = []
    canary_used = False
    canaries_in_flight: set[str] = set()
    settled: set[str] = set()  # catalogs already read or skipped (never re-counted)
    per_schema: dict[tuple[str, str], tuple[int, int]] = {}
    skipped: list[str] = []

    async def _query(batch: list[str], original: bool) -> tuple[list[str], bool, list[dict] | None, Exception | None]:
        async with sem:
            try:
                # record=False: the per-batch queries differ only in their catalog
                # list, so ONE representative is recorded above for "View SQL".
                rows = await execute_sql(column_coverage_batch_sql(batch, system_ok, schemas), record=False,
                                         poll_seconds=_METADATA_QUERY_POLL_SECONDS, wait_seconds=0)
                return batch, original, rows, None
            except Exception as e:
                return batch, original, None, e

    # Work queue: every query is its own task, so the no-progress stop counts each
    # query (a retry never hides inside a long-running task). A batch is one
    # UNION/IN query, so one bad catalog (an unreachable federated remote, a
    # catalog too big for the time budget) fails all of it. A failed batch is split
    # in half and both halves re-queued, down to single catalogs (a timed-out batch
    # goes straight to single catalogs); only a catalog that fails alone is skipped.
    pending: set[asyncio.Future] = {asyncio.ensure_future(_query(b, True)) for b in batches}
    _emit_progress("metadata", 0, total_cats, f"Scanning column metadata across {total_cats} catalogs…")
    try:
        while pending:
            done, pending = await asyncio.wait(
                pending, timeout=_METADATA_NO_PROGRESS_TIMEOUT, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                raise TimeoutError(
                    f"metadata column scan stalled — no query completed in "
                    f"{_METADATA_NO_PROGRESS_TIMEOUT}s ({scanned}/{total_cats} catalogs scanned)"
                )
            failed: list[tuple[list[str], Exception]] = []
            for d in done:
                batch, original, rows, err = d.result()
                if len(batch) == 1:
                    canaries_in_flight.discard(batch[0])
                if err is not None:
                    (held if original and ok_cats == 0 else failed).append((batch, err))
                    continue
                scanned += len(batch)
                ok_cats += len(batch)
                settled.update(batch)
                for r in rows:
                    t, c = int(r.get("total") or 0), int(r.get("commented") or 0)
                    c_total += t
                    c_commented += c
                    cat, sch = r.get("cat"), r.get("sch")
                    if cat and sch:
                        per_schema[(cat, sch)] = (t, c)
            if ok_cats > 0 and held and not canaries_in_flight:
                # Current failures (e.g. a canary that just failed) first, so they are
                # settled before the held batches are filtered against `settled`.
                failed, held = failed + held, []
            elif ok_cats == 0 and held and not pending and not failed and not canary_used:
                canary_used = True
                flat = [c for b, _ in held for c in b]
                n = min(_METADATA_SCAN_CONCURRENCY, len(flat))
                for c in dict.fromkeys(flat[(i * len(flat)) // n] for i in range(n)):
                    canaries_in_flight.add(c)
                    pending.add(asyncio.ensure_future(_query([c], False)))
            for batch, err in failed:
                batch = [c for c in batch if c not in settled]  # e.g. a canary already read it
                if not batch:
                    continue
                why = f"{type(err).__name__}: {str(err)[:100]}"
                singles = [[c] for c in batch]
                halves = [batch[:len(batch) // 2], batch[len(batch) // 2:]]
                # A statement timeout usually means one catalog is too big for the
                # budget: halving would re-run it at every level, so go to single
                # catalogs. Other errors (incl. transport timeouts): halve.
                if len(batch) == 1:
                    parts = []
                elif isinstance(err, StatementTimeout) and len(singles) <= retry_budget:
                    parts = singles
                elif retry_budget >= 2:
                    parts = halves
                else:
                    parts = []
                if parts:
                    retry_budget -= len(parts)
                    logger.warning(f"metadata column batch failed ({len(batch)} catalogs), "
                                   f"{'retrying each alone' if parts is singles else 'splitting'}: {why}")
                    for part in parts:
                        pending.add(asyncio.ensure_future(_query(part, False)))
                else:
                    if len(batch) > 1:
                        logger.warning(f"metadata retry budget spent; skipping {len(batch)} catalogs: {why}")
                    else:
                        logger.warning(f"metadata column scan skipped catalog {batch[0]!r}: {why}")
                    skipped.extend(batch)
                    settled.update(batch)
                    scanned += len(batch)
            _emit_progress("metadata", scanned, total_cats,
                           f"Scanned {scanned}/{total_cats} catalogs · {c_commented}/{c_total} columns commented"
                           + (f" · {len(skipped)} skipped" if skipped else ""))
    finally:
        # Cancel any still-pending queries (stall / circuit paths) and await them so
        # the cancellations settle (their statements are cancelled on the warehouse).
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    # Every catalog failed (only held failures, never a success) → an access/infra
    # failure, not real 0% coverage. Raise so probe_metadata marks the pillar
    # unavailable (pre-chunking behavior).
    if ok_cats == 0:
        raise RuntimeError(f"column coverage scan failed for all {total_cats} catalogs")
    if skipped:
        logger.warning(f"metadata scan: {len(skipped)}/{total_cats} catalogs unreadable; "
                       f"coverage computed over the {ok_cats} readable")
    return c_total, c_commented, per_schema, skipped


def tagged_tables_sql(source: str, where: str) -> str:
    """Distinct tables carrying >=1 governed tag, counted by fully-qualified name (#92).

    The SQL is the shared spec query (``signal_spec.METADATA``), so the skill renders
    the identical statement.
    """
    q = METADATA_SPEC["queries_by_id"]["tagged_tables"]
    return render_sql(q["sql"], {"<TABLE_TAGS>": source}, schema_filter=where).rstrip(";")


async def probe_metadata() -> dict:
    s = await _resolve_sources()
    tbl = _src("tables", s)
    if tbl is None:
        return _empty(_no_catalogs_note("metadata assessment"))
    try:
        system_ok = bool(s.get("system_ok"))
        catalogs = s.get("catalogs") or []

        # Table comment coverage — ONE grouped scan that yields BOTH the headline
        # totals and the per-schema breakdown for the drill-down (#10), so table
        # metadata isn't scanned twice (the drill-down previously re-ran this exact
        # scan). In system mode the metastore-wide tables view also spans internal
        # catalogs (system/samples/…), but column coverage below is scoped to the
        # enumerated non-internal catalogs; exclude the same internal catalogs here
        # so both halves of the 50/50 score measure the same population. That means
        # the named internal catalogs AND any __-prefixed one (#109), the same rule as
        # the tag count below and the skill's Source resolution. (Per-catalog _src is
        # already scoped to that list.)
        if system_ok:
            tables_src, tables_where = ("system.information_schema.tables",
                                        f"WHERE {_schema_sql(s)} {_internal_catalog_filter(s)}".rstrip())
        else:
            tables_src, tables_where = tbl, f"WHERE {_schema_sql(s)}"
        trows = await execute_sql(
            "SELECT table_catalog AS cat, table_schema AS sch, COUNT(*) AS total, "
            "SUM(CASE WHEN comment IS NOT NULL AND comment <> '' THEN 1 ELSE 0 END) AS commented "
            f"FROM {tables_src} {tables_where} GROUP BY table_catalog, table_schema"
        )
        table_per_schema: dict[tuple[str, str], tuple[int, int]] = {}
        t_total = t_commented = 0
        for r in trows:
            tot, com = int(r.get("total") or 0), int(r.get("commented") or 0)
            t_total += tot
            t_commented += com
            cat, sch = r.get("cat"), r.get("sch")
            if cat and sch:
                table_per_schema[(cat, sch)] = (tot, com)

        # Heavy read — scan columns coverage in per-catalog batches so a wide
        # metastore fills in progressively instead of timing out on one big scan.
        # Fall back to a single query only when there's no catalog list to chunk by.
        col_per_schema: dict[tuple[str, str], tuple[int, int]] = {}
        skipped_cats: list[str] = []
        if catalogs:
            c_total, c_commented, col_per_schema, skipped_cats = await _column_coverage_chunked(
                catalogs, system_ok, s.get("schemas")
            )
        else:
            col = _src("columns", s)  # built lazily — only the no-catalog-list fallback needs it
            if col is not None:
                crows = await execute_sql(
                    _COV_SELECT + f"{col} WHERE {_schema_sql(s)}"
                )
                c_total = int(crows[0].get("total") or 0)
                c_commented = int(crows[0].get("commented") or 0)
            else:
                c_total = c_commented = 0

        # Catalogs whose column metadata couldn't be read are left out of BOTH halves
        # of the score (and the drill-down), so table and column coverage describe
        # the same set of catalogs.
        col_failed = len(skipped_cats)  # column-scan skips (#113's "over N of M catalogs")
        skipped_set = set(skipped_cats)
        if skipped_set:
            for (cat, sch), (tot, com) in list(table_per_schema.items()):
                if cat in skipped_set:
                    t_total -= tot
                    t_commented -= com
                    del table_per_schema[(cat, sch)]
        skipped_cats = sorted(skipped_set | set(s.get("excluded") or []))

        tagged_tables = None
        tt = _src("table_tags", s)
        if tt is not None:
            try:
                # Same population as the table-comment scan above: in system mode, drop
                # internal catalogs (system/__databricks_internal/...) so their tags
                # don't count — matches the skill's source-resolution rule.
                tag_where = _schema_sql(s, "schema_name") + " " + _internal_catalog_filter(s, "catalog_name")
                tagged_tables = await _scalar(tagged_tables_sql(tt, tag_where.strip()))
            except Exception:
                tagged_tables = None

        table_pct = _pct(t_commented, t_total)
        col_pct = _pct(c_commented, c_total)
        score = round(METADATA_TABLE_WEIGHT * table_pct + METADATA_COLUMN_WEIGHT * col_pct, 1)

        # gap_ids[i] is the spec id of gaps[i] (same order), for the impact statements.
        gaps = []
        gap_ids = []
        if table_pct < TABLE_COMMENT_TARGET_PCT:
            gaps.append(gap_text(METADATA_SPEC, "table_comments_low", table_pct=table_pct))
            gap_ids.append("table_comments_low")
        if col_pct < COLUMN_COMMENT_TARGET_PCT:
            gaps.append(gap_text(METADATA_SPEC, "column_comments_low", col_pct=col_pct))
            gap_ids.append("column_comments_low")
        if tagged_tables is not None and t_total and tagged_tables == 0:
            gaps.append(gap_text(METADATA_SPEC, "no_tagged_tables"))
            gap_ids.append("no_tagged_tables")

        label = METADATA_SPEC["signal_labels"]
        signals = [
            {"label": label["table_comments"], "value": table_pct, "unit": "%",
             "detail": f"{t_commented} of {t_total} tables"},
            {"label": label["column_comments"], "value": col_pct, "unit": "%",
             "detail": f"{c_commented} of {c_total} columns"
                       + (f" (over {len(catalogs) - col_failed} of {len(catalogs)} catalogs)" if col_failed else "")},
        ]
        if tagged_tables is not None:
            signals.append({"label": label["tagged_tables"], "value": tagged_tables,
                            "detail": "Tables with ≥1 governed tag"})
        if skipped_cats:
            names = ", ".join(skipped_cats[:10]) + (f" (+{len(skipped_cats) - 10} more)" if len(skipped_cats) > 10 else "")
            signals.append({"label": "Catalogs skipped", "value": len(skipped_cats),
                            "detail": "Metadata could not be read (no access, an unreachable federated source, "
                                      f"or a Lakebase database catalog), so coverage excludes: {names}"})

        # Per-schema drill-down (#10): comment coverage by schema, worst first, so a
        # domain lead sees which schemas drag the score down (sliceable by catalog/schema).
        # table_per_schema was populated by the grouped headline scan above (no re-scan);
        # col_per_schema comes from the column-coverage scan.
        drill_rows = []
        for (cat, sch) in sorted(set(table_per_schema) | set(col_per_schema)):
            t_tot, t_com = table_per_schema.get((cat, sch), (0, 0))
            c_tot, c_com = col_per_schema.get((cat, sch), (0, 0))
            drill_rows.append({
                "catalog": cat,
                "schema": sch,
                "table_comment_pct": _pct(t_com, t_tot),
                "column_comment_pct": _pct(c_com, c_tot),
                "tables": t_tot,
            })
        drill_rows.sort(key=lambda r: (r["table_comment_pct"] + r["column_comment_pct"]))
        drill_total = len(drill_rows)
        drill_rows = drill_rows[:_METADATA_DRILL_MAX_ROWS]

        return {
            "available": True,
            "score": score,
            "signals": signals,
            "gaps": gaps,
            "gap_ids": gap_ids,
            # #112: the same disclosure the skill gives when some batches fail, so a
            # score computed over part of the estate isn't read as complete.
            "note": (f"Column coverage over {len(catalogs) - col_failed} of {len(catalogs)} catalogs "
                     f"({col_failed} could not be read).") if col_failed else None,
            "metrics": {"table_comment_pct": table_pct, "column_comment_pct": col_pct, "tagged_tables": tagged_tables},
            "drill_down": _drill(
                "Comment coverage by schema (worst first)" if drill_total <= _METADATA_DRILL_MAX_ROWS
                else f"Comment coverage by schema (worst {_METADATA_DRILL_MAX_ROWS} of {drill_total})",
                [{"key": "catalog", "label": "Catalog"},
                 {"key": "schema", "label": "Schema"},
                 {"key": "tables", "label": "Tables"},
                 {"key": "table_comment_pct", "label": "Tables commented", "unit": "%"},
                 {"key": "column_comment_pct", "label": "Columns commented", "unit": "%"}],
                drill_rows,
                total_rows=drill_total,
            ),
        }
    except Exception as e:
        return _failed(e, "Comment coverage")


# ---------------------------------------------------------------------------
# 3. Relationships & modeling (PK/FK + gold layer)
# ---------------------------------------------------------------------------
@_with_gap_ids
async def probe_relationships() -> dict:
    s = await _resolve_sources()
    tbl = _src("tables", s)
    if tbl is None:
        return _empty(_no_catalogs_note("relationship assessment"))
    q = REL_SPEC["queries_by_id"]
    label = REL_SPEC["signal_labels"]
    try:
        constraints_available = True
        pk = fk = 0
        tc = _src("table_constraints", s)
        try:
            rows = await execute_sql(
                render_sql(q["constraints"]["sql"], {"<TABLE_CONSTRAINTS>": tc},
                           schema_filter=_schema_sql(s)).rstrip(";")
            )
            by_type = {r["constraint_type"]: int(r["n"] or 0) for r in rows}
            pk = by_type.get("PRIMARY KEY", 0)
            fk = by_type.get("FOREIGN KEY", 0)
        except Exception:
            constraints_available = False

        gold_tables = await _scalar(
            render_sql(q["gold_tables"]["sql"], {"<TABLES>": tbl}, schema_filter=_schema_sql(s)).rstrip(";")
        ) or 0

        score = 0.0
        if gold_tables and gold_tables > 0:
            score += REL_GOLD_POINTS
        if constraints_available:
            if fk > 0:
                score += REL_FK_POINTS
            if pk > 0:
                score += REL_PK_POINTS
        score = min(score, 100.0)

        gaps: list[str] = []
        gap_ids: list[str] = []
        if gold_tables == 0:
            _spec_gap(REL_SPEC, "no_gold_layer", gaps, gap_ids)
        if constraints_available and fk == 0:
            _spec_gap(REL_SPEC, "no_fk_constraints", gaps, gap_ids)

        signals = [{"label": label["gold_tables"], "value": gold_tables, "detail": "Tables in gold/mart/analytics-style schemas"}]
        if constraints_available:
            signals += [
                {"label": label["pk"], "value": pk, "detail": "Declared PK constraints"},
                {"label": label["fk"], "value": fk, "detail": "Declared FK constraints"},
            ]

        note = None if constraints_available else "Constraint metadata not available; relationship score is based on the gold layer only."

        # Per-schema drill-down (#10): gold-layer tables and declared PK/FK by
        # schema, so a domain sees where modeling (constraints/gold layer) is thin.
        gold_by: dict[tuple[str, str], int] = {}
        try:
            grows = await execute_sql(
                f"SELECT table_catalog AS cat, table_schema AS sch, COUNT(*) AS n FROM {tbl} "
                f"WHERE {_schema_sql(s)} "
                f"AND (lower(table_schema) RLIKE '{GOLD_SCHEMA_PATTERN}' "
                f"   OR lower(table_name) RLIKE '{GOLD_TABLE_PATTERN}') "
                f"{_internal_catalog_filter(s)}GROUP BY table_catalog, table_schema"
            )
            gold_by = {(r.get("cat"), r.get("sch")): int(r.get("n") or 0) for r in grows if r.get("cat") and r.get("sch")}
        except Exception as e:
            logger.info(f"relationships gold-by-schema failed: {str(e)[:80]}")
        pkfk_by: dict[tuple[str, str], dict] = {}
        if constraints_available:
            try:
                crows = await execute_sql(
                    f"SELECT table_catalog AS cat, table_schema AS sch, constraint_type AS ct, COUNT(*) AS n "
                    f"FROM {tc} WHERE {_schema_sql(s)} GROUP BY table_catalog, table_schema, constraint_type"
                )
                for r in crows:
                    cat, sch = r.get("cat"), r.get("sch")
                    if not cat or not sch:
                        continue
                    d = pkfk_by.setdefault((cat, sch), {"pk": 0, "fk": 0})
                    if r.get("ct") == "PRIMARY KEY":
                        d["pk"] = int(r.get("n") or 0)
                    elif r.get("ct") == "FOREIGN KEY":
                        d["fk"] = int(r.get("n") or 0)
            except Exception as e:
                logger.info(f"relationships pkfk-by-schema failed: {str(e)[:80]}")
        drill_cols = [{"key": "catalog", "label": "Catalog"}, {"key": "schema", "label": "Schema"},
                      {"key": "gold_tables", "label": "Gold tables"}]
        if constraints_available:
            drill_cols += [{"key": "primary_keys", "label": "PKs"}, {"key": "foreign_keys", "label": "FKs"}]
        drill_rows = []
        for (cat, sch) in sorted(set(gold_by) | set(pkfk_by)):
            row = {"catalog": cat, "schema": sch, "gold_tables": gold_by.get((cat, sch), 0)}
            if constraints_available:
                row["primary_keys"] = pkfk_by.get((cat, sch), {}).get("pk", 0)
                row["foreign_keys"] = pkfk_by.get((cat, sch), {}).get("fk", 0)
            drill_rows.append(row)
        drill_rows.sort(key=lambda r: (r["gold_tables"], r.get("foreign_keys", 0)))

        return {"available": True, "score": score, "signals": signals, "gaps": gaps, "gap_ids": gap_ids, "note": note,
                "metrics": {"primary_keys": pk, "foreign_keys": fk, "gold_tables": gold_tables, "constraints_available": constraints_available},
                "drill_down": _drill("Modeling by schema (thinnest first)", drill_cols, drill_rows)}
    except Exception as e:
        return _failed(e, "Relationships and modeling")


# ---------------------------------------------------------------------------
# 4. Semantic layer (metric views)
# ---------------------------------------------------------------------------
@_with_gap_ids
async def probe_metrics() -> dict:
    s = await _resolve_sources()
    tbl = _src("tables", s)
    if tbl is None:
        return _empty(_no_catalogs_note("semantic-layer assessment"))
    try:
        q = METRICS["queries_by_id"]
        label = METRICS["signal_labels"]

        def _metric_sql(query_id: str, type_value: str) -> str:
            # The spec's METRIC_VIEW literal, spelled the way this runtime reports it.
            return render_sql(q[query_id]["sql"], {"<TABLES>": tbl, "'METRIC_VIEW'": f"'{type_value}'"},
                              schema_filter=_schema_sql(s)).rstrip(";")

        metric_views = None
        type_value = None
        for tv in METRIC_VIEW_TYPES:
            try:
                metric_views = await _scalar(_metric_sql("metric_views", tv))
                if metric_views is not None:
                    type_value = tv
                    break
            except Exception:
                continue
        if metric_views is None:
            return _empty("Metric view metadata not available on this metastore version; use the self-assessment for the semantic layer.",
                          reason="not_enabled")

        # If no metric views, return early with the "absent" result
        if metric_views == 0:
            gaps: list[str] = []
            gap_ids: list[str] = []
            _spec_gap(METRICS, "no_metric_views", gaps, gap_ids)
            return {
                "available": True,
                "score": 0.0,
                "signals": [{"label": label["metric_views"], "value": 0, "detail": "UC metric views"}],
                "gaps": gaps,
                "gap_ids": gap_ids,
                "note": None,
                "metrics": {"metric_views": 0},
            }

        # Query for commented metric views (defensive: treat failure as 0)
        commented = 0
        try:
            commented = await _scalar(_metric_sql("commented", type_value)) or 0
        except Exception:
            commented = 0

        # Score formula (see the spec's `score`): base + coverage ramp + quality.
        score = float(METRIC_BASE_POINTS)
        score += METRIC_RAMP_POINTS * min(metric_views, METRIC_RAMP_VIEWS) / float(METRIC_RAMP_VIEWS)
        score += METRIC_QUALITY_POINTS * (float(commented) / float(metric_views)) if metric_views > 0 else 0.0
        score = round(min(score, 100.0), 1)

        # Build signals
        signals = [
            {"label": label["metric_views"], "value": metric_views, "detail": "UC metric views"}
        ]
        if metric_views > 0:
            signals.append({
                "label": label["commented"],
                "value": _pct(commented, metric_views),
                "unit": "%",
                "detail": "Share of metric views with a description",
            })

        # Build gaps
        gaps: list[str] = []
        gap_ids: list[str] = []
        if metric_views < METRIC_FEW_VIEWS:
            _spec_gap(METRICS, "few_metric_views", gaps, gap_ids)
        if metric_views > 0:
            uncommented = metric_views - commented
            if uncommented > 0:
                _spec_gap(METRICS, "metric_views_uncommented", gaps, gap_ids, uncommented=uncommented)

        # Per-schema drill-down (#10): where the metric views live (and how many
        # are described), so teams see which schemas still lack a semantic layer.
        drill_rows = []
        try:
            mrows = await execute_sql(
                f"SELECT table_catalog AS cat, table_schema AS sch, COUNT(*) AS n, "
                f"SUM(CASE WHEN comment IS NOT NULL AND trim(comment) <> '' THEN 1 ELSE 0 END) AS commented "
                f"FROM {tbl} WHERE table_type = '{type_value}' {_schema_and(s)}{_internal_catalog_filter(s)}"
                f"GROUP BY table_catalog, table_schema ORDER BY n DESC LIMIT 500"
            )
            drill_rows = [
                {"catalog": r.get("cat"), "schema": r.get("sch"), "metric_views": int(r.get("n") or 0),
                 "commented": int(r.get("commented") or 0)}
                for r in mrows if r.get("cat")
            ]
        except Exception as e:
            logger.info(f"metrics per-schema drill-down failed: {str(e)[:80]}")

        return {
            "available": True,
            "score": score,
            "signals": signals,
            "gaps": gaps,
            "gap_ids": gap_ids,
            "note": None,
            "metrics": {"metric_views": metric_views, "metric_views_commented": commented},
            "drill_down": _drill(
                "Metric views by schema",
                [{"key": "catalog", "label": "Catalog"},
                 {"key": "schema", "label": "Schema"},
                 {"key": "metric_views", "label": "Metric views"},
                 {"key": "commented", "label": "Commented"}],
                drill_rows,
            ),
        }
    except Exception as e:
        return _failed(e, "The metric-view footprint")


# ---------------------------------------------------------------------------
# 5. Genie Agents (graded usage from the audit log + best-effort curation via REST)
# ---------------------------------------------------------------------------
_MAX_INSPECT = 30  # cap how many spaces we deep-inspect to bound latency
_GENIE_AUDIT_LOOKBACK_DAYS = GENIE_AUDIT_LOOKBACK_DAYS  # the spec's window (its SQL and gap text use it too)
# Poll budget for the audit-log scans (system.access.audit over the lookback). On a
# busy, large account the 90-day scan can run well past the 180 s default, more so
# while other pillars load the same warehouse. Background runs (#128) aren't bound
# by the Apps proxy's stream cut, so give these queries room; the run cap still applies.
_GENIE_AUDIT_POLL_SECONDS = 600

# Lowercased, quoted SQL IN-lists for the per-agent drill-down (the spec's
# audit_counts query builds its own; these keep the drill-down in lockstep).
_CONV_IN = ", ".join("'" + a.lower().replace("'", "''") + "'" for a in GENIE_CONVERSATION_ACTIONS)
_DASH_IN = ", ".join("'" + a.lower().replace("'", "''") + "'" for a in GENIE_DASHBOARD_ACTIONS)


def _parse_date(value):
    """A ``datetime.date`` from a date/datetime or an ISO ``YYYY-MM-DD`` string; None if unparseable."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _recency_days(value):
    """Whole days since ``value`` (clamped at 0), or None when there's no date."""
    d = _parse_date(value)
    return max(0, (datetime.date.today() - d).days) if d else None


def _count(serialized: dict, *path: str) -> int:
    """Length of the list at a nested path in a serialized space (0 if missing)."""
    node = serialized
    try:
        for key in path:
            node = node[key]
        return len(node) if isinstance(node, list) else 0
    except (KeyError, TypeError):
        return 0


async def _inspect_space(host: str, headers: dict, sid: str, title: str) -> tuple[str, dict | None]:
    """Fetch one serialized space and count each curation dimension.

    Returns (status, data):
      ("ok", {...counts})   — serialized space read and parsed
      ("forbidden", None)   — the SP lacks CAN_EDIT (serialized read is gated behind edit)
      ("error", None)       — transient/other failure
    """
    try:
        async with aiohttp.ClientSession(timeout=_PROBE_TIMEOUT, headers=with_ua()) as session:
            async with session.get(
                f"{host}/api/2.0/genie/spaces/{sid}",
                headers=headers, params={"include_serialized_space": "true"},
            ) as resp:
                if resp.status in (401, 403):
                    return "forbidden", None
                if resp.status != 200:
                    return "error", None
                data = await resp.json()
    except Exception:
        return "error", None

    ss = data.get("serialized_space")
    if isinstance(ss, str):
        try:
            ss = json.loads(ss)
        except Exception:
            ss = None
    # No serialized payload returned (e.g. insufficient access) — treat as forbidden.
    if not isinstance(ss, dict):
        return "forbidden", None

    return "ok", {
        "title": data.get("title") or title or sid,
        "instructions": _count(ss, "instructions", "text_instructions"),
        "sample_questions": _count(ss, "config", "sample_questions"),
        "example_sqls": _count(ss, "instructions", "example_question_sqls"),
        "functions": _count(ss, "instructions", "sql_functions"),
        "benchmarks": _count(ss, "benchmarks", "questions"),
        "tables": _count(ss, "data_sources", "tables"),
    }


# Reading a space's curation (serialized_space) requires CAN_EDIT; listing/asking
# only needs CAN_RUN. So the deep per-space breakdown is best-effort: we assess
# whatever the app can read and never report a space we can't read as "uncurated".
_EDIT_HINT = ("Reading an agent's curation detail requires CAN_EDIT on that agent; the app only needs "
              "CAN_RUN to list and count agents. Grant the app service principal CAN_EDIT on the "
              "agents you want curation-assessed (or run this assessment as a user who can edit them).")


async def _genie_audit_counts() -> dict:
    """Graded Genie usage for STANDALONE agents from the audit system table.

    system.access.audit records Genie activity under service_name='aibiGenie'
    with the space id in request_params.space_id. A space is counted when it has
    an aibiGenie event in the lookback window and was never trashed (a `trashSpace`
    action; there is no deleteSpace — https://docs.databricks.com/aws/en/ai-bi/admin/audit),
    and is classified DASHBOARD-EMBEDDED (counted separately, not scored) when its
    space_id appears in the response.result of a getDashboardGenie/updateDashboardGenie
    row; otherwise STANDALONE.

    Real usage is measured from CONVERSATION actions only (raw events overstate via
    polling, #24): questions_30d, distinct_users_30d and active_30d over 30 days.
    The single bounded scan has its own poll budget (_GENIE_AUDIT_POLL_SECONDS); a
    background run isn't bound by the Apps proxy's stream cut (#128).

    Returns the aggregate usage fields (each count None if the audit table isn't
    readable), plus last_active_date and the derived recency_days.
    """
    try:
        workspace_filter, parameters = workspace_predicate()
        rows = await execute_sql(
            render_sql(GENIE_SPEC["queries_by_id"]["audit_counts"]["sql"],
                       workspace_filter=workspace_filter).rstrip(";"),
            parameters=parameters or None,
            poll_seconds=_GENIE_AUDIT_POLL_SECONDS,
        )
        row = rows[0] if rows else {}
        last_active = row.get("last_active_date")
        return {
            "total": int(row.get("total") or 0),
            "active_30d": int(row.get("active_30d") or 0),
            "questions_30d": int(row.get("questions_30d") or 0),
            "distinct_users_30d": int(row.get("distinct_users_30d") or 0),
            "dashboard_embedded": int(row.get("dashboard_embedded") or 0),
            "last_active_date": _parse_date(last_active).isoformat() if _parse_date(last_active) else None,
            "recency_days": _recency_days(last_active),
        }
    except StatementTimeout:
        logger.warning("Genie audit scan timed out after %ss", _GENIE_AUDIT_POLL_SECONDS)
        return {"total": None, "active_30d": None, "timed_out": True}
    except Exception:
        return {"total": None, "active_30d": None}


async def _genie_audit_rows() -> list[dict]:
    """Best-effort per-agent drill-down: the top Genie Agents (by audit events) in
    scope, each with its type (standalone vs dashboard-embedded), 30-day conversation
    questions / distinct users / active flag, and (when >1 workspace is selected)
    which workspace they live in. Bounded so it can't blow the streaming timeout."""
    try:
        wsf, wparams = workspace_predicate()  # applied inside the inner audit scans (unqualified column)
        multi = is_multi_workspace()
        ws_select = ", w.workspace_name AS workspace, a.workspace_id AS workspace_id" if multi else ""
        ws_join = (" LEFT JOIN system.access.workspaces_latest w "
                   "ON CAST(a.workspace_id AS STRING) = CAST(w.workspace_id AS STRING)") if multi else ""
        conv_30d = (f"lower(action_name) IN ({_CONV_IN}) "
                    f"AND event_date >= current_date() - INTERVAL {GENIE_ACTIVE_WINDOW_DAYS} DAYS")
        rows = await execute_sql(
            # names: latest display_name per space over the wider name window; the
            # activity subquery (a) drives event volume / usage over the windows;
            # dashboard_refs drives the standalone-vs-dashboard classification.
            "WITH names AS ( "
            "  SELECT request_params.space_id AS space_id, "
            "         max_by(request_params.display_name, event_time) AS space_name "
            "  FROM system.access.audit "
            "  WHERE service_name = 'aibiGenie' AND request_params.space_id IS NOT NULL "
            "    AND request_params.display_name IS NOT NULL "
            f"    AND event_date >= current_date() - INTERVAL {GENIE_NAME_LOOKBACK_DAYS} DAYS "
            f"    {wsf}"
            "  GROUP BY request_params.space_id "
            "), "
            "dashboard_refs AS ( "
            "  SELECT response.result AS result FROM system.access.audit "
            f"  WHERE lower(action_name) IN ({_DASH_IN}) AND response.result IS NOT NULL "
            f"    AND event_date >= current_date() - INTERVAL {_GENIE_AUDIT_LOOKBACK_DAYS} DAYS "
            f"    {wsf}"
            ") "
            "SELECT COALESCE(nm.space_name, a.space_id) AS agent, a.space_id AS space_id, "
            "       a.events AS events, a.questions AS questions, a.users AS users, a.active_30d AS active_30d, "
            "       CASE WHEN EXISTS (SELECT 1 FROM dashboard_refs d "
            "            WHERE d.result LIKE '%' || a.space_id || '%') THEN 1 ELSE 0 END AS dashboard_embedded"
            + ws_select + " "
            "FROM ( "
            "  SELECT request_params.space_id AS space_id, "
            + ("any_value(workspace_id) AS workspace_id, " if multi else "") +
            "         COUNT(*) AS events, "
            f"         SUM(CASE WHEN {conv_30d} THEN 1 ELSE 0 END) AS questions, "
            f"         COUNT(DISTINCT CASE WHEN {conv_30d} THEN user_identity.email END) AS users, "
            "         MAX(CASE WHEN lower(action_name) = 'trashspace' THEN 1 ELSE 0 END) AS trashed, "
            f"         MAX(CASE WHEN {conv_30d} THEN 1 ELSE 0 END) AS active_30d "
            "  FROM system.access.audit "
            "  WHERE service_name = 'aibiGenie' AND request_params.space_id IS NOT NULL "
            "    AND request_params.space_id <> 'new' "
            f"    AND event_date >= current_date() - INTERVAL {_GENIE_AUDIT_LOOKBACK_DAYS} DAYS "
            f"    {wsf}"
            "  GROUP BY request_params.space_id "
            ") a LEFT JOIN names nm ON a.space_id = nm.space_id" + ws_join + " "
            "WHERE a.trashed = 0 ORDER BY a.events DESC LIMIT 200",
            parameters=wparams or None,
            poll_seconds=_GENIE_AUDIT_POLL_SECONDS,
        )
        out = []
        for r in rows:
            dash = int(r.get("dashboard_embedded") or 0)
            row = {"agent": r.get("agent"), "space_id": r.get("space_id"),
                   "type": "Dashboard-embedded" if dash else "Standalone",
                   "questions": int(r.get("questions") or 0),
                   "users": int(r.get("users") or 0),
                   "events": int(r.get("events") or 0),
                   "active_30d": "Yes" if int(r.get("active_30d") or 0) else "No"}
            if multi:
                row["workspace"] = r.get("workspace") or r.get("workspace_id")
            out.append(row)
        return out
    except Exception as e:
        logger.info(f"genie per-agent drill-down failed: {str(e)[:80]}")
        return []


def _curation_not_readable(title: str) -> dict:
    """A clear 'not readable / not assessed' curation row — null counts, NEVER 0 —
    for a standalone agent whose serialized space the app can't fetch (#24)."""
    return {"title": title, "readable": False, "type": "standalone",
            "instructions": None, "sample_questions": None, "example_sqls": None,
            "functions": None, "benchmarks": None, "tables": None}


async def _genie_curation(standalone_rows: list[dict]) -> list[dict]:
    """Best-effort per-agent curation counts for STANDALONE agents, read from each
    agent's serialized space (Databricks gates that read behind CAN_EDIT, and it only
    works for agents co-located in this workspace). This is curation QUALITY context
    only — it NEVER affects the numeric score. Where the serialized space can't be
    fetched (other workspace / no CAN_EDIT / 403 / not-found) the agent degrades to a
    clear 'not readable' row with null counts, never 0/absent, so a partial read
    ('where visible') isn't mistaken for poor curation. Bounded by _MAX_INSPECT."""
    if not standalone_rows:
        return []
    try:
        host = get_workspace_host()
        headers = get_auth_headers(force_sp=True)  # Genie REST isn't covered by the OBO sql scope
    except Exception:
        host, headers = "", None
    out: list[dict] = []
    for r in standalone_rows[:_MAX_INSPECT]:
        sid = r.get("space_id")
        title = r.get("agent") or sid or "(unknown agent)"
        if not host or not headers or not sid:
            out.append(_curation_not_readable(title))
            continue
        try:
            status, data = await _inspect_space(host, headers, sid, title)
        except Exception:
            status, data = "error", None
        if status == "ok" and data:
            out.append({
                "title": data["title"], "readable": True, "type": "standalone",
                "instructions": data["instructions"],
                "sample_questions": data["sample_questions"],
                "example_sqls": data["example_sqls"],
                "functions": data["functions"],
                "benchmarks": data["benchmarks"],
                "tables": data["tables"],
            })
        else:
            out.append(_curation_not_readable(title))
    return out


def _genie_audit_signals(audit: dict) -> list:
    sig = []
    label = GENIE_SPEC["signal_labels"]
    if audit.get("total") is not None:
        sig.append({"label": label["total"], "value": audit["total"],
                    "detail": f"Standalone Genie Agents in the audit log ({_GENIE_AUDIT_LOOKBACK_DAYS}d); "
                              "dashboard-embedded agents are excluded"})
    if audit.get("active_30d") is not None:
        sig.append({"label": label["active_30d"], "value": audit["active_30d"],
                    "detail": f"Standalone agents with a conversation in the last {GENIE_ACTIVE_WINDOW_DAYS} days"})
    if audit.get("distinct_users_30d") is not None:
        sig.append({"label": label["distinct_users_30d"], "value": audit["distinct_users_30d"],
                    "detail": f"Distinct users who asked a question across standalone agents "
                              f"({GENIE_ACTIVE_WINDOW_DAYS}d)"})
    if audit.get("questions_30d") is not None:
        sig.append({"label": label["questions_30d"], "value": audit["questions_30d"],
                    "detail": f"Conversation questions across standalone agents ({GENIE_ACTIVE_WINDOW_DAYS}d)"})
    if audit.get("dashboard_embedded"):
        sig.append({"label": label["dashboard_embedded"], "value": audit["dashboard_embedded"],
                    "detail": "AI/BI Dashboard agents (space_id seen in a dashboard-genie response) — "
                              "counted separately, not scored"})
    return sig


@_with_gap_ids
async def probe_genie_agents() -> dict:
    """Score STANDALONE Genie Agents from the audit log (system.access.audit / aibiGenie),
    graded on real conversation usage (#24).

    We deliberately do not use the Genie REST API "spaces visible to the app" for the
    count: it is permission- and scope-gated (on-behalf-of-user tokens lack the genie
    scope) and reflects only what one principal can see. The audit log gives a workspace-
    wide view via the viewer's own system-table access. Agent type (standalone vs AI/BI
    dashboard-embedded) is a best-effort audit heuristic, and per-agent curation is a
    best-effort REST read layered on top — neither the type split's dashboard population
    nor the curation counts change the numeric score.
    """
    audit = await _genie_audit_counts()
    total, active = audit.get("total"), audit.get("active_30d")
    if total is None and active is None:
        if audit.get("timed_out"):
            # Not a permission problem: don't send the user to fix grants they have.
            return _empty(f"Genie usage couldn't be read in time — the {_GENIE_AUDIT_LOOKBACK_DAYS}-day audit-log "
                          "scan timed out. Run the assessment again, or use a larger SQL warehouse.",
                          reason="scan_failed")
        return _empty("Genie usage can't be read — the assessing identity needs SELECT on system.access.audit.",
                      reason="insufficient_permission")
    total, active = total or 0, active or 0
    raw = await _genie_audit_rows()
    standalone_rows = [r for r in raw if r.get("type") == "Standalone"]
    drill_cols = [{"key": "agent", "label": "Genie Agent"},
                  {"key": "space_id", "label": "Space id"},
                  {"key": "type", "label": "Type"},
                  {"key": "questions", "label": "Questions 30d"},
                  {"key": "users", "label": "Users 30d"},
                  {"key": "active_30d", "label": "Active 30d"}]
    if is_multi_workspace():
        drill_cols.insert(3, {"key": "workspace", "label": "Workspace"})

    score = genie_usage_score(total, active, audit.get("distinct_users_30d"),
                              audit.get("questions_30d"), audit.get("recency_days"))

    gaps: list[str] = []
    gap_ids: list[str] = []
    if total == 0:
        _spec_gap(GENIE_SPEC, "no_genie_agents", gaps, gap_ids)
    elif active == 0:
        _spec_gap(GENIE_SPEC, "no_active_genie_agents", gaps, gap_ids, total=total)

    spaces = await _genie_curation(standalone_rows)

    return {
        "available": True,
        "score": round(score, 1),
        "signals": _genie_audit_signals(audit),
        "gaps": gaps,
        "gap_ids": gap_ids,
        "note": (f"Scored from standalone Genie Agents in the in-scope workspaces' system.access.audit "
                 f"events over the last {_GENIE_AUDIT_LOOKBACK_DAYS} days (aibiGenie), graded on real "
                 "conversation usage. Agent type (standalone vs AI/BI dashboard-embedded) is a best-effort "
                 "audit heuristic. Per-agent curation (instructions, example SQL, benchmarks) is read "
                 "where visible — it needs CAN_EDIT and only works for agents in this workspace, so it is "
                 "labeled 'where visible' and never affects the score."),
        "metrics": {"genie_agents": total, "active_30d": active,
                    "distinct_users_30d": audit.get("distinct_users_30d"),
                    "questions_30d": audit.get("questions_30d"),
                    "dashboard_embedded": audit.get("dashboard_embedded"),
                    "genie_audit": audit, "spaces": spaces},
        "drill_down": _drill("Genie Agents by audit activity", drill_cols, raw),
    }


# ---------------------------------------------------------------------------
# 6. Domains & stewardship (Discover Domains API + governed tags)
# ---------------------------------------------------------------------------
# Discover domains (UC semantics) are governed tags keyed by the domain name itself: a top-level
# domain `Finance`, a subdomain `Finance/Tax` ({parentTag}/{subdomain}), usually with an empty value.
# Tagging an asset with a subdomain does not also tag it with the parent.
# Docs: https://docs.databricks.com/aws/en/uc-semantics/domains
# API:  https://docs.databricks.com/api/domains/v1/list-domains (GET, needs MANAGE DISCOVER)
_DOMAINS_API_PATHS = ("/api/2.0/domains", "/api/discover/v1/domains")
_DOMAINS_MAX_PAGES = 50
_STEWARD_TAG_KEYS = ("owner", "data_owner", "steward", "data_steward")
# Certification is the governed tag `system.certification_status` with value
# `certified` (or `deprecated`). Match both the namespaced and bare key forms.
# Docs: https://docs.databricks.com/aws/en/data-governance/unity-catalog/certify-deprecate-data
_CERT_TAG_KEYS = ("system.certification_status", "certification_status")


async def _list_domains(host: str, headers: dict) -> tuple[str, list]:
    """List Discover domains and subdomains as one identity, following next_page_token.

    Returns ("ok", domains), ("forbidden", []) on 401/403, else ("unavailable", [])."""
    status = "unavailable"
    for path in _DOMAINS_API_PATHS:
        if status == "forbidden":  # the same identity gets the same answer on the other path
            break
        domains, token = [], None
        try:
            async with aiohttp.ClientSession(timeout=_PROBE_TIMEOUT, headers=with_ua()) as session:
                for _ in range(_DOMAINS_MAX_PAGES):
                    params = {"page_size": "100", **({"page_token": token} if token else {})}
                    async with session.get(f"{host}{path}", headers=headers, params=params) as resp:
                        if resp.status in (401, 403):
                            status = "forbidden"
                            break
                        if resp.status != 200:
                            break
                        data = await resp.json()
                    domains += data.get("domains") or []
                    token = data.get("next_page_token")
                    if not token:
                        return "ok", domains
        except Exception:
            continue
    return status, []


async def _native_domains() -> dict:
    """List Discover domains and subdomains via the Domains API.

    Tries the viewer first. The forwarded viewer token carries only the `sql` user scope, so the
    Domains REST API refuses it even for a curator; on 401/403 the read is retried as the app SP
    (like the Genie REST reads). Returns {"status": "ok", "domains": [...]} on success,
    {"status": "forbidden"} when every identity got 401/403 (missing MANAGE DISCOVER), else
    {"status": "unavailable"}.
    """
    host = get_workspace_host()
    headers = get_auth_headers()
    if not host or not headers:
        return {"status": "unavailable"}
    status, domains = await _list_domains(host, headers)
    if status == "ok":
        # REST reads bypass execute_sql, so record the identity via the shared helper.
        record_rest_identity()
        return {"status": "ok", "domains": domains}
    if status == "forbidden" and get_user_token():
        sp_headers = get_auth_headers(force_sp=True)
        if sp_headers and sp_headers != headers:
            status, domains = await _list_domains(host, sp_headers)
            if status == "ok":
                record_identity("sp_fallback")
                return {"status": "ok", "domains": domains}
            logger.info(f"Domains API refused the viewer token; as the app SP: {status}")
    return {"status": status}


def _domain_tag_sql(domain_keys: list[str]) -> tuple[str, str]:
    """(predicate, label) over a *_tags view: which tag rows assign an asset to a published Discover
    domain, and the top-level domain each rolls up to (`Parent/Sub` → `Parent`)."""
    if not domain_keys:
        return "FALSE", "split(tag_name, '/')[0]"
    keys = ", ".join(quote_literal(k.lower()) for k in domain_keys)
    return f"lower(tag_name) IN ({keys})", "split(tag_name, '/')[0]"


def _domain_score(domains: int, stewarded: int = 0) -> float:
    score = (40 + 40 * min(domains, 5) / 5) if domains > 0 else 0.0
    return min(100.0, score + (20 if stewarded > 0 else 0))


async def _first_scalar(queries: list[str]) -> int:
    """The first query that runs; the next ones are simpler fallbacks (e.g. without catalog tags)."""
    for i, q in enumerate(queries):
        try:
            return int(await _scalar(q) or 0)
        except Exception as e:
            logger.info(f"domains query {i + 1}/{len(queries)} failed: {str(e)[:80]}")
    return 0


async def probe_domains() -> dict:
    """Domains & stewardship from Discover domains (Domains API) and governed tags.

    Domains are account-level and catalog-agnostic: the domain count is the account's published
    top-level domains, the same in every run, scoped or not. Coverage is per scope: which of those
    domains the assessed catalogs, schemas and tables are assigned to.

    Without the Domains API (no MANAGE DISCOVER, or not enabled) the pillar is not measured and is
    left out of the overall score. Domains are not inferred from tags: on a real account, tags alone
    picked the right top-level domains only about half the time.
    """
    api = await _native_domains()
    if api["status"] != "ok":
        forbidden = api["status"] == "forbidden"
        res = _empty(
            ("Discover domains need MANAGE DISCOVER on the app's service principal (an account admin grants it: "
             "Discover → ⋮ → Manage Domains → Permissions). " if forbidden else
             "The Discover Domains API didn't answer in this workspace. ")
            + "Domains aren't guessed from governed tags, because tags alone can't tell domains apart from "
              "other tags. Use the self-assessment.",
            reason="insufficient_permission" if forbidden else "not_enabled")
        res["score_excluded"] = True
        # Short, user-facing reason shown on the pillar card and under the overall score.
        res["score_excluded_reason"] = (
            "The app's service principal needs MANAGE DISCOVER to read Discover domains." if forbidden else
            "The Discover Domains API didn't answer, so domains couldn't be read.")
        res["metrics"] = {"domains_api": api["status"]}
        return res

    # Only published domains count: drafts are visible to curators only and are still being built
    # (`effective_draft` also covers a subdomain under a draft parent). Their tags don't count either.
    listed = api["domains"]
    published = [d for d in listed if not d.get("effective_draft")]
    draft_domains = len(listed) - len(published)
    top_level = [d for d in published if not d.get("parent_domain_id")]
    n_domains, subdomains = len(top_level), len(published) - len(top_level)
    domain_pred, domain_label = _domain_tag_sql([d["tag_key"] for d in published if d.get("tag_key")])
    domains_signal = {"label": "Domains (Discover)", "value": n_domains,
                      "detail": f"Published top-level domains in the account ({subdomains} subdomains; "
                                f"{draft_domains} drafts not counted)"}
    api_metrics = {"distinct_domains": n_domains, "subdomains": subdomains, "draft_domains": draft_domains,
                   "domains_api": "ok", "source": "native_api"}
    no_domains_gap = ("No published domains in Discover. Create business-aligned domains and subdomains, publish "
                      "them, and assign catalogs, schemas or tables to them.")

    s = await _resolve_sources()
    tt = _src("table_tags", s)
    st = _src("schema_tags", s)
    ct = _src("catalog_tags", s)
    if tt is None:  # domains are readable, asset coverage is not
        return {"available": True, "score": _domain_score(n_domains), "signals": [domains_signal],
                "gaps": [] if n_domains else [no_domains_gap],
                "note": "Published domains from the Discover Domains API (drafts excluded). No readable catalogs, "
                        "so domain coverage and stewardship weren't measured.",
                "metrics": api_metrics}
    try:
        steward_keys = ", ".join(quote_literal(k) for k in _STEWARD_TAG_KEYS)
        cert_keys = ", ".join(quote_literal(k) for k in _CERT_TAG_KEYS)
        sch = _schema_and(s, "schema_name")

        # Domains used in scope: published domains assigned to an assessed catalog, schema or table.
        def used_sql(views):
            parts = [f"SELECT {domain_label} AS domain FROM {v} WHERE {domain_pred} {f if f else ''}"
                     for v, f in views]
            return (f"SELECT COUNT(DISTINCT lower(domain)) AS used_domains, COUNT(*) AS assignments "
                    f"FROM ({' UNION ALL '.join(parts)})")
        # tt, st and ct are all set here (_src returns None for all of them only with no catalogs).
        # catalog_tags has no schema column; in per-catalog mode keep it to the assessed catalogs.
        cat_scope = ("" if s.get("system_ok") or not s.get("catalogs") else
                     "AND catalog_name IN (" + ", ".join(quote_literal(c) for c in s["catalogs"]) + ")")
        level_views = [(tt, sch), (st, sch)]
        try:
            rows = await execute_sql(used_sql(level_views + [(ct, cat_scope)]))
        except StatementTimeout:
            raise
        except Exception as e:  # catalog_tags unreadable: count schema and table assignments
            logger.info(f"domains: catalog tags unavailable: {str(e)[:80]}")
            rows = await execute_sql(used_sql(level_views))
        used_domains = int(rows[0].get("used_domains") or 0)
        assignments = int(rows[0].get("assignments") or 0)

        stewarded = 0
        try:
            stewarded = int(await _scalar(
                f"SELECT COUNT(*) FROM {tt} WHERE lower(tag_name) IN ({steward_keys}) {sch}"
            ) or 0)
        except Exception:
            stewarded = 0

        # Certified assets: distinct tables whose system.certification_status
        # governed tag is 'certified' (excludes 'deprecated').
        certified = 0
        try:
            certified = int(await _scalar(
                f"SELECT COUNT(DISTINCT concat_ws('.', catalog_name, schema_name, table_name)) "
                f"FROM {tt} WHERE lower(tag_name) IN ({cert_keys}) AND lower(tag_value) = 'certified' {sch}"
            ) or 0)
        except Exception:
            certified = 0

        # Coverage over eligible assets (tables): how many carry ANY UC governed tag, and how many are
        # in a domain: the table, its schema or its catalog is assigned to a published domain.
        tbl = _src("tables", s)
        total_tables = governed_tagged = 0
        try:
            total_tables = int(await _scalar(f"SELECT COUNT(*) FROM {tbl} WHERE {_schema_sql(s)}") or 0)
        except Exception:
            total_tables = 0
        try:
            governed_tagged = int(await _scalar(
                f"SELECT COUNT(DISTINCT concat_ws('.', catalog_name, schema_name, table_name)) "
                f"FROM {tt} WHERE {_schema_sql(s, 'schema_name')}"
            ) or 0)
        except Exception:
            governed_tagged = 0
        in_table = f"(table_catalog, table_schema, table_name) IN (SELECT catalog_name, schema_name, table_name FROM {tt} WHERE {domain_pred})"
        in_schema = f"(table_catalog, table_schema) IN (SELECT catalog_name, schema_name FROM {st} WHERE {domain_pred})"
        in_catalog = f"table_catalog IN (SELECT catalog_name FROM {ct} WHERE {domain_pred})"
        # With catalog tags, then without them, then tables tagged directly.
        domain_tagged = await _first_scalar(
            [f"SELECT COUNT(*) FROM {tbl} WHERE {_schema_sql(s)} AND ({in_table} OR {in_schema} OR {in_catalog})",
             f"SELECT COUNT(*) FROM {tbl} WHERE {_schema_sql(s)} AND ({in_table} OR {in_schema})",
             f"SELECT COUNT(DISTINCT concat_ws('.', catalog_name, schema_name, table_name)) "
             f"FROM {tt} WHERE {domain_pred} {sch}"]) if published else 0
        pct_tagged = _pct(governed_tagged, total_tables)
        pct_in_domain = _pct(domain_tagged, total_tables)

        # Certification of the *most-used* assets: of the top-N most-accessed
        # tables (ranked from system.access.table_lineage), how many are
        # certified? Certifying high-traffic tables is where certification most
        # improves Genie/ontology accuracy. table_lineage is a system table
        # (SP-read); the certification join is best-effort.
        top_accessed = top_certified = None
        top_accessed_list = []
        try:
            lineage_wsf, lineage_params = workspace_predicate()
            lineage_scope = ""
            if s.get("schemas") and s.get("catalogs"):
                lineage_scope = (
                    f"AND source_table_catalog = {quote_literal(s['catalogs'][0])} "
                    f"AND {schema_predicate(s['schemas'], 'source_table_schema')} "
                )
            rows = await execute_sql(
                "WITH top AS ("
                "  SELECT source_table_full_name AS name, COUNT(DISTINCT created_by) AS n "
                "  FROM system.access.table_lineage "
                "  WHERE source_table_full_name IS NOT NULL "
                "    AND source_table_catalog NOT IN ('system','__databricks_internal','samples') "
                f"    AND {schema_predicate(s.get('schemas'), 'source_table_schema')} "
                "    AND event_date >= current_date() - INTERVAL 90 DAYS "
                f"    {lineage_scope}{lineage_wsf}"
                "  GROUP BY source_table_full_name ORDER BY n DESC LIMIT 10 "
                "), cert AS ("
                "  SELECT concat_ws('.', catalog_name, schema_name, table_name) AS name "
                "  FROM system.information_schema.table_tags "
                "  WHERE lower(tag_name) IN ('system.certification_status','certification_status') "
                "        AND lower(tag_value) = 'certified' "
                ") SELECT t.name AS name, t.n AS accesses, "
                "         CASE WHEN c.name IS NOT NULL THEN 1 ELSE 0 END AS certified "
                "FROM top t LEFT JOIN cert c ON t.name = c.name ORDER BY t.n DESC",
                parameters=lineage_params or None,
            )
            top_accessed_list = [
                {"name": r.get("name"), "accesses": int(r.get("accesses") or 0),
                 "certified": bool(int(r.get("certified") or 0))}
                for r in rows if r.get("name")
            ]
            top_accessed = len(top_accessed_list)
            top_certified = sum(1 for r in top_accessed_list if r["certified"])
        except Exception as e:
            logger.info(f"top-accessed certification signal unavailable: {str(e)[:80]}")
            top_accessed = top_certified = None
            top_accessed_list = []

        score = _domain_score(n_domains, stewarded)

        gaps = []
        if n_domains == 0:
            gaps.append(no_domains_gap)
        elif used_domains == 0:
            gaps.append("None of the account's published domains is assigned to the assessed catalogs, schemas or "
                        "tables. Assign them to domains in Discover so their assets roll up to a business area.")
        if stewarded == 0:
            gaps.append("No stewardship tags (owner/steward) found; assign a named steward per domain.")
        if certified == 0:
            gaps.append("No certified assets found — certify canonical gold tables so users (and Genie) know which to trust.")
        if total_tables and pct_tagged < 50:
            gaps.append(f"Only {pct_tagged}% of tables carry any UC governed tag — tag eligible assets (PII, domain, certification) to power governed discovery.")
        if total_tables and n_domains and pct_in_domain < 50:
            gaps.append(f"Only {pct_in_domain}% of tables are in a domain — assign their catalog, schema or the table to a domain so assets roll up to business-aligned domains.")
        if top_accessed and top_certified is not None and top_certified < top_accessed:
            gaps.append(f"Only {top_certified} of your top {top_accessed} most-accessed resources are certified — certify high-traffic tables so Genie/ontology can trust your busiest data.")

        signals = [
            domains_signal,
            {"label": "Domains used here", "value": used_domains,
             "detail": "Published domains assigned to an assessed catalog, schema or table"},
            {"label": "Domain assignments", "value": assignments,
             "detail": "Catalogs, schemas and tables assigned to a published domain"},
            {"label": "Stewarded assets", "value": stewarded, "detail": "Assets with an owner/steward tag"},
            {"label": "Certified assets", "value": certified, "detail": "Tables tagged system.certification_status = certified"},
        ]
        if total_tables:
            signals.append({"label": "Tables tagged", "value": pct_tagged, "unit": "%",
                            "detail": f"{governed_tagged} of {total_tables} tables carry a UC governed tag"})
            signals.append({"label": "Tables in a domain", "value": pct_in_domain, "unit": "%",
                            "detail": f"{domain_tagged} of {total_tables} tables are in a domain, directly or "
                                      f"through their schema or catalog"})
        if top_accessed and top_certified is not None:
            signals.append({"label": "Top accessed certified", "value": top_certified, "unit": f"/ {top_accessed}",
                            "detail": f"{top_certified} out of the top {top_accessed} most accessed resources are certified (last 90d)"})

        # Per-schema drill-down (#10): domain / steward / certified tag coverage by
        # schema, so a domain lead sees which schemas lack governance tags.
        drill_rows = []
        try:
            drows = await execute_sql(
                f"SELECT catalog_name AS catalog, schema_name AS schema, "
                f"  COUNT(DISTINCT CASE WHEN {domain_pred} "
                f"    THEN concat_ws('.', catalog_name, schema_name, table_name) END) AS domain_tagged, "
                f"  COUNT(DISTINCT CASE WHEN lower(tag_name) IN ({steward_keys}) "
                f"    THEN concat_ws('.', catalog_name, schema_name, table_name) END) AS stewarded, "
                f"  COUNT(DISTINCT CASE WHEN lower(tag_name) IN ({cert_keys}) AND lower(tag_value) = 'certified' "
                f"    THEN concat_ws('.', catalog_name, schema_name, table_name) END) AS certified "
                f"FROM {tt} GROUP BY catalog_name, schema_name ORDER BY domain_tagged DESC LIMIT 500"
            )
            drill_rows = [
                {"catalog": r.get("catalog"), "schema": r.get("schema"),
                 "domain_tagged": int(r.get("domain_tagged") or 0),
                 "stewarded": int(r.get("stewarded") or 0), "certified": int(r.get("certified") or 0)}
                for r in drows if r.get("catalog")
            ]
        except Exception as e:
            logger.info(f"domains per-schema drill-down failed: {str(e)[:80]}")

        return {
            "available": True,
            "score": score,
            "signals": signals,
            "gaps": gaps,
            "note": "Domains are account-wide: the count is the account's published domains from the Discover "
                    "Domains API (drafts excluded), the same for every scope. Coverage is for the assessed "
                    "catalogs: the domains used here and the tables in a domain, directly or through their "
                    "schema or catalog.",
            "metrics": {**api_metrics, "used_domains": used_domains, "domain_tag_assignments": assignments,
                        "stewarded_assets": stewarded, "certified_assets": certified,
                        "total_tables": total_tables,
                        "governed_tagged_assets": governed_tagged, "pct_tagged": pct_tagged,
                        "domain_tagged_assets": domain_tagged, "pct_in_domain": pct_in_domain,
                        "top_accessed": top_accessed, "top_accessed_certified": top_certified,
                        "top_accessed_list": top_accessed_list},
            "drill_down": _drill(
                "Governance tags by schema",
                [{"key": "catalog", "label": "Catalog"},
                 {"key": "schema", "label": "Schema"},
                 {"key": "domain_tagged", "label": "Tables tagged with a domain (directly)"},
                 {"key": "stewarded", "label": "Stewarded"},
                 {"key": "certified", "label": "Certified"}],
                drill_rows,
            ),
        }
    except Exception as e:
        return _failed(e, "Domains and stewardship",
                       "The governed-tag queries for domain coverage failed; use the self-assessment.")


# ---------------------------------------------------------------------------
# 7. Adoption & activity (system tables — optional)
# ---------------------------------------------------------------------------
@_with_gap_ids
async def probe_adoption() -> dict:
    wsf, wparams = workspace_predicate()
    q = ADOPTION_SPEC["queries_by_id"]
    label = ADOPTION_SPEC["signal_labels"]
    try:
        # Keep the last read failure so we can classify WHY both signals came back
        # empty (issue #20): system tables not enabled vs. a grant the SP/viewer
        # lacks — the two render differently in the UI.
        last_err: Exception | None = None
        active_users = None
        try:
            # System tables default to OBO like every other signal; if the viewer
            # lacks the grant, execute_sql falls back to the app SP automatically.
            # Scoped to the workspaces in the active filter (issue #25).
            active_users = await _scalar(
                render_sql(q["active_users"]["sql"], workspace_filter=wsf).rstrip(";"),
                parameters=wparams,
            )
        except Exception as e:
            active_users = None
            last_err = e

        queries_30d = None
        try:
            queries_30d = await _scalar(
                render_sql(q["queries_30d"]["sql"], workspace_filter=wsf).rstrip(";"),
                parameters=wparams,
            )
        except Exception as e:
            queries_30d = None
            last_err = e

        if active_users is None and queries_30d is None:
            # Neither read returned. Under OBO the read ran as the viewer and, on an
            # authorization error, would have fallen back to the app SP — but a
            # non-authz failure (timeout/5xx) skips that fallback, so we can't assert
            # the SP was actually tried. Point at both grant targets instead; for this
            # workspace-wide signal the SP grant is usually the right fix.
            who = ("the app service principal (recommended for this workspace-wide "
                   "signal) or your user account"
                   if get_user_token()
                   else "the app service principal")
            # Classify WHY (issue #20): a missing system schema (not enabled) renders
            # as neutral "not available", an authz denial as "insufficient permission",
            # rather than always claiming a grant is missing.
            reason = _reason_for(last_err) if last_err is not None else "not_enabled"
            return _empty("System tables (system.access / system.query) are not enabled "
                          f"or not granted to {who}.", reason=reason)

        # Band the (time-windowed) activity counts into fixed tiers so day-to-day
        # drift rarely moves the score — keeps runs comparable while still
        # rewarding real adoption (the bands are in the spec).
        score = adoption_band_points(active_users)
        if queries_30d and queries_30d > 0:
            score += ADOPTION_QUERY_POINTS
        score = min(score, 100.0)

        signals = []
        if active_users is not None:
            signals.append({"label": label["active_users"], "value": active_users, "detail": "Distinct users in audit log"})
        if queries_30d is not None:
            signals.append({"label": label["queries_30d"], "value": queries_30d, "detail": "Query history volume"})

        # Per-workspace drill-down (#10) — only meaningful when more than one
        # workspace is in scope (adoption is otherwise a single workspace-wide number).
        drill_down = None
        if is_multi_workspace():
            drill_down = _drill(
                "Adoption by workspace",
                [{"key": "workspace", "label": "Workspace"},
                 {"key": "active_users", "label": "Active users (30d)"},
                 {"key": "queries", "label": "Queries (30d)"}],
                await _adoption_by_workspace(wsf, wparams),
            )

        return {"available": True, "score": score, "signals": signals, "gaps": [], "gap_ids": [], "note": None,
                "metrics": {"active_users_30d": active_users, "queries_30d": queries_30d},
                "drill_down": drill_down}
    except Exception as e:
        return _failed(e, "Adoption signals")


async def _adoption_by_workspace(wsf: str, wparams: dict) -> list[dict]:
    """Best-effort per-workspace active-users and query volume for the adoption
    drill-down, with workspace names from system.access.workspaces_latest."""
    try:
        rows = await execute_sql(
            "SELECT COALESCE(w.workspace_name, CAST(u.workspace_id AS STRING)) AS workspace, "
            "       u.active_users AS active_users, COALESCE(q.queries, 0) AS queries "
            "FROM ( "
            "  SELECT workspace_id, COUNT(DISTINCT user_identity.email) AS active_users "
            "  FROM system.access.audit "
            f"  WHERE event_date >= current_date() - INTERVAL {ADOPTION_WINDOW_DAYS} DAYS {wsf}"
            "  GROUP BY workspace_id "
            ") u "
            "LEFT JOIN ( "
            "  SELECT workspace_id, COUNT(*) AS queries FROM system.query.history "
            f"  WHERE start_time >= current_timestamp() - INTERVAL {ADOPTION_WINDOW_DAYS} DAYS {wsf}"
            "  GROUP BY workspace_id "
            ") q ON CAST(u.workspace_id AS STRING) = CAST(q.workspace_id AS STRING) "
            "LEFT JOIN system.access.workspaces_latest w "
            "  ON CAST(u.workspace_id AS STRING) = CAST(w.workspace_id AS STRING) "
            "ORDER BY u.active_users DESC LIMIT 200",
            parameters=wparams or None,
        )
        return [
            {"workspace": r.get("workspace"), "active_users": int(r.get("active_users") or 0),
             "queries": int(r.get("queries") or 0)}
            for r in rows if r.get("workspace")
        ]
    except Exception as e:
        logger.info(f"adoption per-workspace drill-down failed: {str(e)[:80]}")
        return []


# Map pillar key -> probe coroutine
PROBES = {
    "uc_foundation": probe_uc_foundation,
    "metadata": probe_metadata,
    "relationships": probe_relationships,
    "metrics": probe_metrics,
    "genie_agents": probe_genie_agents,
    "domains": probe_domains,
    "adoption": probe_adoption,
}
