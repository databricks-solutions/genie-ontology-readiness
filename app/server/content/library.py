"""Embedded enablement content — ships with the app (no internet/Glean at runtime).

For each capability: a concise "what it is", technical + business value, clear
recommendations, best practices, and PUBLIC Databricks documentation sources only.

Product accuracy to preserve (without release-stage labels):
  - Unity Catalog *Business Semantics* (metric views, Pages, domains,
    synonyms, certification) is the customer-built, governed foundation.
  - *Genie Ontology* is the continuously-LEARNED enterprise context layer that
    Databricks builds on top of that foundation. The foundation FEEDS the
    ontology — preparing for Genie Ontology means maturing the foundation.
"""

# Each capability is keyed; pillars reference these via pillars.PILLARS[*]["capability"].
CAPABILITIES: dict[str, dict] = {
    "ontology": {
        "name": "Genie Ontology — the big picture",
        "tagline": "A learned enterprise context layer built on your governed semantics.",
        "what": (
            "Genie Ontology is the business-aware context layer that lets Genie answer from "
            "your authoritative source. It brings together two kinds of context: the semantics "
            "you MODEL and govern in Unity Catalog (metric views, domains, Pages, "
            "synonyms, certified assets) and context Genie LEARNS from the assets you already "
            "have (dashboards, saved queries, Genie Agents, notebooks). It ranks every signal by "
            "authority — things like certification, popularity, freshness, and ownership — so it "
            "answers from the source that is actually right. The modeled foundation FEEDS the "
            "learned layer, so preparing for Genie Ontology means maturing that foundation: "
            "governance, metadata, metric views, domains, Pages, and curated Genie Agents."
        ),
        "technical_value": "One governed meaning, reused across Genie, SQL, notebooks, dashboards, and BI — instead of definitions fragmented across tools.",
        "business_value": "Business users and AI agents give the same trustworthy answer to the same question. The foundation work pays off in AI/BI and Genie accuracy immediately.",
        "technical_enablement": [
            "Mature the foundation first: UC governance, rich metadata, metric views, curated Genie Agents.",
            "Engage your Databricks account team about Genie Ontology eligibility.",
        ],
        "business_adoption": [
            "Frame this as a semantic-standardization program, not a tool rollout.",
            "Name owners for metrics, Pages, and domains before scaling.",
        ],
        "best_practices": [
            "Treat 'preparing for Genie Ontology' as maturing UC Business Semantics + governance + Genie Agents.",
            "The foundation work improves AI/BI accuracy today — don't wait.",
        ],
        "sources": [
            {"title": "Introducing Genie One, Genie Ontology, and Genie Agents (blog)", "url": "https://www.databricks.com/blog/introducing-genie-one-genie-ontology-and-genie-agents"},
            {"title": "AI/BI Genie (docs)", "url": "https://docs.databricks.com/aws/en/genie/"},
        ],
    },
    "unity_catalog": {
        "name": "Unity Catalog Foundation",
        "tagline": "The governance foundation everything else builds on.",
        "what": "Unity Catalog is the unified governance layer for data and AI — a three-level namespace with centralized access control, lineage, auditing, and discovery that everything above it inherits.",
        "technical_value": "Centralized ACLs, lineage, and audit across all workspaces; one source of truth for permissions.",
        "business_value": "Trustworthy, governed data and AI — the prerequisite for letting business users self-serve safely.",
        "technical_enablement": [
            "Enable Unity Catalog and assign a metastore to the workspace.",
            "Migrate Hive-metastore tables into UC catalogs/schemas.",
            "Grant access via SCIM/identity groups, not individuals.",
            "Enable system schemas (information_schema, access, query).",
        ],
        "business_adoption": [
            "Name an accountable UC governance owner.",
            "Drive adoption org-wide, beyond a single team or POC.",
            "Define a small set of access groups aligned to business roles.",
        ],
        "best_practices": [
            "Grant access through groups, not individual users.",
            "Standardize a catalog/schema naming convention (by domain + medallion layer).",
            "Enable system tables so readiness and usage can be measured.",
        ],
        "sources": [
            {"title": "What is Unity Catalog? (docs)", "url": "https://docs.databricks.com/aws/en/data-governance/unity-catalog/"},
        ],
        "queries": [
            {
                "title": "Count tables in vs. not in Unity Catalog (coverage %)",
                "sql": (
                    "WITH uc AS (\n"
                    "  SELECT COUNT(*) AS n FROM system.information_schema.tables\n"
                    "  WHERE table_schema <> 'information_schema'\n"
                    "),\n"
                    "legacy AS (\n"
                    "  SELECT COUNT(*) AS n FROM hive_metastore.information_schema.tables\n"
                    "  WHERE table_schema <> 'information_schema'\n"
                    ")\n"
                    "SELECT uc.n     AS uc_tables,\n"
                    "       legacy.n AS non_uc_tables,\n"
                    "       ROUND(100.0 * uc.n / NULLIF(uc.n + legacy.n, 0), 1) AS pct_in_unity_catalog\n"
                    "FROM uc, legacy;"
                ),
            },
            {
                "title": "List tables NOT in Unity Catalog (legacy hive_metastore)",
                "sql": (
                    "SELECT table_schema, table_name, table_type\n"
                    "FROM hive_metastore.information_schema.tables\n"
                    "WHERE table_schema <> 'information_schema'\n"
                    "ORDER BY table_schema, table_name;\n"
                    "-- If hive_metastore.information_schema is unavailable, enumerate instead:\n"
                    "--   SHOW SCHEMAS IN hive_metastore;\n"
                    "--   SHOW TABLES IN hive_metastore.`<schema>`;"
                ),
            },
        ],
    },
    "metadata": {
        "name": "Metadata Richness",
        "tagline": "The comments and tags Genie reads to understand your data.",
        "what": "Table and column comments, descriptions, and governed tags. Genie maps natural-language questions to the right tables and columns using this metadata, so its quality strongly predicts answer accuracy.",
        "technical_value": "Higher first-attempt accuracy; less manual instruction-writing in each Genie Agent.",
        "business_value": "Faster, more trustworthy self-serve answers and better discovery in Catalog Explorer.",
        "technical_enablement": [
            "Add COMMENT to every gold-layer table and column.",
            "Use AI-generated comments as a first draft, then have stewards refine them.",
            "Apply governed tags/classifications (PII, domain, certified).",
        ],
        "business_adoption": [
            "Write descriptions in business language, not source-system jargon.",
            "Require metadata on new assets as part of the definition-of-done.",
            "Prioritize the gold/analytics layer first.",
        ],
        "best_practices": [
            "Target high comment coverage on gold tables/columns before standing up Genie.",
            "Add synonyms / business terms users actually say.",
            "Review AI-generated comments — don't ship them unedited.",
        ],
        "sources": [
            {"title": "Add comments to data and AI assets (docs)", "url": "https://docs.databricks.com/aws/en/comments/"},
            {"title": "Add AI-generated comments (docs)", "url": "https://docs.databricks.com/aws/en/comments/ai-comments"},
        ],
    },
    "relationships": {
        "name": "Relationships & Modeling",
        "tagline": "Keys and a curated gold layer Genie can join reliably.",
        "what": "Declared primary/foreign-key relationships and a curated, query-ready gold layer (often pre-joined dimensional models). Declared keys let Genie infer correct join paths instead of guessing.",
        "technical_value": "Reliable join inference; fewer wrong-cardinality answers.",
        "business_value": "Analytics-ready data, so business questions resolve without data-engineering tickets.",
        "technical_enablement": [
            "Declare PRIMARY KEY / FOREIGN KEY constraints on gold fact and dimension tables.",
            "Build a curated gold layer (star/snowflake) for analytics consumption.",
            "Document common join paths.",
        ],
        "business_adoption": [
            "Designate which tables are the certified, analytics-ready gold layer.",
            "Avoid pointing Genie at raw bronze/silver tables.",
        ],
        "best_practices": [
            "Pre-join where it simplifies the model for business questions.",
            "Ensure stable identifiers and a clean grain on fact tables.",
        ],
        "sources": [
            {"title": "Constraints on Databricks (docs)", "url": "https://docs.databricks.com/aws/en/tables/constraints"},
        ],
    },
    "metric_views": {
        "name": "Metric Views",
        "tagline": "Centrally-defined, certified KPIs — the core of the semantic foundation.",
        "what": "Unity Catalog metric views define measures, dimensions, filters, and joins once, in governed YAML, and reuse them across Genie, dashboards, SQL, notebooks, and external BI. This is the central pillar of the semantic foundation that feeds Genie Ontology.",
        "technical_value": "One definition of each KPI consumed everywhere; no metric drift between BI and Genie.",
        "business_value": "Everyone — and every AI agent — computes revenue, churn, and margin the same certified way.",
        "technical_enablement": [
            "Define metric views in YAML for your top KPIs (measures, dimensions, joins).",
            "Add synonyms, display names, and formatting for agent-friendliness.",
            "Use the same metric views in dashboards and Genie Agents.",
        ],
        "business_adoption": [
            "Have business owners define the 'what' of each KPI, not just IT the 'how'.",
            "Certify and version metric definitions; assign an owner to each.",
            "Start with the 10–20 KPIs the business argues about most.",
        ],
        "best_practices": [
            "Centralize KPI logic in metric views, not repeated across dashboards/queries.",
            "Certify metrics and record an owner for each.",
            "Add synonyms reflecting how the business actually speaks.",
        ],
        "sources": [
            {"title": "Unity Catalog metric views (docs)", "url": "https://docs.databricks.com/aws/en/business-semantics/metric-views/"},
            {"title": "Redefining the semantics layer for BI and AI (blog)", "url": "https://www.databricks.com/blog/redefining-semantics-data-layer-future-bi-and-ai"},
        ],
    },
    "genie_agents": {
        "name": "Genie Agents",
        "tagline": "Curated natural-language analytics over your governed data.",
        "what": "A Genie Agent is a curated room where business users ask questions in natural language. Curation — instructions, example/verified SQL, benchmark questions, and the tables/metric views in scope — is what makes answers accurate.",
        "technical_value": "Scoped, governed context for the model; verified queries and benchmarks raise accuracy.",
        "business_value": "Self-serve answers for business users without writing SQL; faster decisions.",
        "technical_enablement": [
            "Create a Genie Agent scoped to a domain's gold tables and metric views.",
            "Add instructions, example/verified SQL, and benchmark questions.",
            "Test answer quality and iterate on the instructions.",
        ],
        "business_adoption": [
            "Onboard real business users and capture their actual questions.",
            "Run a feedback loop to refine instructions and verified queries.",
            "Scope agents by business domain, not the whole catalog.",
        ],
        "best_practices": [
            "Prefer gold/semantic objects; give the agent a tight, well-described scope.",
            "Maintain a benchmark question set to catch regressions as you tune.",
            "Curate instructions/examples — an uncurated agent answers poorly.",
        ],
        "sources": [
            {"title": "Curate an effective Genie agent (docs)", "url": "https://docs.databricks.com/aws/en/genie/best-practices"},
            {"title": "Set up a Genie agent (docs)", "url": "https://docs.databricks.com/aws/en/genie/set-up"},
        ],
    },
    "domains": {
        "name": "Domains & Stewardship",
        "tagline": "Business-aligned groupings with named stewards and certification.",
        "what": "Domains organize data and AI assets into business-aligned groups, giving agents scoped context plus stewardship and certification signals. Before the native Domains feature, the same structure can be expressed with a governed `domain` tag and an `owner`/`steward` tag.",
        "technical_value": "Scoped context for agents beats full-catalog access; certification signals raise the authority of trusted assets.",
        "business_value": "Clear ownership and trust, and an internal marketplace where teams find certified, governed assets.",
        "technical_enablement": [
            "Define domains around business capabilities (e.g. Sales, Risk, Supply Chain).",
            "Assign assets (catalogs/schemas/tables/metric views) to domains.",
            "Mark certified assets so authority is signaled to agents.",
        ],
        "business_adoption": [
            "Design domains around business capabilities — NOT org charts or source systems.",
            "Name a steward per domain accountable for quality and certification.",
            "Keep domains stable; they are a shared vocabulary, not a reorg artifact.",
        ],
        "best_practices": [
            "Capability-aligned domains, each with one accountable steward.",
            "Certify the canonical assets in each domain so trusted answers win.",
            "Certify your most-accessed resources first — high-traffic tables are where certification most improves Genie/ontology accuracy (this app flags how many of your top-10 most-accessed resources are certified).",
            "Start with 3–5 high-value domains rather than boiling the ocean.",
            "Before the native feature, organize assets with a governed `domain` tag and an `owner`/`steward` tag — this app assesses both.",
        ],
        "sources": [
            {"title": "Governed tags (docs)", "url": "https://docs.databricks.com/aws/en/admin/governed-tags"},
            {"title": "Data classification (docs)", "url": "https://docs.databricks.com/aws/en/data-governance/unity-catalog/data-classification"},
            {"title": "Flag data as certified or deprecated (docs)", "url": "https://docs.databricks.com/aws/en/data-governance/unity-catalog/certify-deprecate-data"},
        ],
        "queries": [
            {
                "title": "Popularity discovery — most-used tables by distinct users (last 90 days)",
                "sql": (
                    "-- Popularity = distinct users (COUNT(DISTINCT created_by)). COUNT(*) or distinct\n"
                    "-- runs are inflated by streaming/automated jobs (one job can log millions of\n"
                    "-- reads); distinct users reflects real breadth of use. System catalogs excluded.\n"
                    "SELECT source_table_full_name AS table_name,\n"
                    "       COUNT(DISTINCT created_by) AS distinct_users,\n"
                    "       COUNT(DISTINCT entity_run_id) AS read_runs\n"
                    "FROM system.access.table_lineage\n"
                    "WHERE source_table_full_name IS NOT NULL\n"
                    "  AND source_table_catalog NOT IN ('system','__databricks_internal','samples')\n"
                    "  AND source_table_schema <> 'information_schema'\n"
                    "  AND event_date >= current_date() - INTERVAL 90 DAYS\n"
                    "GROUP BY source_table_full_name\n"
                    "ORDER BY distinct_users DESC\n"
                    "LIMIT 25;"
                ),
            },
            {
                "title": "Are your most-used tables certified? (popularity + certification)",
                "sql": (
                    "WITH top AS (\n"
                    "  SELECT source_table_full_name AS table_name, COUNT(DISTINCT created_by) AS distinct_users\n"
                    "  FROM system.access.table_lineage\n"
                    "  WHERE source_table_full_name IS NOT NULL\n"
                    "    AND source_table_catalog NOT IN ('system','__databricks_internal','samples')\n"
                    "    AND source_table_schema <> 'information_schema'\n"
                    "    AND event_date >= current_date() - INTERVAL 90 DAYS\n"
                    "  GROUP BY source_table_full_name\n"
                    "  ORDER BY distinct_users DESC LIMIT 10\n"
                    "),\ncertified AS (\n"
                    "  SELECT concat_ws('.', catalog_name, schema_name, table_name) AS table_name\n"
                    "  FROM system.information_schema.table_tags\n"
                    "  WHERE lower(tag_name) = 'system.certification_status'\n"
                    "    AND lower(tag_value) = 'certified'\n"
                    ")\nSELECT t.table_name, t.distinct_users,\n"
                    "       (c.table_name IS NOT NULL) AS is_certified\n"
                    "FROM top t LEFT JOIN certified c ON t.table_name = c.table_name\n"
                    "ORDER BY t.distinct_users DESC;"
                ),
            },
        ],
    },
    "adoption": {
        "name": "Adoption & Activity",
        "tagline": "Real usage, sponsorship, and the roles that sustain it.",
        "what": "Sustained value depends on people: active users querying in natural language, an executive sponsor, defined steward/owner roles, and a feedback loop that keeps semantics and Genie answers accurate over time.",
        "technical_value": "System tables (access, query history, lineage) make adoption and data richness measurable.",
        "business_value": "Without sponsorship and stewardship, semantic investments decay; with them, accuracy compounds.",
        "technical_enablement": [
            "Enable system schemas and monitor active users, query history, and lineage.",
            "Stand up dashboards to track Genie usage and accuracy over time.",
        ],
        "business_adoption": [
            "Secure an executive sponsor for the AI/BI + Genie initiative.",
            "Formally define and staff steward / data-owner roles.",
            "Run a benchmark + feedback cadence to improve answers continuously.",
        ],
        "best_practices": [
            "Measure adoption with system tables — don't assume it.",
            "Tie stewardship to roles, not heroics.",
        ],
        "sources": [
            {"title": "System tables reference (docs)", "url": "https://docs.databricks.com/aws/en/admin/system-tables/"},
        ],
    },
    "pages": {
        "name": "Pages & Business Concepts",
        "tagline": "Governed, authoritative definitions of your business concepts — cited by Genie.",
        "what": "A Page is a governed definition of a business concept — a critical term, entity, or acronym — authored in Unity Catalog. Pages are the human-modeled layer of Genie Ontology: structured fields (domain, owner, synonyms, description) plus a rich body, linked to the metrics and tables the concept depends on. When answering, Genie One prioritizes a Page's definition over context it infers automatically and cites the Page so users can confirm the source.",
        "technical_value": "One authoritative definition per concept that agents reason over and cite — resolving conflicting or inferred definitions and raising answer trust.",
        "business_value": "A shared business vocabulary: everyone (and every agent) uses the same definition of 'active user', 'qualified lead', or 'net revenue', with a named owner and an auditable source.",
        "technical_enablement": [
            "Have an account admin enable Pages (Previews), then author Pages for your highest-traffic terms, entities, and acronyms.",
            "Give each Page an owner and place it in the right domain/subdomain; add synonyms and link the related metrics, tables, and sources.",
            "Publish the canonical definitions (draft Pages stay private) and certify them so Genie One prefers and cites them.",
            "Use AI-assisted bulk import to extract terms from an existing glossary or documents, then dedupe and resolve conflicts.",
        ],
        "business_adoption": [
            "Start with the handful of contested metrics that cause the most 'which number is right?' debates.",
            "Name an owner per concept and route edit suggestions and comments through them so definitions stay trusted and current.",
            "Review Genie One answers for citations — where it cites a Page the definition is landing; where it doesn't, author or publish one.",
        ],
        "best_practices": [
            "Author Pages for the concepts people argue about first — authority beats coverage early on.",
            "Every published Page has an owner, a domain, synonyms, and links to the metrics/tables it defines.",
            "Publish and certify canonical definitions so Genie One cites them over inferred context; keep drafts private until they're ready.",
            "Keep Pages fresh — a stale authoritative definition is worse than none; use the review/suggestion workflow.",
        ],
        "sources": [
            {"title": "Pages (Unity Catalog Semantics docs)", "url": "https://docs.databricks.com/aws/en/uc-semantics/pages"},
            {"title": "Introducing Genie One, Genie Agents, and Genie Ontology (blog)", "url": "https://www.databricks.com/blog/introducing-genie-one-genie-ontology-and-genie-agents"},
        ],
    },
}

# Display order for the Learn tab.
CAPABILITY_ORDER = [
    "ontology", "unity_catalog", "metadata", "relationships",
    "metric_views", "genie_agents", "domains", "pages", "adoption",
]


def capability_summary(capability_key: str) -> str:
    cap = CAPABILITIES.get(capability_key)
    return cap["what"] if cap else ""


def best_practices_for(pillar_key: str) -> list[str]:
    """Best practices for a pillar, resolved via its capability mapping."""
    from server.pillars import PILLARS_BY_KEY
    pillar = PILLARS_BY_KEY.get(pillar_key)
    if not pillar:
        return []
    cap = CAPABILITIES.get(pillar["capability"], {})
    return cap.get("best_practices", [])


def list_capabilities() -> list[dict]:
    """All capabilities in display order (for the Learn tab)."""
    return [{"key": k, **CAPABILITIES[k]} for k in CAPABILITY_ORDER if k in CAPABILITIES]


def get_capability(key: str) -> dict | None:
    cap = CAPABILITIES.get(key)
    return {"key": key, **cap} if cap else None
