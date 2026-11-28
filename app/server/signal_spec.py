"""Canonical per-pillar signal spec shared by the App and the Genie Code skill (#108).

Dependency-free like ``server.pillars``: the App's probes (``assessment/probes.py``)
build their SQL, thresholds and gap text from here, and ``scripts/render_skill.py``
renders the same definitions into ``skills/genie-ontology-readiness/references/``.
``scripts/check.sh`` (and ``tests/test_skill_render.py``) fail when the rendered
skill docs are stale, so a signal changes in one place and both paths follow.

SQL templates use the skill's source placeholders (``<TABLES>``, ``<COLUMNS>``,
``<TABLE_TAGS>``, ...; see SKILL.md -> Source resolution) and ``{schema_filter}``
for the schema predicate. ``render_sql`` fills them in for either consumer.

Gap ``when`` rules are plain boolean expressions over the pillar's derived values
(valid Python). The tests evaluate them against the App's probe output across a
grid of inputs, so the documented rule and the App's behaviour cannot disagree.

Each query is either:

* ``shared`` — the App runs exactly this SQL (after placeholder substitution); or
* logical — the App computes the same result with an equivalent but differently
  shaped scan (e.g. per-catalog chunking on wide metastores), named in ``app_impl``.

Pillars migrate here one at a time. Migrated: Metadata, Unity Catalog Foundation,
Relationships, Metrics, Genie Agents and Adoption. Domains & Stewardship still lives
inline in probes.py and is hand-written in the skill until it moves (#98).

Gap ids are the stable keys other tools use (the probe's ``gap_ids`` list runs in
step with its ``gaps`` list). A gap id names a rule, not its text, so the wording can
change without breaking them.
"""

import re

# The default schema predicate when no schema allowlist is in force.
DEFAULT_SCHEMA_FILTER = "{column} <> 'information_schema'"


def render_sql(template: str, sources: dict[str, str] | None = None, schema_filter: str | None = None,
               schema_column: str = "table_schema", workspace_filter: str | None = None) -> str:
    """Fill a spec SQL template.

    ``sources`` maps a placeholder (``"<TABLE_TAGS>"``) to a FROM-able source; leave it
    out to keep the placeholder (the skill's form). ``schema_filter`` defaults to the
    information_schema exclusion on ``schema_column``. ``workspace_filter`` is the
    App's ``AND ...`` workspace predicate (``{workspace_filter}`` in the template); the
    skill leaves it out, so the placeholder renders as nothing.
    """
    values = dict(sources or {})
    values["{schema_filter}"] = (
        schema_filter if schema_filter is not None else DEFAULT_SCHEMA_FILTER.format(column=schema_column)
    )
    wf = (workspace_filter or "").strip()
    values["{workspace_filter}"] = f" {wf}" if wf else ""
    # One pass over the template, so a substituted value (e.g. a quoted schema or
    # catalog name) is never itself re-scanned for placeholders.
    pattern = re.compile("|".join(re.escape(k) for k in sorted(values, key=len, reverse=True)))
    return pattern.sub(lambda m: values[m.group(0)], template)


def gap_text(pillar: dict, gap_id: str, **values) -> str:
    """The App-facing gap message for ``gap_id``, formatted with the probe's values."""
    return pillar["gaps_by_id"][gap_id]["text"].format(**values)


def _index(pillar: dict) -> dict:
    pillar["gaps_by_id"] = {g["id"]: g for g in pillar["gaps"]}
    pillar["queries_by_id"] = {q["id"]: q for q in pillar["queries"]}
    pillar["signal_labels"] = {sig["id"]: sig["label"] for sig in pillar["signals"]}
    return pillar


# ---------------------------------------------------------------------------
# 2. Metadata Richness
# ---------------------------------------------------------------------------

