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

Pillars migrate here one at a time; the rest still live inline in probes.py and
hand-written in the skill until they move.
"""

import re

# The default schema predicate when no schema allowlist is in force.
DEFAULT_SCHEMA_FILTER = "{column} <> 'information_schema'"


def render_sql(template: str, sources: dict[str, str] | None = None, schema_filter: str | None = None,
               schema_column: str = "table_schema") -> str:
    """Fill a spec SQL template.

    ``sources`` maps a placeholder (``"<TABLE_TAGS>"``) to a FROM-able source; leave it
    out to keep the placeholder (the skill's form). ``schema_filter`` defaults to the
    information_schema exclusion on ``schema_column``.
    """
    values = dict(sources or {})
    values["{schema_filter}"] = (
        schema_filter if schema_filter is not None else DEFAULT_SCHEMA_FILTER.format(column=schema_column)
    )
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
                "column comment coverage (heaviest read; on a wide metastore, scan in\n"
                "per-catalog batches and sum, rather than one metastore-wide query that times out)"
            ),
            "sql": (
                "SELECT COUNT(*) AS total,\n"
                "       SUM(CASE WHEN comment IS NOT NULL AND comment <> '' THEN 1 ELSE 0 END) AS commented\n"
                "FROM <COLUMNS> WHERE {schema_filter};"
            ),
            "schema_column": "table_schema",
            "app_impl": "_column_coverage_chunked: same counts, scanned in per-catalog batches",
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

PILLAR_SPECS = {p["key"]: p for p in (METADATA,)}
