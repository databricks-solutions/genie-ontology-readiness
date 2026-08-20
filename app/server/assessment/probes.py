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
which customers often won't do for an app SP. So we resolve the source once:
if ``system.information_schema`` is readable we use it; otherwise we fall back
to unioning each accessible catalog's own ``<catalog>.information_schema`` —
which only needs catalog-level SELECT (grantable by any catalog owner).
"""

import asyncio
import json
import logging
import aiohttp

from server.sql_client import execute_sql
from server.config import (
    get_workspace_host,
    get_auth_headers,
    ASSESS_CATALOGS,
    GENIE_SPACE_ID,
)

logger = logging.getLogger(__name__)

# Catalogs that are never part of a customer's own data estate.
# Excluded from the *Unity Catalog* footprint. `hive_metastore` is the legacy
# workspace-local metastore (not UC) — it is counted separately for UC coverage.
_INTERNAL_CATALOGS = ("system", "__databricks_internal", "samples", "hive_metastore")


def _empty(note: str) -> dict:
    return {"available": False, "score": 0.0, "signals": [], "gaps": [], "note": note, "metrics": {}}


async def _scalar(query: str, force_sp: bool = False):
    """First column of the first row, coercing numeric strings.

    The Statement Execution API returns every value as a string in JSON_ARRAY
    format, so COUNT(*) comes back as e.g. "8" — coerce to int/float so callers
    can compare numerically.

    ``force_sp=True`` runs as the app service principal (for system-table reads).
    """
    rows = await execute_sql(query, force_sp=force_sp)
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


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------
_sources = None
_sources_lock = asyncio.Lock()


async def _resolve_sources() -> dict:
    """Determine whether system.information_schema is readable and which
    catalogs to assess. Cached for the process lifetime."""
    global _sources
    if _sources is not None:
        return _sources
    async with _sources_lock:
        if _sources is not None:
            return _sources

        system_ok = False
        try:
            await execute_sql("SELECT 1 FROM system.information_schema.tables LIMIT 1")
            system_ok = True
        except Exception:
            system_ok = False

        catalogs = list(ASSESS_CATALOGS)
        if not catalogs:
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

        # When restricted to specific catalogs, prefer per-catalog reads even if
        # system is readable, so we never depend on system grants we can't assume.
        use_system = system_ok and not ASSESS_CATALOGS

        # In per-catalog mode, SHOW CATALOGS may list catalogs the SP can only
        # BROWSE (not SELECT) — querying their information_schema would fail and
        # break the UNION. Keep only catalogs whose information_schema is
        # actually readable, tested concurrently.
        if not use_system and catalogs:
            async def _readable(c: str) -> bool:
                try:
                    await execute_sql(f"SELECT 1 FROM `{c}`.information_schema.tables LIMIT 1")
                    return True
                except Exception:
                    return False
            checks = await asyncio.gather(*(_readable(c) for c in catalogs))
            accessible = [c for c, ok in zip(catalogs, checks) if ok]
            if accessible:
                catalogs = accessible
            logger.info(f"accessible catalogs: {len(catalogs)} of {len(checks)} discovered")

        _sources = {"system_ok": use_system, "catalogs": catalogs}
        logger.info(f"assessment sources: system_ok={use_system}, catalogs={len(catalogs)}")
        return _sources


def _src(view: str, sources: dict) -> str | None:
    """FROM-able source for an information_schema view, aliased as _t."""
    if sources["system_ok"]:
        return f"system.information_schema.{view} AS _t"
    cats = sources["catalogs"]
    if not cats:
        return None
    union = " UNION ALL ".join(f"SELECT * FROM `{c}`.information_schema.{view}" for c in cats)
    return f"({union}) AS _t"


# ---------------------------------------------------------------------------
# 1. Unity Catalog foundation
# ---------------------------------------------------------------------------
async def probe_uc_foundation() -> dict:
    s = await _resolve_sources()
    n_catalogs = len(s["catalogs"])
    if n_catalogs == 0 and not s["system_ok"]:
        return _empty("No catalogs are readable by the app service principal. Grant it USE CATALOG + SELECT on the catalogs to assess.")
    try:
        tbl = _src("tables", s)
        sch = _src("schemata", s)
        n_schemas = await _scalar(f"SELECT COUNT(*) FROM {sch} WHERE schema_name <> 'information_schema'")
        rows = await execute_sql(
            f"""SELECT COUNT(*) AS total,
                       SUM(CASE WHEN table_type IN ('MANAGED','MANAGED_SHALLOW_CLONE') THEN 1 ELSE 0 END) AS managed
                FROM {tbl} WHERE table_schema <> 'information_schema'"""
        )
        total = int(rows[0].get("total") or 0)
        managed = int(rows[0].get("managed") or 0)

        # Legacy (non-UC) footprint: tables still in the workspace-local Hive
        # metastore. Best-effort — hive_metastore exposes its own
        # information_schema in current runtimes; if it isn't readable we simply
        # omit the coverage signal rather than fail the pillar.
        non_uc = None
        try:
            non_uc = int(await _scalar(
                "SELECT COUNT(*) FROM hive_metastore.information_schema.tables "
                "WHERE table_schema <> 'information_schema'"
            ) or 0)
        except Exception as e:
            logger.info(f"hive_metastore not readable for UC-coverage signal: {str(e)[:80]}")
            non_uc = None
        uc_coverage_pct = _pct(total, total + non_uc) if non_uc is not None else None

        score = 0.0
        if n_catalogs:
            score += 40
        if total > 0:
            score += 30
        if total >= 50:
            score += 15
        if n_schemas and n_schemas >= 5:
            score += 15
        score = min(score, 100.0)

        gaps = []
        if not n_catalogs:
            gaps.append("No user catalogs found — Unity Catalog may not be in active use.")
        if total < 50:
            gaps.append("Limited table footprint; broaden UC adoption beyond an initial workload.")
        if non_uc:
            gaps.append(
                f"{non_uc} table(s) ({round(100 - uc_coverage_pct, 1)}%) are still in the legacy "
                f"hive_metastore (not in Unity Catalog) — migrate them into UC (see the UCX accelerator)."
            )

        signals = [
            {"label": "Catalogs", "value": n_catalogs, "detail": "User catalogs assessed"},
            {"label": "Schemas", "value": n_schemas, "detail": "Excluding information_schema"},
            {"label": "Managed", "value": _pct(managed, total), "unit": "%", "detail": "% of tables that are UC-managed"},
        ]
        # Only surface the not-in-UC footprint (never the raw in-UC table count).
        if non_uc is not None:
            signals.append({
                "label": "In Unity Catalog", "value": uc_coverage_pct, "unit": "%",
                "detail": "Share of tables in Unity Catalog vs. legacy hive_metastore",
            })
            signals.append({
                "label": "Not in Unity Catalog", "value": non_uc,
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

        return {
            "available": True,
            "score": score,
            "signals": signals,
            "gaps": gaps,
            "note": None,
            "metrics": {
                "catalogs": n_catalogs, "schemas": n_schemas, "tables": total,
                "managed_pct": _pct(managed, total),
                "uc_tables": total, "non_uc_tables": non_uc, "uc_coverage_pct": uc_coverage_pct,
                "legacy_by_schema": legacy_by_schema,
            },
        }
    except Exception as e:
        logger.warning(f"probe_uc_foundation failed: {e}")
        return _empty(f"Could not read information_schema ({str(e)[:120]}). The app SP may lack catalog access.")


# ---------------------------------------------------------------------------
# 2. Metadata richness (comments + tags)
# ---------------------------------------------------------------------------
async def probe_metadata() -> dict:
    s = await _resolve_sources()
    tbl = _src("tables", s)
    col = _src("columns", s)
    if tbl is None:
        return _empty("No readable catalogs for metadata assessment.")
    try:
        rows = await execute_sql(
            f"""SELECT COUNT(*) AS total,
                       SUM(CASE WHEN comment IS NOT NULL AND comment <> '' THEN 1 ELSE 0 END) AS commented
                FROM {tbl} WHERE table_schema <> 'information_schema'"""
        )
        t_total = int(rows[0].get("total") or 0)
        t_commented = int(rows[0].get("commented") or 0)

        crows = await execute_sql(
            f"""SELECT COUNT(*) AS total,
                       SUM(CASE WHEN comment IS NOT NULL AND comment <> '' THEN 1 ELSE 0 END) AS commented
                FROM {col} WHERE table_schema <> 'information_schema'"""
        )
        c_total = int(crows[0].get("total") or 0)
        c_commented = int(crows[0].get("commented") or 0)

        tagged_tables = None
        tt = _src("table_tags", s)
        if tt is not None:
            try:
                tagged_tables = await _scalar(f"SELECT COUNT(DISTINCT table_name) FROM {tt}")
            except Exception:
                tagged_tables = None

        table_pct = _pct(t_commented, t_total)
        col_pct = _pct(c_commented, c_total)
        score = round(0.5 * table_pct + 0.5 * col_pct, 1)

        gaps = []
        if table_pct < 80:
            gaps.append(f"Only {table_pct}% of tables have descriptions — Genie relies on these to understand data.")
        if col_pct < 60:
            gaps.append(f"Only {col_pct}% of columns are commented; aim for high coverage on gold-layer columns.")
        if tagged_tables is not None and t_total and tagged_tables == 0:
            gaps.append("No governed tags found; tags aid discovery and domain organization.")

        signals = [
            {"label": "Tables commented", "value": table_pct, "unit": "%", "detail": f"{t_commented} of {t_total} tables"},
            {"label": "Columns commented", "value": col_pct, "unit": "%", "detail": f"{c_commented} of {c_total} columns"},
        ]
        if tagged_tables is not None:
            signals.append({"label": "Tagged tables", "value": tagged_tables, "detail": "Tables with ≥1 governed tag"})

        return {
            "available": True,
            "score": score,
            "signals": signals,
            "gaps": gaps,
            "note": None,
            "metrics": {"table_comment_pct": table_pct, "column_comment_pct": col_pct, "tagged_tables": tagged_tables},
        }
    except Exception as e:
        logger.warning(f"probe_metadata failed: {e}")
        return _empty(f"Could not read comment coverage ({str(e)[:120]}).")


# ---------------------------------------------------------------------------
# 3. Relationships & modeling (PK/FK + gold layer)
# ---------------------------------------------------------------------------
async def probe_relationships() -> dict:
    s = await _resolve_sources()
    tbl = _src("tables", s)
    if tbl is None:
        return _empty("No readable catalogs for relationship assessment.")
    try:
        constraints_available = True
        pk = fk = 0
        tc = _src("table_constraints", s)
        try:
            rows = await execute_sql(f"SELECT constraint_type, COUNT(*) AS n FROM {tc} GROUP BY constraint_type")
            by_type = {r["constraint_type"]: int(r["n"] or 0) for r in rows}
            pk = by_type.get("PRIMARY KEY", 0)
            fk = by_type.get("FOREIGN KEY", 0)
        except Exception:
            constraints_available = False

        gold_tables = await _scalar(
            f"""SELECT COUNT(*) FROM {tbl}
                WHERE lower(table_schema) RLIKE '(gold|mart|marts|analytics|semantic|presentation|reporting|dwh)'
                   OR lower(table_name) RLIKE '^(gold_|mart_|dim_|fact_)'"""
        ) or 0

        score = 0.0
        if gold_tables and gold_tables > 0:
            score += 50
        if constraints_available:
            if fk > 0:
                score += 35
            if pk > 0:
                score += 15
        score = min(score, 100.0)

        gaps = []
        if not gold_tables:
            gaps.append("No clearly-named gold/mart layer detected; Genie performs best on curated, pre-joined tables.")
        if constraints_available and fk == 0:
            gaps.append("No foreign-key constraints declared; PK/FK relationships let Genie infer joins reliably.")

        signals = [{"label": "Gold-layer tables", "value": gold_tables, "detail": "Tables in gold/mart/analytics-style schemas"}]
        if constraints_available:
            signals += [
                {"label": "Primary keys", "value": pk, "detail": "Declared PK constraints"},
                {"label": "Foreign keys", "value": fk, "detail": "Declared FK constraints"},
            ]

        note = None if constraints_available else "Constraint metadata not available; relationship score is based on the gold layer only."
        return {"available": True, "score": score, "signals": signals, "gaps": gaps, "note": note,
                "metrics": {"primary_keys": pk, "foreign_keys": fk, "gold_tables": gold_tables, "constraints_available": constraints_available}}
    except Exception as e:
        logger.warning(f"probe_relationships failed: {e}")
        return _empty(f"Could not assess relationships ({str(e)[:120]}).")


# ---------------------------------------------------------------------------
# 4. Semantic layer (metric views)
# ---------------------------------------------------------------------------
async def probe_metrics() -> dict:
    s = await _resolve_sources()
    tbl = _src("tables", s)
    if tbl is None:
        return _empty("No readable catalogs for the semantic-layer assessment.")
    try:
        metric_views = None
        type_value = None
        for tv in ("METRIC_VIEW", "METRIC VIEW"):
            try:
                metric_views = await _scalar(f"SELECT COUNT(*) FROM {tbl} WHERE table_type = '{tv}'")
                if metric_views is not None:
                    type_value = tv
                    break
            except Exception:
                continue
        if metric_views is None:
            return _empty("Metric view metadata not available on this metastore version; use the self-assessment for the semantic layer.")

        # If no metric views, return early with the "absent" result
        if metric_views == 0:
            return {
                "available": True,
                "score": 0.0,
                "signals": [{"label": "Metric views", "value": 0, "detail": "UC metric views"}],
                "gaps": ["No metric views found. Metric views are the GA foundation that feeds Genie Ontology — define KPIs centrally here."],
                "note": None,
                "metrics": {"metric_views": 0},
            }

        # Query for commented metric views (defensive: treat failure as 0)
        commented = 0
        try:
            commented = await _scalar(
                f"SELECT COUNT(*) FROM {tbl} WHERE table_type = '{type_value}' AND comment IS NOT NULL AND trim(comment) <> ''"
            ) or 0
        except Exception:
            commented = 0

        # Score formula:
        # - existence: +30 if metric_views > 0
        # - coverage ramp: +40 * min(metric_views, 10) / 10
        # - quality: +30 * (commented / metric_views)
        score = 30.0
        score += 40.0 * min(metric_views, 10) / 10.0
        score += 30.0 * (float(commented) / float(metric_views)) if metric_views > 0 else 0.0
        score = round(min(score, 100.0), 1)

        # Build signals
        signals = [
            {"label": "Metric views", "value": metric_views, "detail": "UC metric views"}
        ]
        if metric_views > 0:
            signals.append({
                "label": "Commented",
                "value": _pct(commented, metric_views),
                "unit": "%",
                "detail": "Share of metric views with a description",
            })

        # Build gaps
        gaps = []
        if metric_views < 3:
            gaps.append("Few metric views; expand coverage so common KPIs are centrally defined and certified.")
        if metric_views > 0:
            uncommented = metric_views - commented
            if uncommented > 0:
                gaps.append(
                    f"{uncommented} metric view(s) lack a description — Genie reads metric-view, dimension, and measure comments to reason; add them."
                )

        return {
            "available": True,
            "score": score,
            "signals": signals,
            "gaps": gaps,
            "note": None,
            "metrics": {"metric_views": metric_views, "metric_views_commented": commented},
        }
    except Exception as e:
        logger.warning(f"probe_metrics failed: {e}")
        return _empty(f"Could not count metric views ({str(e)[:120]}).")


# ---------------------------------------------------------------------------
# 5. Genie Agents (REST) — deep curation assessment
# ---------------------------------------------------------------------------
_MAX_INSPECT = 30  # cap how many spaces we deep-inspect to bound latency


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
        async with aiohttp.ClientSession() as session:
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
    """Best-effort Genie usage from the audit system table.

    system.access.audit records Genie activity under service_name='aibiGenie'
    with the space id in request_params.space_id. The count of Genie Agents is
    the number of distinct space_ids, excluding any space that has ever been
    trashed (a `trashSpace` action; there is no deleteSpace — see the docs at
    https://docs.databricks.com/aws/en/ai-bi/admin/audit).

    Single scan: group by space_id and derive per-space "trashed" and
    "active in last 30 days" flags in one pass (instead of separate COUNT
    DISTINCT + NOT IN queries that scanned the large audit table repeatedly).
    Returns total / active_30d (each None if the audit table isn't readable).
    """
    try:
        rows = await execute_sql(
            "SELECT COUNT(*) AS total, "
            "       SUM(CASE WHEN active_30d = 1 THEN 1 ELSE 0 END) AS active_30d "
            "FROM ( "
            "  SELECT request_params.space_id AS space_id, "
            "         MAX(CASE WHEN lower(action_name) = 'trashspace' THEN 1 ELSE 0 END) AS trashed, "
            "         MAX(CASE WHEN event_date >= current_date() - INTERVAL 30 DAYS THEN 1 ELSE 0 END) AS active_30d "
            "  FROM system.access.audit "
            "  WHERE service_name = 'aibiGenie' AND request_params.space_id IS NOT NULL "
            "  GROUP BY request_params.space_id "
            ") WHERE trashed = 0"
        )
        row = rows[0] if rows else {}
        return {
            "total": int(row.get("total") or 0),
            "active_30d": int(row.get("active_30d") or 0),
        }
    except Exception:
        return {"total": None, "active_30d": None}


def _genie_audit_signals(audit: dict) -> list:
    sig = []
    if audit.get("total") is not None:
        sig.append({"label": "Genie Agents", "value": audit["total"],
                    "detail": "Distinct existing agents in system.access.audit (aibiGenie)"})
    if audit.get("active_30d") is not None:
        sig.append({"label": "Active agents (30d)", "value": audit["active_30d"],
                    "detail": "Distinct agents with activity in the last 30 days — audit log"})
    return sig


async def probe_genie_agents() -> dict:
    """Count Genie Agents from the audit log ONLY (system.access.audit / aibiGenie).

    We deliberately do not use the Genie REST API "spaces visible to the app": it is
    permission- and scope-gated (on-behalf-of-user tokens lack the genie scope), and
    it reflects only what one principal can see. The audit log gives a workspace-wide,
    existing-spaces count via the viewer's own system-table access.
    """
    audit = await _genie_audit_counts()
    total, active = audit.get("total"), audit.get("active_30d")
    if total is None and active is None:
        return _empty("Genie usage can't be read — the assessing identity needs SELECT on system.access.audit.")
    total, active = total or 0, active or 0

    score = 0.0
    if total > 0:
        score += 40
    if active > 0:
        score += 40
    if total > 0 and active / total >= 0.3:
        score += 20
    score = min(score, 100.0)

    gaps = []
    if total == 0:
        gaps.append("No Genie Agents found in the audit log — create a curated Genie Agent as the entry point to natural-language analytics.")
    elif active == 0:
        gaps.append(f"{total} Genie Agent(s) exist but none were active in the last 30 days — drive adoption or retire stale agents.")

    return {
        "available": True,
        "score": round(score, 1),
        "signals": _genie_audit_signals(audit),
        "gaps": gaps,
        "note": "Counted from system.access.audit (aibiGenie). Curation quality — instructions, "
                "example/verified SQL, benchmarks — isn't visible in the audit log; use the "
                "Genie Agent Quality Workshop accelerator to assess and lift it.",
        "metrics": {"genie_agents": total, "active_30d": active, "genie_audit": audit},
    }


# ---------------------------------------------------------------------------
# 6. Domains & stewardship (native API, else governed-tag proxy)
# ---------------------------------------------------------------------------
_DOMAIN_TAG_KEYS = ("domain", "data_domain", "business_domain", "subject_area", "data_product")
_STEWARD_TAG_KEYS = ("owner", "data_owner", "steward", "data_steward")
# Certification is the governed tag `system.certification_status` with value
# `certified` (or `deprecated`). Match both the namespaced and bare key forms.
# Docs: https://docs.databricks.com/aws/en/data-governance/unity-catalog/certify-deprecate-data
_CERT_TAG_KEYS = ("system.certification_status", "certification_status")


async def _native_domains() -> int | None:
    host = get_workspace_host()
    headers = get_auth_headers()  # viewer token if present; the tag proxy is the user-scoped fallback
    if not host or not headers:
        return None
    for url in (
        f"{host}/api/2.1/unity-catalog/data-domains",
        f"{host}/api/2.0/data-domains",
    ):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        domains = data.get("data_domains") or data.get("domains") or data.get("data") or []
                        return len(domains)
        except Exception:
            continue
    return None


async def probe_domains() -> dict:
    native = await _native_domains()
    if native is not None:
        score = min(100.0, 40 + 60 * min(native, 5) / 5) if native else 0.0
        gaps = [] if native else ["No domains defined; organize assets into business-aligned domains with stewards."]
        return {"available": True, "score": score,
                "signals": [{"label": "Domains (native)", "value": native, "detail": "Business/data domains defined in Unity Catalog"}],
                "gaps": gaps, "note": None, "metrics": {"domains": native, "source": "native_api"}}

    s = await _resolve_sources()
    tt = _src("table_tags", s)
    st = _src("schema_tags", s)
    if tt is None:
        return _empty("Domains API unavailable and no readable catalogs for the tag proxy; use the self-assessment.")
    try:
        domain_keys = ", ".join(f"'{k}'" for k in _DOMAIN_TAG_KEYS)
        steward_keys = ", ".join(f"'{k}'" for k in _STEWARD_TAG_KEYS)
        cert_keys = ", ".join(f"'{k}'" for k in _CERT_TAG_KEYS)

        parts = [f"SELECT tag_value FROM {tt} WHERE lower(tag_name) IN ({domain_keys})"]
        if st is not None:
            parts.append(f"SELECT tag_value FROM {st} WHERE lower(tag_name) IN ({domain_keys})")
        rows = await execute_sql(
            f"SELECT COUNT(DISTINCT tag_value) AS distinct_domains, COUNT(*) AS assignments FROM ({' UNION ALL '.join(parts)})"
        )
        distinct_domains = int(rows[0].get("distinct_domains") or 0)
        assignments = int(rows[0].get("assignments") or 0)

        stewarded = 0
        try:
            stewarded = int(await _scalar(f"SELECT COUNT(*) FROM {tt} WHERE lower(tag_name) IN ({steward_keys})") or 0)
        except Exception:
            stewarded = 0

        # Certified assets: distinct tables whose system.certification_status
        # governed tag is 'certified' (excludes 'deprecated').
        certified = 0
        try:
            certified = int(await _scalar(
                f"SELECT COUNT(DISTINCT concat_ws('.', catalog_name, schema_name, table_name)) "
                f"FROM {tt} WHERE lower(tag_name) IN ({cert_keys}) AND lower(tag_value) = 'certified'"
            ) or 0)
        except Exception:
            certified = 0

        # Coverage over eligible assets (tables): how many carry ANY UC governed
        # tag, and how many live in a domain (carry a domain-style tag), vs. the
        # total table footprint — surfaced as percentages.
        tbl = _src("tables", s)
        total_tables = governed_tagged = domain_tagged = 0
        try:
            total_tables = int(await _scalar(f"SELECT COUNT(*) FROM {tbl} WHERE table_schema <> 'information_schema'") or 0)
        except Exception:
            total_tables = 0
        try:
            governed_tagged = int(await _scalar(
                f"SELECT COUNT(DISTINCT concat_ws('.', catalog_name, schema_name, table_name)) FROM {tt}"
            ) or 0)
        except Exception:
            governed_tagged = 0
        try:
            domain_tagged = int(await _scalar(
                f"SELECT COUNT(DISTINCT concat_ws('.', catalog_name, schema_name, table_name)) "
                f"FROM {tt} WHERE lower(tag_name) IN ({domain_keys})"
            ) or 0)
        except Exception:
            domain_tagged = 0
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
            rows = await execute_sql(
                "WITH top AS ("
                "  SELECT source_table_full_name AS name, COUNT(DISTINCT created_by) AS n "
                "  FROM system.access.table_lineage "
                "  WHERE source_table_full_name IS NOT NULL "
                "    AND source_table_catalog NOT IN ('system','__databricks_internal','samples') "
                "    AND source_table_schema <> 'information_schema' "
                "    AND event_date >= current_date() - INTERVAL 90 DAYS "
                "  GROUP BY source_table_full_name ORDER BY n DESC LIMIT 10 "
                "), cert AS ("
                "  SELECT concat_ws('.', catalog_name, schema_name, table_name) AS name "
                "  FROM system.information_schema.table_tags "
                "  WHERE lower(tag_name) IN ('system.certification_status','certification_status') "
                "        AND lower(tag_value) = 'certified' "
                ") SELECT t.name AS name, t.n AS accesses, "
                "         CASE WHEN c.name IS NOT NULL THEN 1 ELSE 0 END AS certified "
                "FROM top t LEFT JOIN cert c ON t.name = c.name ORDER BY t.n DESC"
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

        score = 0.0
        if distinct_domains > 0:
            score += 40 + 40 * min(distinct_domains, 5) / 5
        if stewarded > 0:
            score += 20
        score = min(score, 100.0)

        gaps = []
        if distinct_domains == 0:
            gaps.append("No domain-style governed tags found (e.g. a `domain` tag). Organize assets into business-aligned domains.")
        if stewarded == 0:
            gaps.append("No stewardship tags (owner/steward) found; assign a named steward per domain.")
        if certified == 0:
            gaps.append("No certified assets found — certify canonical gold tables so users (and Genie) know which to trust.")
        if total_tables and pct_tagged < 50:
            gaps.append(f"Only {pct_tagged}% of tables carry any UC governed tag — tag eligible assets (PII, domain, certification) to power governed discovery.")
        if total_tables and pct_in_domain < 50:
            gaps.append(f"Only {pct_in_domain}% of tables are assigned to a domain — apply domain tags so assets roll up to business-aligned domains.")
        if top_accessed and top_certified is not None and top_certified < top_accessed:
            gaps.append(f"Only {top_certified} of your top {top_accessed} most-accessed resources are certified — certify high-traffic tables so Genie/ontology can trust your busiest data.")

        signals = [
            {"label": "Distinct domains (via tags)", "value": distinct_domains, "detail": "Distinct values of domain-style governed tags"},
            {"label": "Domain-tagged assets", "value": assignments, "detail": "Assets carrying a domain tag"},
            {"label": "Stewarded assets", "value": stewarded, "detail": "Assets with an owner/steward tag"},
            {"label": "Certified assets", "value": certified, "detail": "Tables tagged system.certification_status = certified"},
        ]
        if total_tables:
            signals.append({"label": "Tables tagged", "value": pct_tagged, "unit": "%",
                            "detail": f"{governed_tagged} of {total_tables} tables carry a UC governed tag"})
            signals.append({"label": "Tables in a domain", "value": pct_in_domain, "unit": "%",
                            "detail": f"{domain_tagged} of {total_tables} tables carry a domain tag"})
        if top_accessed and top_certified is not None:
            signals.append({"label": "Top accessed certified", "value": top_certified, "unit": f"/ {top_accessed}",
                            "detail": f"{top_certified} out of the top {top_accessed} most accessed resources are certified (last 90d)"})

        return {
            "available": True,
            "score": score,
            "signals": signals,
            "gaps": gaps,
            "note": "Assessed via governed tags (the native UC Domains feature is a gated preview). "
                    "Use the self-assessment to capture domain design maturity the tags can't show.",
            "metrics": {"distinct_domains": distinct_domains, "domain_tag_assignments": assignments,
                        "stewarded_assets": stewarded, "certified_assets": certified,
                        "total_tables": total_tables,
                        "governed_tagged_assets": governed_tagged, "pct_tagged": pct_tagged,
                        "domain_tagged_assets": domain_tagged, "pct_in_domain": pct_in_domain,
                        "top_accessed": top_accessed, "top_accessed_certified": top_certified,
                        "top_accessed_list": top_accessed_list,
                        "source": "tag_proxy"},
        }
    except Exception as e:
        logger.warning(f"probe_domains failed: {e}")
        return _empty(f"Domains API unavailable and tag proxy failed ({str(e)[:120]}); use the self-assessment.")


# ---------------------------------------------------------------------------
# 7. Adoption & activity (system tables — optional)
# ---------------------------------------------------------------------------
async def probe_adoption() -> dict:
    try:
        active_users = None
        try:
            active_users = await _scalar(
                "SELECT COUNT(DISTINCT user_identity.email) FROM system.access.audit "
                "WHERE event_date >= current_date() - INTERVAL 30 DAYS",
                force_sp=True,  # system tables: the SP is granted; an OBO viewer may not be
            )
        except Exception:
            active_users = None

        queries_30d = None
        try:
            queries_30d = await _scalar(
                "SELECT COUNT(*) FROM system.query.history "
                "WHERE start_time >= current_timestamp() - INTERVAL 30 DAYS",
                force_sp=True,
            )
        except Exception:
            queries_30d = None

        if active_users is None and queries_30d is None:
            return _empty("System tables (system.access / system.query) are not enabled or not granted to the app SP.")

        # Band the (time-windowed) activity counts into fixed tiers so day-to-day
        # drift rarely moves the score — keeps runs comparable while still
        # rewarding real adoption.
        def _band(users: int | None) -> float:
            u = users or 0
            if u <= 0:
                return 0.0
            if u < 5:
                return 20.0
            if u < 20:
                return 35.0
            if u < 50:
                return 45.0
            return 50.0

        score = _band(active_users)
        if queries_30d and queries_30d > 0:
            score += 50
        score = min(score, 100.0)

        signals = []
        if active_users is not None:
            signals.append({"label": "Active users (30d)", "value": active_users, "detail": "Distinct users in audit log"})
        if queries_30d is not None:
            signals.append({"label": "Queries (30d)", "value": queries_30d, "detail": "Query history volume"})

        return {"available": True, "score": score, "signals": signals, "gaps": [], "note": None,
                "metrics": {"active_users_30d": active_users, "queries_30d": queries_30d}}
    except Exception as e:
        logger.warning(f"probe_adoption failed: {e}")
        return _empty(f"Could not read adoption signals ({str(e)[:120]}).")


async def probe_pages() -> dict:
    """Placeholder probe for the Pages & Business Concepts pillar.

    Pages — governed, authoritative definitions of business concepts (terms,
    entities, acronyms) and the human-modeled, *cited* layer of Genie Ontology —
    are a Beta Unity Catalog Semantics feature with no public metadata API (no
    information_schema view, system table, or documented REST endpoint) as of
    this writing, so there is no reliable read-only signal to score yet.

    We return score_exempt=True: scoring.py shows and explains the pillar but
    EXCLUDES it from the overall score, so this top-weighted-but-unmeasurable
    pillar never drags the headline number down. When a detection API lands,
    replace this with a real probe and drop score_exempt so Pages counts at its
    full weight. Docs: https://docs.databricks.com/aws/en/uc-semantics/pages
    """
    return {
        "available": False,
        "score_exempt": True,
        "score": 0.0,
        "signals": [],
        "gaps": [
            "Pages readiness can't be auto-detected yet (Beta; no public metadata API) — assess it manually via the Learn tab.",
            "Have an account admin enable Pages (Previews), then author governed Pages for your top business concepts, terms, and acronyms.",
            "Give each Page an owner and a domain, link related metrics/tables, and publish + certify the canonical definitions so Genie One cites them over inferred context.",
        ],
        "note": ("Pages is a Beta Unity Catalog Semantics feature with no public metadata API yet, so it is "
                 "explained and planned for here but not scored automatically. See the Learn tab and "
                 "https://docs.databricks.com/aws/en/uc-semantics/pages."),
        "metrics": {},
    }


# Map pillar key -> probe coroutine
PROBES = {
    "uc_foundation": probe_uc_foundation,
    "metadata": probe_metadata,
    "relationships": probe_relationships,
    "metrics": probe_metrics,
    "genie_agents": probe_genie_agents,
    "domains": probe_domains,
    "pages": probe_pages,
    "adoption": probe_adoption,
}