TABLE_COMMENT_TARGET_PCT = 80
COLUMN_COMMENT_TARGET_PCT = 60
METADATA_TABLE_WEIGHT = 0.5
METADATA_COLUMN_WEIGHT = 0.5
# Column coverage is the heaviest read (a row per column of every table), so both the
# App and the skill scan it in batches of catalogs (#112). A metastore-wide scan of a
# ~1,400-catalog estate runs past 15 minutes; a 25-catalog batch returns in seconds.
METADATA_COLUMN_BATCH_SIZE = 25
# The skill's agent runs far fewer queries at once than the App, so it packs catalogs
# by size instead: at most this many tables per batch, a bigger catalog on its own.
# On a 1,392-catalog / 78k-table metastore that is ~8 queries instead of 56.
METADATA_BATCH_MAX_TABLES = 5000
# Give up only when NO batch has finished for this long (not a fixed total deadline),
# so a slow but progressing scan of a huge metastore can still finish.
METADATA_NO_PROGRESS_TIMEOUT_S = 300

METADATA = _index({
    "key": "metadata",
    "queries": [
        {
            "id": "table_comments",
            "comment": "table comment coverage",
            "sql": (
                "SELECT COUNT(*) AS total,\n"
                "       SUM(CASE WHEN comment IS NOT NULL AND comment <> '' THEN 1 ELSE 0 END) AS commented\n"
                "FROM <TABLES> WHERE {schema_filter};"
            ),
            "schema_column": "table_schema",
            "app_impl": "probe_metadata: one scan grouped by catalog/schema (also feeds the drill-down)",
        },
        {
            "id": "column_comments",
            "comment": (
                "column comment coverage: the heaviest read. Run it once per BATCH of\n"
                "catalogs and add the results up (see \"Running column_comments\" below)."
            ),
            "sql": (
                "SELECT table_catalog AS cat, table_schema AS sch, COUNT(*) AS total,\n"
                "       SUM(CASE WHEN comment IS NOT NULL AND comment <> '' THEN 1 ELSE 0 END) AS commented\n"
                "FROM <COLUMNS>\n"
                "WHERE table_catalog IN (<CATALOG_BATCH>) AND {schema_filter}\n"
                "GROUP BY table_catalog, table_schema;"
            ),
            "schema_column": "table_schema",
            # The App runs this exact SQL once per batch (_column_coverage_chunked).
            "shared": True,
            "procedure": [
                "**Never** run this as one query over the whole metastore: on a large estate it "
                "runs for many minutes and times out.",
                "Take the catalogs in scope (the non-internal catalogs from Source resolution, or "
                "the catalogs the user asked for) and count their tables in one query: "
                "`SELECT lower(table_catalog) AS cat, COUNT(*) AS n FROM <TABLES> WHERE "
                "table_schema <> 'information_schema' GROUP BY 1`. Skip catalogs with no tables.",
                "Pack the catalogs into batches by table count: a catalog with more than "
                f"**{METADATA_BATCH_MAX_TABLES:,}** tables is a batch on its own; put the others "
                f"together until the next one would take the batch past {METADATA_BATCH_MAX_TABLES:,} "
                f"tables (in per-catalog mode, also stop at {METADATA_COLUMN_BATCH_SIZE} catalogs). "
                "**Every catalog goes into exactly one batch and is named in that batch's "
                "`IN (...)` list. Never use `NOT IN` to cover \"the rest\"**: that is an unbounded "
                "scan of the whole metastore.",
                "Run the query once per batch, with `<CATALOG_BATCH>` set to that batch's catalog "
                "names, quoted and in **lower case** (`table_catalog` is stored in lower case). "
                "In per-catalog mode, build `<COLUMNS>` from only that batch's catalogs. Run "
                "several batches at the same time if you can.",
                "Add up `total` and `commented` over every row of the batches that succeeded: "
                "those sums are `col_total` and `col_commented`. Keep the per-schema rows; the "
                "drill-down below reuses them, so columns are never scanned twice.",
                "If some batches fail, compute coverage over the catalogs you could read, and add "
                "a note: \"column coverage over N of M catalogs\".",
                "If every batch fails, or no batch finishes for "
                f"**{METADATA_NO_PROGRESS_TIMEOUT_S // 60} minutes**, mark the pillar "
                "**unavailable**. Do not count column coverage as 0.",
                "On a very large estate, suggest that the user limits the assessment to the "
                "catalogs that matter.",
            ],
        },
        {
            "id": "tagged_tables",
            "comment": (
                "governed-tag reach (best-effort; treat failure as unknown). Count by\n"
                "fully-qualified name so same-named tables in different schemas/catalogs\n"
                "are counted separately (matches the Domains pillar)."
            ),
            "sql": (
                "SELECT COUNT(DISTINCT concat_ws('.', catalog_name, schema_name, table_name)) AS tagged_tables\n"
                "FROM <TABLE_TAGS> WHERE {schema_filter};"
            ),
            "schema_column": "schema_name",
            "shared": True,
        },
    ],
    "derived": [
        "`table_pct = pct(table_commented, table_total)`;",
        "`col_pct = pct(col_commented, col_total)`.",
    ],
    "score": f"round({METADATA_TABLE_WEIGHT} * table_pct + {METADATA_COLUMN_WEIGHT} * col_pct, 1)",
    "signals": [
        {"id": "table_comments", "label": "Tables commented", "value": "`table_pct`%"},
        {"id": "column_comments", "label": "Columns commented", "value": "`col_pct`%"},
        {"id": "tagged_tables", "label": "Tagged tables", "value": "`tagged_tables`", "when": "when known"},
    ],
    "gaps": [
        {
            "id": "table_comments_low",
            "when": f"table_pct < {TABLE_COMMENT_TARGET_PCT}",
            "text": "Only {table_pct}% of tables have descriptions — Genie relies on these to understand data.",
        },
        {
            "id": "column_comments_low",
            "when": f"col_pct < {COLUMN_COMMENT_TARGET_PCT}",
            "text": "Only {col_pct}% of columns are commented; aim for high coverage on gold-layer columns.",
        },
        {
            "id": "no_tagged_tables",
            "when": "table_total > 0 and tagged_tables == 0",
            "when_note": "only when the tag read succeeded",
            "text": "No governed tags found; tags aid discovery and domain organization.",
        },
    ],
})

# ---------------------------------------------------------------------------
# 1. Unity Catalog Foundation
# ---------------------------------------------------------------------------
UC_CATALOG_POINTS = 40
UC_TABLE_POINTS = 30
UC_FOOTPRINT_POINTS = 15
UC_SCHEMA_POINTS = 15
# One bar for both the footprint bonus and the "limited footprint" gap.
UC_FOOTPRINT_TABLES = 50
UC_SCHEMA_MIN = 5

UC_FOUNDATION = _index({
    "key": "uc_foundation",
    "queries": [
        {
            "id": "schemas",
            "comment": "schemas (exclude the per-catalog information_schema)",
            "sql": "SELECT COUNT(*) AS n_schemas\nFROM <SCHEMATA>\nWHERE {schema_filter};",
            "schema_column": "schema_name",
            "shared": True,
        },
        {
            "id": "tables",
            "comment": "table footprint + managed share",
            "sql": (
                "SELECT COUNT(*) AS total,\n"
                "       SUM(CASE WHEN table_type IN ('MANAGED','MANAGED_SHALLOW_CLONE') THEN 1 ELSE 0 END) AS managed\n"
                "FROM <TABLES> WHERE {schema_filter};"
            ),
            "shared": True,
        },
        {
            "id": "hive_tables",
            "comment": (
                "legacy (non-UC) footprint still in the workspace-local Hive metastore (best-effort;\n"
                "omit this signal if hive_metastore.information_schema isn't readable — e.g. it\n"
                "raises UC_HIVE_METASTORE_DISABLED_EXCEPTION when legacy access is turned off,\n"
                "which just means non_uc is unknown, NOT that the pillar is unavailable)"
            ),
            "sql": (
                "SELECT COUNT(*) AS non_uc\n"
                "FROM hive_metastore.information_schema.tables\n"
                "WHERE table_schema <> 'information_schema';"
            ),
            "shared": True,
        },
    ],
    "derived": [
        "`n_catalogs` = user (non-internal) catalogs in scope.",
        "If `non_uc` is readable: `uc_coverage_pct = pct(total, total + non_uc)`.",
    ],
    "score": [
        "Start at 0, then:",
        f"- `+{UC_CATALOG_POINTS}` if `n_catalogs > 0`;",
        f"- `+{UC_TABLE_POINTS}` if `total > 0`;",
        f"- `+{UC_FOOTPRINT_POINTS}` if `total >= {UC_FOOTPRINT_TABLES}`;",
        f"- `+{UC_SCHEMA_POINTS}` if `n_schemas >= {UC_SCHEMA_MIN}`.",
        "",
        "Cap the result at 100.",
    ],
    "signals": [
        {"id": "catalogs", "label": "Catalogs", "value": "`n_catalogs`"},
        {"id": "schemas", "label": "Schemas", "value": "`n_schemas`"},
        {"id": "managed", "label": "Managed", "value": "`pct(managed, total)`%"},
        {"id": "in_uc", "label": "In Unity Catalog", "value": "`uc_coverage_pct`%",
         "when": "when `non_uc` is known"},
        {"id": "not_in_uc", "label": "Not in Unity Catalog", "value": "`non_uc` tables",
         "when": "when `non_uc` is known"},
    ],
    "gaps": [
        {
            "id": "no_user_catalogs",
            "when": "n_catalogs == 0",
            "text": "No user catalogs found — Unity Catalog may not be in active use.",
        },
        {
            "id": "small_table_footprint",
            "when": f"total < {UC_FOOTPRINT_TABLES}",
            "text": "Limited table footprint; broaden UC adoption beyond an initial workload.",
        },
        {
            "id": "hive_metastore_tables",
            "when": "non_uc > 0",
            "when_note": "only when the hive_metastore read succeeded",
            "text": ("{non_uc} table(s) ({not_uc_pct}%) are still in the legacy hive_metastore "
                     "(not in Unity Catalog) — migrate them into UC (see the UCX accelerator)."),
        },
    ],
})

# ---------------------------------------------------------------------------
# 3. Relationships & Modeling
# ---------------------------------------------------------------------------
REL_GOLD_POINTS = 50
REL_FK_POINTS = 35
REL_PK_POINTS = 15
# Gold / mart layer, detected by schema or table naming. The App's drill-down uses the same two.
GOLD_SCHEMA_PATTERN = "(gold|mart|marts|analytics|semantic|presentation|reporting|dwh)"
GOLD_TABLE_PATTERN = "^(gold_|mart_|dim_|fact_)"

REL_MODELING = _index({
    "key": "relationships",
    "queries": [
        {
            "id": "constraints",
            "comment": ("declared constraints (if table_constraints isn't available, note it and score "
                        "gold-layer only)"),
            "sql": (
                "SELECT constraint_type, COUNT(*) AS n\n"
                "FROM <TABLE_CONSTRAINTS>\n"
                "WHERE {schema_filter}\n"
                "GROUP BY constraint_type;"
            ),
            "shared": True,
        },
        {
            "id": "gold_tables",
            "comment": "curated gold/mart layer, detected by schema or table naming",
            "sql": (
                "SELECT COUNT(*) AS gold_tables\n"
                "FROM <TABLES>\n"
                "WHERE {schema_filter}\n"
                f"  AND (lower(table_schema) RLIKE '{GOLD_SCHEMA_PATTERN}'\n"
                f"       OR lower(table_name) RLIKE '{GOLD_TABLE_PATTERN}');"
            ),
            "shared": True,
        },
    ],
    "derived": [
        "`pk` = count of `'PRIMARY KEY'` rows; `fk` = count of `'FOREIGN KEY'` rows.",
    ],
    "score": [
        "Start at 0, then:",
        f"- `+{REL_GOLD_POINTS}` if `gold_tables > 0`;",
        f"- if constraints are available: `+{REL_FK_POINTS}` if `fk > 0`; `+{REL_PK_POINTS}` if `pk > 0`.",
        "",
        "Cap the result at 100.",
    ],
    "signals": [
        {"id": "gold_tables", "label": "Gold-layer tables", "value": "`gold_tables`"},
        {"id": "pk", "label": "Primary keys", "value": "`pk`", "when": "when constraints are available"},
        {"id": "fk", "label": "Foreign keys", "value": "`fk`", "when": "when constraints are available"},
    ],
    "gaps": [
        {
            "id": "no_gold_layer",
            "when": "gold_tables == 0",
            "text": "No clearly-named gold/mart layer detected; Genie performs best on curated, pre-joined tables.",
        },
        {
            "id": "no_fk_constraints",
            "when": "constraints_available and fk == 0",
            "when_note": "only when constraint metadata is readable",
            "text": "No foreign-key constraints declared; PK/FK relationships let Genie infer joins reliably.",
        },
    ],
})

# ---------------------------------------------------------------------------
# 4. Metrics (metric views)
# ---------------------------------------------------------------------------
# table_type is spelled either way on different runtimes; the App tries each in turn.
METRIC_VIEW_TYPES = ("METRIC_VIEW", "METRIC VIEW")
METRIC_BASE_POINTS = 30
METRIC_RAMP_POINTS = 40
METRIC_RAMP_VIEWS = 10
METRIC_QUALITY_POINTS = 30
METRIC_FEW_VIEWS = 3

METRICS = _index({
    "key": "metrics",
    "queries": [
        {
            "id": "metric_views",
            "comment": ("metric view count (table_type spelling varies by runtime: try 'METRIC_VIEW' "
                        "then 'METRIC VIEW')"),
            "sql": (
                "SELECT COUNT(*) AS metric_views\n"
                "FROM <TABLES>\n"
                "WHERE table_type = 'METRIC_VIEW' AND {schema_filter};"
            ),
            "shared": True,
        },
        {
            "id": "commented",
            "comment": "described metric views (a read that fails counts as 0)",
            "sql": (
                "SELECT COUNT(*) AS commented\n"
                "FROM <TABLES>\n"
                "WHERE table_type = 'METRIC_VIEW' AND comment IS NOT NULL AND trim(comment) <> '' "
                "AND {schema_filter};"
            ),
            "shared": True,
        },
    ],
    "derived": [
        "`uncommented = metric_views - commented`.",
    ],
    "score": [
        "If `metric_views == 0`, the score is **0** and only the gap below applies.",
        "",
        "Otherwise:",
        "",
        ("code",
         f"{METRIC_BASE_POINTS} + {METRIC_RAMP_POINTS} * min(metric_views, {METRIC_RAMP_VIEWS}) / "
         f"{METRIC_RAMP_VIEWS} + {METRIC_QUALITY_POINTS} * (commented / metric_views)",
         ", rounded to 1 decimal and capped at 100."),
    ],
    "signals": [
        {"id": "metric_views", "label": "Metric views", "value": "`metric_views`"},
        {"id": "commented", "label": "Commented", "value": "`pct(commented, metric_views)`%",
         "when": "when `metric_views > 0`"},
    ],
    "gaps": [
        {
            "id": "no_metric_views",
            "when": "metric_views == 0",
            "text": ("No metric views found. Metric views are the GA foundation that feeds Genie Ontology — "
                     "define KPIs centrally here."),
        },
        {
            "id": "few_metric_views",
            "when": f"0 < metric_views < {METRIC_FEW_VIEWS}",
            "text": "Few metric views; expand coverage so common KPIs are centrally defined and certified.",
        },
        {
            "id": "metric_views_uncommented",
            "when": "uncommented > 0",
            "text": ("{uncommented} metric view(s) lack a description — Genie reads metric-view, dimension, "
                     "and measure comments to reason; add them."),
        },
    ],
})

# ---------------------------------------------------------------------------
# 5. Genie Agents (graded real-usage score from the audit log)
# ---------------------------------------------------------------------------
# Existence: every space with aibiGenie audit events over this full lookback (not
# trashed). active_30d / questions_30d / distinct_users_30d are measured over the
# shorter 30-day window; a stale agent is counted but is not active (#120).
GENIE_AUDIT_LOOKBACK_DAYS = 90
GENIE_ACTIVE_WINDOW_DAYS = 30
# Resolve each space's display_name over a wider window (names only appear on
# create/update events, not on query/conversation events that drive 30-day usage).
GENIE_NAME_LOOKBACK_DAYS = 90

# Real usage is measured from CONVERSATION-bearing actions only, NOT raw audit
# events. Raw events are dominated by genieGetSpace polling and service/eval
# accounts, which overstate usage (#24). An agent is "active" when it has at
# least one of these actions in the 30-day window; questions_30d counts them and
# distinct_users_30d counts the distinct user emails behind them.
GENIE_CONVERSATION_ACTIONS = (
    "genieStartConversationMessage",
    "createConversationMessage",
    "genieCreateConversationMessage",
)
# Best-effort agent-type classification: a space is DASHBOARD-EMBEDDED (an AI/BI
# Dashboard agent) when its space_id appears in the response.result of one of
# these dashboard actions. Absence of that evidence is the DEFAULT — a space with
# no such row is classified STANDALONE (an absence-of-evidence default, labeled as
# such, not a positive marker). Dashboard-embedded agents are classified out of
# the scored population and counted separately so they don't skew the pillar.
GENIE_DASHBOARD_ACTIONS = ("getDashboardGenie", "updateDashboardGenie")

# Graded usage bands (replace the old binary +40/+40/+20 count thresholds). Each
# band is a fixed tier so day-to-day drift rarely moves the score; the probe adds
# the highest tier reached in each band and caps the total at 100.
GENIE_EXISTENCE_POINTS = 15.0
# active_30d / total (share of standalone agents active in the window).
GENIE_SHARE_BANDS = ((0.1, 8.0), (0.3, 16.0), (0.6, 25.0))
# distinct users who asked a question across standalone agents (30d).
GENIE_USER_BANDS = ((1, 8.0), (5, 16.0), (20, 25.0))
# conversation questions across standalone agents (30d).
GENIE_QUESTION_BANDS = ((1, 7.0), (25, 14.0), (100, 20.0))
# recency: days since the most recent standalone-agent activity (<= each tier).
GENIE_RECENCY_BANDS = ((7, 15.0), (14, 10.0), (30, 5.0))


def _genie_band(value, bands) -> float:
    """Points for ``value`` from an ascending ``(at_least, points)`` band table
    (the highest tier whose threshold ``value`` meets)."""
    points = 0.0
    for at_least, pts in bands:
        if (value or 0) >= at_least:
            points = pts
    return points


def genie_recency_points(days) -> float:
    """Points for recency from ``GENIE_RECENCY_BANDS`` (days-since-last-activity;
    fresher earns more). ``None`` (no activity / unknown) earns 0."""
    if days is None:
        return 0.0
    points = 0.0
    for within, pts in GENIE_RECENCY_BANDS:
        if days <= within:
            points = max(points, pts)
    return points


def genie_usage_score(total, active_30d, distinct_users_30d, questions_30d, recency_days) -> float:
    """Deterministic graded usage score (0..100) for the standalone Genie-agent
    population. ``total == 0`` (no standalone agents) scores 0."""
    if not total:
        return 0.0
    score = GENIE_EXISTENCE_POINTS
    share = (active_30d or 0) / total
    score += _genie_band(share, GENIE_SHARE_BANDS)
    score += _genie_band(distinct_users_30d, GENIE_USER_BANDS)
    score += _genie_band(questions_30d, GENIE_QUESTION_BANDS)
    score += genie_recency_points(recency_days)
    return round(min(score, 100.0), 1)


def _sql_action_list(actions) -> str:
    """Lowercased, quoted SQL IN-list for ``lower(action_name) IN (...)``."""
    return ", ".join("'" + a.lower().replace("'", "''") + "'" for a in actions)


def _band_doc(bands, unit: str = "") -> str:
    return ", ".join(f"`+{pts:g}` at `>= {at:g}{unit}`" for at, pts in bands)


_GENIE_CONV_SQL = _sql_action_list(GENIE_CONVERSATION_ACTIONS)
_GENIE_DASH_SQL = _sql_action_list(GENIE_DASHBOARD_ACTIONS)

GENIE_AGENTS = _index({
    "key": "genie_agents",
    "queries": [
        {
            "id": "audit_counts",
            "comment": (
                "Graded usage for STANDALONE Genie Agents, from the audit log over the last "
                f"{GENIE_AUDIT_LOOKBACK_DAYS} days (aibiGenie), minus trashed spaces. A space is "
                "DASHBOARD-EMBEDDED (counted separately, not scored) when its space_id EXACTLY equals "
                "the space_id/genie_space_id value parsed from the response.result JSON of a "
                "getDashboardGenie/updateDashboardGenie row (exact match, not a substring); otherwise "
                "STANDALONE (absence-of-evidence default). Real usage is measured from CONVERSATION "
                "actions only (not raw events, which overstate via polling): questions_30d, "
                f"distinct_users_30d and active_30d are over the last {GENIE_ACTIVE_WINDOW_DAYS} days."
            ),
            "sql": (
                "WITH genie_events AS (\n"
                "  SELECT request_params.space_id AS space_id, action_name, event_date,\n"
                "         user_identity.email AS user_email\n"
                "  FROM system.access.audit\n"
                "  WHERE service_name = 'aibiGenie'\n"
                "    AND request_params.space_id IS NOT NULL\n"
                "    AND request_params.space_id <> 'new'\n"
                f"    AND event_date >= current_date() - INTERVAL {GENIE_AUDIT_LOOKBACK_DAYS} DAYS"
                "{workspace_filter}\n"
                "),\n"
                "dashboard_refs AS (\n"
                "  SELECT get_json_object(response.result, '$.space_id') AS sid1,\n"
                "         get_json_object(response.result, '$.genie_space_id') AS sid2\n"
                "  FROM system.access.audit\n"
                f"  WHERE lower(action_name) IN ({_GENIE_DASH_SQL})\n"
                "    AND response.result IS NOT NULL\n"
                f"    AND event_date >= current_date() - INTERVAL {GENIE_AUDIT_LOOKBACK_DAYS} DAYS"
                "{workspace_filter}\n"
                "),\n"
                "dashboard_space_ids AS (\n"
                "  SELECT sid1 AS space_id FROM dashboard_refs WHERE sid1 IS NOT NULL\n"
                "  UNION SELECT sid2 FROM dashboard_refs WHERE sid2 IS NOT NULL\n"
                "),\n"
                "per_space AS (\n"
                "  SELECT space_id,\n"
                "         MAX(CASE WHEN lower(action_name) = 'trashspace' THEN 1 ELSE 0 END) AS trashed,\n"
                f"         SUM(CASE WHEN lower(action_name) IN ({_GENIE_CONV_SQL})\n"
                f"                   AND event_date >= current_date() - INTERVAL {GENIE_ACTIVE_WINDOW_DAYS} DAYS\n"
                "                  THEN 1 ELSE 0 END) AS questions_30d,\n"
                "         MAX(event_date) AS last_active_date\n"
                "  FROM genie_events GROUP BY space_id\n"
                "),\n"
                "classified AS (\n"
                "  SELECT space_id, trashed, questions_30d, last_active_date,\n"
                "         CASE WHEN space_id IN (SELECT space_id FROM dashboard_space_ids)\n"
                "              THEN 1 ELSE 0 END AS dashboard_embedded\n"
                "  FROM per_space\n"
                "),\n"
                "standalone AS (\n"
                "  SELECT * FROM classified WHERE trashed = 0 AND dashboard_embedded = 0\n"
                ")\n"
                "SELECT\n"
                "  (SELECT COUNT(*) FROM standalone) AS total,\n"
                "  (SELECT COUNT(*) FROM standalone WHERE questions_30d > 0) AS active_30d,\n"
                "  (SELECT COALESCE(SUM(questions_30d), 0) FROM standalone) AS questions_30d,\n"
                "  (SELECT COUNT(DISTINCT e.user_email) FROM genie_events e\n"
                f"     WHERE lower(e.action_name) IN ({_GENIE_CONV_SQL})\n"
                f"       AND e.event_date >= current_date() - INTERVAL {GENIE_ACTIVE_WINDOW_DAYS} DAYS\n"
                "       AND e.space_id IN (SELECT space_id FROM standalone)) AS distinct_users_30d,\n"
                "  (SELECT MAX(last_active_date) FROM standalone) AS last_active_date,\n"
                "  (SELECT COUNT(*) FROM classified WHERE trashed = 0 AND dashboard_embedded = 1)"
                " AS dashboard_embedded;"
            ),
            "shared": True,
            "app_impl": "adds the viewer's workspace filter as bound parameters; derives recency_days "
                        "from last_active_date",
        },
    ],
    "derived": [
        "`recency_days` = days since `last_active_date` (the most recent standalone-agent event);",
        "`active_share` = `active_30d / total` when `total > 0`.",
    ],
    "score": [
        "If no standalone Genie Agent exists (`total == 0`), the score is **0** and only the "
        "`no_genie_agents` gap applies.",
        "",
        f"Otherwise start at `+{GENIE_EXISTENCE_POINTS:g}` for having a standalone agent, then add the "
        "highest tier reached in each graded real-usage band (capped at 100):",
        "",
        f"- **Active share** (`active_30d / total`): {_band_doc(GENIE_SHARE_BANDS)};",
        f"- **Distinct users (30d)**: {_band_doc(GENIE_USER_BANDS)};",
        f"- **Questions (30d)**: {_band_doc(GENIE_QUESTION_BANDS)};",
        "- **Recency** (days since last activity): "
        + ", ".join(f"`+{pts:g}` within {within}d" for within, pts in GENIE_RECENCY_BANDS) + ".",
        "",
        "Dashboard-embedded agents (AI/BI Dashboard agents) are classified out of this population "
        "and counted separately; they never affect the score.",
    ],
    "signals": [
        {"id": "total", "label": "Genie Agents (standalone)", "value": "`total`"},
        {"id": "active_30d", "label": "Active agents (30d)", "value": "`active_30d`"},
        {"id": "distinct_users_30d", "label": "Users (30d)", "value": "`distinct_users_30d`"},
        {"id": "questions_30d", "label": "Questions (30d)", "value": "`questions_30d`"},
        {"id": "dashboard_embedded", "label": "Dashboard-embedded", "value": "`dashboard_embedded`",
         "when": "counted separately; not scored in this pillar"},
    ],
    "gaps": [
        {
            "id": "no_genie_agents",
            "when": "total == 0",
            "text": ("No standalone Genie Agents found in the audit log — create a curated Genie Agent "
                     "as the entry point to natural-language analytics."),
        },
        {
            "id": "no_active_genie_agents",
            "when": "total > 0 and active_30d == 0",
            "text": ("{total} standalone Genie Agent(s) exist but none had a conversation in the last "
                     f"{GENIE_ACTIVE_WINDOW_DAYS} days — drive adoption or retire stale agents."),
        },
    ],
})

# ---------------------------------------------------------------------------
# 7. Adoption & Activity (system tables)
# ---------------------------------------------------------------------------
ADOPTION_WINDOW_DAYS = 30
ADOPTION_QUERY_POINTS = 50
# (at least this many active users) -> points. Fixed tiers, so day-to-day drift in the
# user count rarely moves the score. Below the first tier (no active users) the band is 0.
ADOPTION_USER_BANDS = ((1, 20.0), (5, 35.0), (20, 45.0), (50, 50.0))


def adoption_band_points(users) -> float:
    """Points for the 30-day active-user count, from ``ADOPTION_USER_BANDS``."""
    points = 0.0
    for at_least, pts in ADOPTION_USER_BANDS:
        if (users or 0) >= at_least:
            points = pts
    return points


ADOPTION = _index({
    "key": "adoption",
    "queries": [
        {
            "id": "active_users",
            "comment": "active users, last 30 days (system.access.audit)",
            "sql": (
                "SELECT COUNT(DISTINCT user_identity.email) AS active_users\n"
                "FROM system.access.audit\n"
                f"WHERE event_date >= current_date() - INTERVAL {ADOPTION_WINDOW_DAYS} DAYS{{workspace_filter}};"
            ),
            "shared": True,
            "app_impl": "scoped to the workspaces in the active filter (bound parameters)",
        },
        {
            "id": "queries_30d",
            "comment": "query volume, last 30 days (system.query.history)",
            "sql": (
                "SELECT COUNT(*) AS queries_30d\n"
                "FROM system.query.history\n"
                f"WHERE start_time >= current_timestamp() - INTERVAL {ADOPTION_WINDOW_DAYS} DAYS{{workspace_filter}};"
            ),
            "shared": True,
            "app_impl": "scoped to the workspaces in the active filter (bound parameters)",
        },
    ],
    "derived": [],
    "score": [
        "Start at 0, then add the band for `active_users`:",
        "",
        ("bands",),  # the band table; the skill renders it from ADOPTION_USER_BANDS
        "",
        f"Then add `+{ADOPTION_QUERY_POINTS}` if `queries_30d > 0`. Cap the result at 100.",
    ],
    "signals": [
        {"id": "active_users", "label": "Active users (30d)", "value": "`active_users`", "when": "when readable"},
        {"id": "queries_30d", "label": "Queries (30d)", "value": "`queries_30d`", "when": "when readable"},
    ],
    "gaps": [],
})

PILLAR_SPECS = {p["key"]: p for p in (METADATA, UC_FOUNDATION, REL_MODELING, METRICS, GENIE_AGENTS, ADOPTION)}
