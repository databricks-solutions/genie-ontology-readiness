"""Accelerators — curated, self-serve assets that raise a pillar's readiness score.

Where the content library (`library.py`) explains *what* a pillar is and *why* it
matters, accelerators give the customer something *runnable* to close the gap: a
notebook, SQL script, dashboard, DAB, or external repo. Each accelerator is tied
to the capability (pillar) it improves and to the assessment signal(s) it lifts,
so the Learn tab can surface it per-pillar and the Plan can recommend it by name.

This is a data registry (like `library.py`) — add accelerators here via PR. Keep
`key` stable; it is the API/UI identity. Set `valid_as_of` and use
`superseded_by` when a stop-gap is replaced by a native API/feature.

Artifacts that ship with the app live under `app/accelerators/<dir>/`; the app
serves a download + an import command (no workspace write permission required).
Accelerators that point at an external repo (e.g. UCX) carry no bundled artifact.
"""

# Each entry. Optional fields: artifact_dir/artifact_file (bundled), source (link),
# review_mode, superseded_by.
ACCELERATORS: list[dict] = [
    {
        # Top-priority metadata accelerator: broadest coverage (comments, tags,
        # certification-ready, metric views, FK) — listed first for customers.
        "key": "metadata-dbxmetagen",
        "title": "dbxmetagen — AI metadata, tags, certification & metric-view generator",
        "summary": "Databricks Industry Solutions toolkit that auto-generates comments, PII/domain tags, metric views, FK predictions, and even Genie agents across Unity Catalog — the fastest way to enrich metadata at scale.",
        "capability": "metadata",
        "type": "repo",
        "effort": "Half a day to pilot on a schema",
        "what_it_does": (
            "dbxmetagen is an AI-powered, DAB-deployed app (FastAPI + React) that enriches Unity Catalog "
            "metadata at scale across several modes: comment generation (tables/columns), PI mode "
            "(classify + tag PII/PHI/PCI as governed tags), domain classification, data profiling and "
            "quality scoring, AI-assisted foreign-key prediction, and a semantic-layer mode that "
            "auto-generates metric views and provisions Genie agents from a knowledge base. Because it "
            "writes comments, governed tags, and metric views, it lifts the Metadata, Domains, "
            "Relationships, and Metrics pillars at once — and its outputs are a strong base for "
            "certifying your canonical assets. Review AI-generated metadata before applying."
        ),
        "prerequisites": [
            "Clone/deploy dbxmetagen via Databricks Asset Bundles (repo linked below)",
            "A Foundation Model API / serving endpoint for the LLM",
            "MODIFY on the target catalog to apply comments/tags; ASSIGN on governed tags to tag",
            "A steward to review generated metadata before it is applied",
        ],
        "improves_signals": ["metadata", "domains", "relationships", "metric_views"],
        "target_level": 4,
        "review_mode": True,
        "steps": [
            "Deploy dbxmetagen from the repo (DAB) into the workspace.",
            "Point it at a target catalog/schema and pick modes (comment / PI / domain / metric views).",
            "Run generation; review the drafted comments, tags, and metric views.",
            "Apply the approved metadata, then certify the canonical assets.",
            "Re-run the readiness assessment — Metadata, Domains, Relationships, and Semantic scores should rise.",
        ],
        "source": {
            "title": "dbxmetagen (Databricks Industry Solutions, GitHub)",
            "url": "https://github.com/databricks-industry-solutions/dbxmetagen",
        },
        "valid_as_of": "2026-07",
    },
    {
        "key": "semantic-dbxmetagen",
        "title": "dbxmetagen — auto-generate metric views (semantic layer)",
        "summary": "Use dbxmetagen's semantic-layer mode to auto-generate Unity Catalog metric views (measures, dimensions, join paths) — and even provision Genie agents — from your documented catalog.",
        "capability": "metric_views",
        "type": "repo",
        "effort": "~1-2 hours per subject area",
        "what_it_does": (
            "dbxmetagen's semantic-layer mode reads your tables (and any comments / tags it generated) and "
            "drafts Unity Catalog metric views — measures, dimensions, and join paths — plus example SQL, "
            "and can provision a Genie agent from the knowledge base. It turns a documented catalog into a "
            "queryable semantic layer far faster than authoring metric views by hand. Always review the "
            "generated measure logic with a business owner before publishing."
        ),
        "prerequisites": [
            "Deploy dbxmetagen via Databricks Asset Bundles (repo linked below)",
            "A Foundation Model API / serving endpoint for the LLM",
            "CREATE/MODIFY on the target schema to create metric views",
            "Rich comments/tags first (run comment/domain modes) — improves generated measure quality",
            "A business owner to validate KPI/measure definitions before publishing",
        ],
        "improves_signals": ["metric_views"],
        "target_level": 4,
        "review_mode": True,
        "steps": [
            "Deploy dbxmetagen (DAB) and point it at the gold schema for one subject area.",
            "Run comment/domain modes first so the LLM has business context (optional but recommended).",
            "Run the semantic-layer mode to draft metric views (measures, dimensions, joins) + example SQL.",
            "Review the generated metric-view definitions with a business owner; correct measure logic and names.",
            "Publish the approved metric views (and optionally the generated Genie agent).",
            "Re-run the readiness assessment — the Metrics pillar should rise.",
        ],
        "source": {
            "title": "dbxmetagen (Databricks Industry Solutions, GitHub)",
            "url": "https://github.com/databricks-industry-solutions/dbxmetagen",
        },
        "valid_as_of": "2026-07",
    },
    {
        "key": "relationships-dbxmetagen",
        "title": "dbxmetagen — AI foreign-key & relationship discovery",
        "summary": "Use dbxmetagen's AI foreign-key prediction to propose primary/foreign-key relationships across your tables, then declare the confirmed ones as UC constraints so joins are modeled for analytics and Genie.",
        "capability": "relationships",
        "type": "repo",
        "effort": "~1 hour per schema",
        "what_it_does": (
            "dbxmetagen uses AI-assisted foreign-key prediction to infer PK/FK relationships between tables "
            "from names, types, and data profiles. Review the proposed keys, then declare the confirmed ones "
            "as informational PRIMARY KEY / FOREIGN KEY constraints in Unity Catalog so join paths are "
            "explicit for analysts, metric views, and Genie."
        ),
        "prerequisites": [
            "Deploy dbxmetagen via Databricks Asset Bundles (repo linked below)",
            "A Foundation Model API / serving endpoint for the LLM",
            "SELECT on the catalog to profile columns; MODIFY to add constraints",
            "A data modeler / steward to confirm proposed keys",
        ],
        "improves_signals": ["relationships"],
        "target_level": 3,
        "review_mode": True,
        "steps": [
            "Deploy dbxmetagen (DAB) and target the schema/catalog to model.",
            "Run FK-prediction to get candidate PK/FK relationships with confidence scores.",
            "Review candidates with a data modeler; discard false positives.",
            "Declare the confirmed keys as informational PRIMARY KEY / FOREIGN KEY constraints in UC.",
            "Re-run the readiness assessment — the Relationships & Modeling pillar should rise.",
        ],
        "source": {
            "title": "dbxmetagen (Databricks Industry Solutions, GitHub)",
            "url": "https://github.com/databricks-industry-solutions/dbxmetagen",
        },
        "valid_as_of": "2026-07",
    },
    {
        "key": "domains-dbxmetagen",
        "title": "dbxmetagen — classify & tag assets for domains, PII & certification",
        "summary": "Use dbxmetagen's PI and domain modes to tag PII/PHI/PCI with governed tags and classify tables into business domains at scale — the groundwork for stewardship and certifying canonical assets.",
        "capability": "domains",
        "type": "repo",
        "effort": "Half a day per catalog",
        "what_it_does": (
            "dbxmetagen's PI mode detects and tags PII/PHI/PCI as UC governed tags, and its domain mode "
            "classifies tables into business domains/subdomains with an agent. Applied at scale this populates "
            "the domain and classification tags this pillar measures, and gives stewards a clean base to "
            "certify the canonical asset in each domain (system.certification_status = certified)."
        ),
        "prerequisites": [
            "Deploy dbxmetagen via Databricks Asset Bundles (repo linked below)",
            "A Foundation Model API / serving endpoint for the LLM",
            "ASSIGN on the governed tags; MODIFY on the target catalog to apply tags",
            "A steward to review classifications/domains before applying, and to certify",
        ],
        "improves_signals": ["domains"],
        "target_level": 4,
        "review_mode": True,
        "steps": [
            "Deploy dbxmetagen (DAB) and target a catalog/schema.",
            "Run PI mode to detect and draft PII/PHI/PCI governed tags.",
            "Run domain mode to classify tables into business domains/subdomains.",
            "Review the drafted tags/domains with stewards; apply the approved ones.",
            "Certify the canonical asset in each domain (SET TAG system.certification_status = certified).",
            "Re-run the readiness assessment — the Domains & Stewardship pillar should rise.",
        ],
        "source": {
            "title": "dbxmetagen (Databricks Industry Solutions, GitHub)",
            "url": "https://github.com/databricks-industry-solutions/dbxmetagen",
        },
        "valid_as_of": "2026-07",
    },
    {
        "key": "metadata-ai-comments",
        "title": "AI-generated, glossary-grounded column comments",
        "summary": "Bulk-generate column comments grounded in your own data dictionary, then apply them after steward review.",
        "capability": "metadata",
        "type": "notebook",
        "effort": "~1 hour",
        "what_it_does": (
            "Loads your internal standards / data-dictionary docs into a Delta table, builds a "
            "Vector Search index over them, and for every column missing a COMMENT runs "
            "VECTOR_SEARCH + ai_query to draft a comment grounded in that documentation. Drafts "
            "are written to a review table; a second step applies only the rows a steward approves. "
            "Fills the gap that AI Comments has no API endpoint for programmatic bulk generation."
        ),
        "prerequisites": [
            "Vector Search enabled in the workspace",
            "A serving endpoint for embeddings (e.g. databricks-bge-large-en) and an LLM (Foundation Model API)",
            "SELECT/MODIFY on the target catalog (to read columns and ALTER comments)",
            "Your data-dictionary / standards text in a Volume or table (the notebook also accepts inline docs)",
        ],
        "improves_signals": ["metadata.column_comment_pct", "metadata.table_comment_pct"],
        "target_level": 3,
        "review_mode": True,
        "steps": [
            "Import ai_comments_rag.py into the customer workspace (see the import command below).",
            "Set the scope params: catalog/schema (or specific tables) and the docs source.",
            "Run the generate step — drafts land in a *_comment_review table tagged validated='no'.",
            "Review the drafts with a steward; mark approved rows validated='yes'.",
            "Run the apply step to ALTER COLUMN … COMMENT only the approved rows.",
            "Re-run the readiness assessment — column/table comment coverage (and the Metadata score) should rise.",
        ],
        "artifact_dir": "metadata-ai-comments",
        "artifact_file": "ai_comments_rag.py",
        "source": {
            "title": "Add AI-generated comments (docs)",
            "url": "https://docs.databricks.com/aws/en/comments/ai-comments",
        },
        "valid_as_of": "2026-06",
    },
    {
        "key": "uc-foundation-ucx",
        "title": "UCX — Hive metastore → Unity Catalog migration toolkit",
        "summary": "Databricks Labs' official toolkit to assess a workspace and migrate tables, grants, and groups onto Unity Catalog.",
        "capability": "unity_catalog",
        "type": "repo",
        "effort": "Half a day to assess; migration varies",
        "what_it_does": (
            "Runs an assessment that inventories your Hive-metastore tables, grants, clusters, and "
            "jobs, then provides workflows to migrate tables into UC catalogs/schemas and convert "
            "user-level grants to group-based grants — the foundation every other pillar builds on."
        ),
        "prerequisites": [
            "Workspace admin to install the toolkit",
            "An existing Hive metastore to migrate from",
            "A Unity Catalog metastore assigned to the workspace",
        ],
        "improves_signals": ["uc_foundation"],
        "target_level": 3,
        "review_mode": False,
        "steps": [
            "Install UCX from Databricks Labs (databricks labs install ucx).",
            "Run the assessment workflow to inventory the Hive metastore.",
            "Review the assessment dashboard, then run table and group migration workflows.",
            "Re-run the readiness assessment to confirm UC coverage.",
        ],
        "source": {
            "title": "Databricks Labs UCX (GitHub)",
            "url": "https://github.com/databrickslabs/ucx",
        },
        "valid_as_of": "2026-06",
    },
    {
        # Customer-facing: every link here is public (open-source repo, public
        # docs/blogs) and the bundled plan is a shareable leave-behind — no
        # internal go/ links, tickets, Slack, or field-only material.
        "key": "genie-space-workbench-workshop",
        "title": "Genie Agent Quality Workshop (Genie Workbench)",
        "summary": "A guided half-day that takes one Genie Agent from cold-start failures to 85%+ benchmark accuracy — scored, optimized, and production-ready — using the open-source Genie Workbench app.",
        "capability": "genie_agents",
        "type": "repo",
        "effort": "Half a day (facilitated)",
        "what_it_does": (
            "Genie Workbench is an open-source Databricks App that scores a Genie Agent against "
            "deterministic and LLM-evaluated checks, benchmarks it against your own question set, and "
            "runs a guided fix + auto-optimize loop with proof of lift. In a half-day workshop your BI "
            "developer and business power users take one agent from failing answers to a trusted, "
            "benchmarked, versioned agent — and leave with a reproducible playbook for the next 2-3 "
            "agents. Download the workshop plan below to line up the right people, then engage your "
            "Databricks account team to facilitate the session."
        ),
        "prerequisites": [
            "Deploy Genie Workbench as a Databricks App in your workspace (open-source repo, linked below)",
            "One Genie Agent backed by a real data model (5-7 tables ideal, fewer than 16 preferred; 30-table hard limit)",
            "10-20 benchmark questions with expected answers or SQL, prepared by business power users",
            "A Foundation Model API endpoint (Genie Workbench uses Claude Sonnet)",
            "Baseline curation where obvious: table/column descriptions, join relationships, hidden internal tables",
        ],
        "improves_signals": ["genie_agents"],
        "target_level": 4,
        "review_mode": True,
        "steps": [
            "Use-case discovery — frame the business questions the agent must answer and the metric definitions behind them (30 min).",
            "Benchmark questions — capture expected SQL/answers, tag the tier-1 deal-breakers, and load the suite into Workbench (45 min).",
            "Scan + deep analysis — run the deterministic and LLM-evaluated checks; prioritize data model > knowledge store > SQL examples > instructions (45 min).",
            "Fix + curation — the BI developer runs the fixes; power users accept/modify/reject each patch in the side-by-side diff (60 min).",
            "Auto-optimize + validate — run the optimization loop, A/B compare baseline vs. optimized on the tier-1 questions, and document the playbook (60 min).",
            "Re-run the readiness assessment — the Genie Agents pillar should rise as the agent reaches the 'Trusted' tier.",
        ],
        "artifact_dir": "genie-space-workbench-workshop",
        "artifact_file": "workshop-plan.md",
        "source": {
            "title": "Genie Workbench (open source, GitHub)",
            "url": "https://github.com/databricks-solutions/databricks-genie-workbench",
        },
        "valid_as_of": "2026-07",
    },
    {
        # Customer-facing: Databricks-owned open-source template; public repo only.
        "key": "genie-code-data-product-accelerator",
        "title": "Data Product Accelerator — Genie Code skills for semantics & Genie Agents",
        "summary": "Databricks open-source library of Genie Code / agentic skills that build a governed data product end-to-end, including semantic-layer (metric view) and Genie-agent patterns.",
        "capability": "genie_agents",
        "type": "repo",
        "effort": "Guided / skill-driven",
        "what_it_does": (
            "The Data Product Accelerator is a Databricks open-source template of composable "
            "'Genie Code' skills that guide an engineer through building a governed data product: "
            "medallion (bronze/silver/gold) modeling, a semantic layer of metric views, "
            "Genie-agent patterns with agent instructions and benchmark questions, and "
            "monitoring. Use the semantic-layer and Genie-agent skills to stand up well-curated "
            "metric views and agents faster and more consistently than authoring them by hand — "
            "a code-driven complement to the Genie Agent Quality Workshop."
        ),
        "prerequisites": [
            "Databricks workspace with Unity Catalog",
            "Access to Genie Code or an agentic coding environment (e.g. Claude Code)",
            "A target schema and the source tables for your data product",
            "A Foundation Model API endpoint for the agentic steps",
        ],
        "improves_signals": ["genie_agents", "metric_views"],
        "target_level": 4,
        "review_mode": False,
        "steps": [
            "Clone the repo and open the data_product_accelerator skills.",
            "Use the semantic-layer skill to define metric views for your core KPIs.",
            "Use the Genie-agent patterns / genai-agents skills to scaffold a curated, well-instructed agent with benchmark questions.",
            "Iterate with the skills' review steps, then publish the objects to Unity Catalog.",
            "Re-run the readiness assessment — the Genie Agents (and Metric Views) score should rise.",
        ],
        "source": {
            "title": "Data Product Accelerator (Databricks, GitHub)",
            "url": "https://github.com/databricks-solutions/vibe-coding-workshop-template",
        },
        "valid_as_of": "2026-07",
    },
    {
        "key": "genie-spaces-methodology-guide",
        "title": "Handbook: curate & benchmark Genie Agents",
        "summary": "How to organize Genie Agents by domain, onboard one measure at a time with example SQL, write trigger→action→example instructions, and benchmark + regression-test. Download the full handbook.",
        "capability": "genie_agents",
        "type": "guide",
        "effort": "Reference / self-guided",
        "what_it_does": (
            "A well-curated Genie Agent has one focused domain/subdomain (≤30 items), always with a description "
            "for routing. You save validated queries as example SQL and keep it simple. Instructions are written "
            "as trigger→action→example. Benchmark 2–4 phrasings per question with ground-truth SQL and "
            "regression-test after every change. This guide covers Phases 3–5 of the AI-ready-semantics handbook "
            "in depth, with techniques for organizing and validating trusted Genie Agents."
        ),
        "prerequisites": [
            "Create access on Genie Agents",
            "Metric views already drafted and available in the agent's source catalog",
            "2-4 benchmark questions per KPI with ground-truth SQL",
            "A business owner to validate agent routing and instructions",
        ],
        "improves_signals": ["genie_agents"],
        "target_level": 4,
        "review_mode": False,
        "steps": [
            "Create one Genie Agent per business domain/subdomain, keeping under 30 items for performance.",
            "Onboard one metric view at a time; validate its measure against sample questions.",
            "Save validated queries as example SQL; keep the SQL simple to reduce the agent's reasoning load.",
            "Write agent instructions as trigger→action→example for ambiguous or domain-specific logic.",
            "Benchmark 2–4 phrasings per question, regression-test after every change, and download the handbook for the full method.",
        ],
        "artifact_dir": "ai-ready-semantics",
        "artifact_file": "building-ai-ready-semantics.md",
        "source": {
            "title": "Building AI-Ready Business Semantics (guide)",
            "url": "https://docs.databricks.com/aws/en/genie/",
        },
        "valid_as_of": "2026-08",
    },
    # ---- Relationships & Modeling (weight 12) -------------------------------
    {
        # Build accelerator (per docs/accelerators-plan.md §3): profile the
        # catalog to propose keys and emit ALTER TABLE … ADD CONSTRAINT for
        # steward review — a lightweight, dependency-free complement to the
        # dbxmetagen FK predictor above.
        "key": "relationships-pk-fk-generator",
        "title": "Declare primary & foreign keys",
        "summary": "How to identify and declare informational primary/foreign-key constraints so join paths are explicit for analysts, metric views, and Genie.",
        "capability": "relationships",
        "type": "guide",
        "effort": "~1 hour per schema",
        "what_it_does": (
            "Profile your target schema using information_schema and column data — checking uniqueness, "
            "cardinality, and name/type matches — to nominate candidate primary and foreign key relationships. "
            "Score each candidate by strength, review with a data modeler, and declare the confirmed ones as "
            "informational constraints. Declaring keys makes join paths explicit so Genie and metric views "
            "model relationships correctly."
        ),
        "prerequisites": [
            "SELECT on the target catalog to profile columns and sample data",
            "MODIFY / ownership on the tables to add informational constraints",
            "A data modeler / steward to confirm proposed keys before applying",
        ],
        "improves_signals": ["relationships"],
        "target_level": 3,
        "review_mode": True,
        "steps": [
            "Profile the target schema with information_schema and column data (uniqueness, cardinality, name/type matches) to nominate candidate keys.",
            "Score each candidate relationship by uniqueness and cardinality; review with a data modeler and mark confirmed relationships, discarding false positives.",
            "For the confirmed keys, write and execute informational ALTER TABLE … ADD CONSTRAINT (PRIMARY KEY / FOREIGN KEY) statements (see the linked docs).",
            "Re-run the readiness assessment — the Relationships & Modeling pillar should rise.",
        ],
        "source": {
            "title": "Declare primary key and foreign key constraints (docs)",
            "url": "https://docs.databricks.com/aws/en/tables/constraints",
        },
        "valid_as_of": "2026-07",
    },
    {
        "key": "relationships-gold-view-scaffolder",
        "title": "Build pre-joined gold views",
        "summary": "How to author wide/star gold views over your common join paths so Genie and analysts see curated, pre-denormalized shapes instead of raw normalized tables.",
        "capability": "relationships",
        "type": "guide",
        "effort": "Half a day per subject area",
        "what_it_does": (
            "Take the declared (or candidate) key relationships for a subject area and author "
            "pre-joined 'gold' views — wide dimensional or star shapes — over the common join paths. "
            "Presenting Genie and analysts a curated, denormalized view of each subject area reduces "
            "the number of joins a natural-language question must infer and improves answer accuracy."
        ),
        "prerequisites": [
            "SELECT on the source tables and CREATE VIEW on the target gold schema",
            "Declared or candidate PK/FK relationships (run the Declare primary & foreign keys guide first)",
            "A data owner to confirm the join grain and business logic of each view",
        ],
        "improves_signals": ["relationships"],
        "target_level": 3,
        "review_mode": True,
        "steps": [
            "Identify the common join paths and denormalization grain for your subject area (e.g., dimensional/star shapes).",
            "Draft wide/star view SQL that pre-joins the source tables over the declared key relationships (see the linked docs).",
            "Review the view definitions with a data owner; correct grain, filters, and naming.",
            "Create the approved gold views in Unity Catalog and comment them.",
            "Re-run the readiness assessment — the Relationships & Modeling pillar should rise.",
        ],
        "source": {
            "title": "Create views in Unity Catalog (docs)",
            "url": "https://docs.databricks.com/aws/en/views/",
        },
        "valid_as_of": "2026-07",
    },
    # ---- Metrics / Metric Views (weight 20) -------------------------
    {
        # Build accelerator (per plan §3): scaffold metric-view YAML from a KPI
        # inventory or query history — a hand-authored complement to the
        # dbxmetagen semantic-layer mode above.
        "key": "semantic-metric-view-scaffolder",
        "title": "Author metric views from a KPI inventory",
        "summary": "How to turn a KPI inventory sheet (or mined query history) into Unity Catalog metric views — measures, dimensions, join paths, and synonyms — to codify your business definitions.",
        "capability": "metric_views",
        "type": "guide",
        "effort": "~1-2 hours per subject area",
        "what_it_does": (
            "Start with a KPI inventory (name, definition, source table, grain) or infer candidates from "
            "query history, then author Unity Catalog metric-view YAML with measures, dimensions, join paths, "
            "and synonyms. Authoring metric views from a structured inventory is far faster and more consistent "
            "than writing YAML by hand, and it codifies the agreed business definitions so Genie and analysts "
            "see the authoritative KPI logic."
        ),
        "prerequisites": [
            "A KPI inventory sheet (name, definition, source table, grain) — or query-history access to mine one",
            "CREATE/MODIFY on the target schema to create metric views",
            "SELECT on the underlying gold tables the metric views sit on",
            "A business owner to validate KPI/measure definitions before publishing",
        ],
        "improves_signals": ["metric_views"],
        "target_level": 4,
        "review_mode": True,
        "steps": [
            "Gather a KPI inventory sheet (name, business definition, source table, grain, owner) — or mine candidates from recent query history.",
            "Author metric-view YAML stubs (measures, dimensions, join paths, synonyms) for each KPI (see the linked docs).",
            "Review each metric view with a business owner; correct measure logic, names, and synonyms.",
            "Publish the approved metric views to Unity Catalog.",
            "Re-run the readiness assessment — the Metrics pillar should rise.",
        ],
        "source": {
            "title": "Unity Catalog metric views (docs)",
            "url": "https://docs.databricks.com/aws/en/metric-views/",
        },
        "valid_as_of": "2026-07",
    },
    {
        "key": "semantic-kpi-drift-finder",
        "title": "Find & consolidate KPI drift",
        "summary": "How to mine query history to find the same KPI computed differently across dashboards, then prioritize which metric views to define first.",
        "capability": "metric_views",
        "type": "guide",
        "effort": "~1 hour",
        "what_it_does": (
            "Scan system.query.history for recurring aggregate expressions and group near-duplicate KPI "
            "computations (e.g. revenue defined five slightly different ways across dashboards), then rank "
            "by traffic and inconsistency to prioritize which metric views to define first. This turns an "
            "ad-hoc semantic sprawl into a prioritized backlog of standardizations to make."
        ),
        "prerequisites": [
            "The system.query.history system table enabled and SELECT granted to the assessment SP",
            "A SQL warehouse to run the analysis",
        ],
        "improves_signals": ["metric_views"],
        "target_level": 3,
        "review_mode": False,
        "steps": [
            "Query system.query.history to cluster near-duplicate KPI computations by frequency and inconsistency (see the linked docs).",
            "Rank the recurring computations by traffic and inconsistency so you can prioritize which metric views to define first.",
            "Review the ranked drift report; pick the top KPIs to standardize via metric views.",
            "Define metric views for those KPIs (see the Author metric views guide) and point dashboards at them.",
            "Re-run the readiness assessment — the Metrics pillar should rise as KPIs consolidate.",
        ],
        "source": {
            "title": "Query history system table (docs)",
            "url": "https://docs.databricks.com/aws/en/admin/system-tables/query-history",
        },
        "valid_as_of": "2026-07",
    },
    {
        "key": "metric-views-methodology-guide",
        "title": "Handbook: build AI-ready metric views",
        "summary": "A step-by-step method for authoring governed metric views — one fact source each, one measure at a time, with the comments and synonyms Genie needs. Download the full handbook.",
        "capability": "metric_views",
        "type": "guide",
        "effort": "Reference / self-guided",
        "what_it_does": (
            "Metric views are the semantic layer's foundation: each one has exactly one source, and you validate "
            "one measure at a time against the trusted number. The methodology covers source selection, base views "
            "(CTEs) for multi-fact KPIs, and then the metric view with rich comments, synonyms, and format specs. "
            "This guide walks through Phase 2 (Build the semantic layer) of the full 5-phase AI-ready-semantics "
            "handbook in depth, with non-negotiable techniques for metric-view authorship."
        ),
        "prerequisites": [
            "CREATE/MODIFY on the target schema to create metric views",
            "SELECT on the underlying source tables",
            "A business owner to validate measure definitions before publishing",
        ],
        "improves_signals": ["metric_views"],
        "target_level": 4,
        "review_mode": False,
        "steps": [
            "Identify your fact sources and the dimensions each metric view will join.",
            "Apply the one-source rule: build a base view (CTEs) first if multiple fact sources are needed, then create the metric view on top.",
            "Add dimensions, then validate one measure at a time against the trusted number.",
            "Comment at the view/dimension/measure level; add synonyms and format specs so Genie understands them.",
            "Download the handbook for the full phase-by-phase method and non-negotiable techniques.",
        ],
        "artifact_dir": "ai-ready-semantics",
        "artifact_file": "building-ai-ready-semantics.md",
        "source": {
            "title": "Building AI-Ready Business Semantics (guide)",
            "url": "https://docs.databricks.com/aws/en/metric-views/",
        },
        "valid_as_of": "2026-08",
    },
    # ---- Domains & Stewardship (weight 10) ---------------------------------
    {
        # Build accelerator (per plan §3): apply governed domain + steward tags
        # from a mapping — the pre-native-Domains structure the app assesses.
        "key": "domains-steward-tagging-kit",
        "title": "Tag domains & assign stewards",
        "summary": "How to apply governed domain and steward tags across your tables to establish the business-domain structure and named accountability that this pillar measures.",
        "capability": "domains",
        "type": "guide",
        "effort": "Half a day per catalog",
        "what_it_does": (
            "Create a mapping of table → business domain and → owner/steward, then apply governed UC tags "
            "(domain, owner, steward) across your catalog at scale. This gives every asset a named accountable owner "
            "and populates the domain and stewardship tags the readiness assessment reads."
        ),
        "prerequisites": [
            "A mapping sheet of table → domain and → owner/steward",
            "ASSIGN on the governed tags and MODIFY on the target catalog to apply tags",
            "Domain leads to confirm the domain boundaries and named stewards",
        ],
        "improves_signals": ["domains"],
        "target_level": 3,
        "review_mode": True,
        "steps": [
            "Create a mapping sheet of table → business domain and → owner/steward.",
            "Write the SET TAG statements you will apply (see the linked docs for tag syntax) and preview them.",
            "Review the planned domain/steward assignments with domain leads.",
            "Apply the approved governed domain, owner, and steward tags across the catalog.",
            "Re-run the readiness assessment — the Domains & Stewardship pillar should rise.",
        ],
        "source": {
            "title": "Govern data with tags (docs)",
            "url": "https://docs.databricks.com/aws/en/database-objects/tags",
        },
        "valid_as_of": "2026-07",
    },
    {
        "key": "domains-certification-tagger",
        "title": "Certify canonical assets",
        "summary": "How to mark the authoritative asset in each domain as certified so Genie and analysts can trust and prefer the canonical source.",
        "capability": "domains",
        "type": "guide",
        "effort": "~1 hour",
        "what_it_does": (
            "Identify the most-accessed, authoritative asset per domain/subject, then apply the "
            "system.certification_status = certified governed tag to each one. Certifying the canonical asset "
            "signals trust to Genie and analysts and is exactly the metric this pillar rewards — the assessment "
            "checks how many of your most-accessed tables are certified."
        ),
        "prerequisites": [
            "A confirmed list of the canonical (authoritative) asset per domain",
            "ASSIGN on the system.certification_status tag and MODIFY on the target tables",
            "Stewards to confirm which asset is canonical before certifying",
        ],
        "improves_signals": ["domains"],
        "target_level": 4,
        "review_mode": True,
        "steps": [
            "Identify the canonical (most-accessed, authoritative) asset per domain; rank by access patterns in system.access.",
            "Confirm the list with domain stewards — which asset is canonical for each domain.",
            "Apply SET TAG system.certification_status = certified on the approved canonical assets (see the linked docs).",
            "Optionally mark superseded assets deprecated so consumers migrate to the canonical one.",
            "Re-run the readiness assessment — the Domains & Stewardship pillar should rise.",
        ],
        "source": {
            "title": "Certified tag / trust in Unity Catalog (docs)",
            "url": "https://docs.databricks.com/aws/en/database-objects/tags",
        },
        "valid_as_of": "2026-07",
    },
    {
        "key": "domains-methodology-guide",
        "title": "Handbook: organize domains & stewardship",
        "summary": "How to structure Genie Agents and metric views around business domains, name metric views by convention, tag assets with their domain, and certify canonical assets with named stewards. Download the full handbook.",
        "capability": "domains",
        "type": "guide",
        "effort": "Reference / self-guided",
        "what_it_does": (
            "Organizing around business domains (not reports) keeps Genie Agents focused and discoverable. "
            "Metric views follow a naming convention ({subdomain}_{kpi_group}), and both agents and metric views "
            "are tagged with their domain for observability. Canonical assets are certified and assigned named stewards. "
            "This guide covers Phase 3 (Organize by domain) of the AI-ready-semantics handbook in depth, with the "
            "foundational discipline that makes a semantic layer sustainable."
        ),
        "prerequisites": [
            "Genie Agents and metric views already created per the build phases",
            "ASSIGN on governed tags to apply domain/steward classifications",
            "Business domain leads to confirm domain boundaries and steward assignments",
        ],
        "improves_signals": ["domains"],
        "target_level": 4,
        "review_mode": False,
        "steps": [
            "Map your Genie Agents to business domains and subdomains; one agent per focused domain, under 30 items.",
            "Name metric views by convention: {subdomain}_{kpi_group} to make ownership visible in the catalog.",
            "Apply governed domain and steward tags to both Genie Agents and metric views for discoverability and observability.",
            "Identify the canonical (most-used, most-trusted) metric view or agent per domain and certify it with a named steward.",
            "Download the handbook for the full domain-organization method and stewardship patterns.",
        ],
        "artifact_dir": "ai-ready-semantics",
        "artifact_file": "building-ai-ready-semantics.md",
        "source": {
            "title": "Building AI-Ready Business Semantics (guide)",
            "url": "https://docs.databricks.com/aws/en/database-objects/tags",
        },
        "valid_as_of": "2026-08",
    },
    # ---- Adoption & Activity (weight 5) ------------------------------------
    {
        "key": "adoption-governance-hub",
        "title": "Use the account Governance Hub dashboards",
        "summary": "Before building your own, check the out-of-the-box usage & activity dashboards in the account-level Governance Hub — account admins get Data, AI, and Cost views plus an importable Usage Dashboard with no build effort.",
        "capability": "adoption",
        "type": "guide",
        "effort": "~15 min (account admin)",
        "what_it_does": (
            "The account console's Governance Hub (currently a preview/beta you enable in the account "
            "console) surfaces centralized Data, AI, and Cost governance/usage insights at the account level. "
            "Separately, the account console provides an importable, pre-built Usage Dashboard (product/SKU "
            "breakdowns, top cost sources, cost forecasting) you set up from Account Console → Usage → Setup "
            "dashboard into any Unity Catalog-enabled workspace. These give an out-of-the-box view of "
            "account-wide activity without building a dashboard by hand — a fast complement to a "
            "workspace-scoped custom dashboard. Note Governance Hub is preview and may take up to a day to "
            "populate after enabling."
        ),
        "prerequisites": [
            "Account Admin role (Governance Hub and the account Usage Dashboard are account-console features)",
            "A Unity Catalog-enabled workspace to import the Usage Dashboard into",
            "For the billing Usage Dashboard: SELECT on system.billing.usage and system.billing.list_prices",
            "Governance Hub preview enabled in the account console (may take ~1 day to populate)",
        ],
        "improves_signals": ["adoption"],
        "target_level": 3,
        "review_mode": False,
        "steps": [
            "Open the account console and enable the Governance Hub preview (Data / AI / Cost pages) — allow up to a day for data to populate.",
            "For account-wide usage, go to Account Console → Usage → Setup dashboard and import the pre-built Usage Dashboard into a Unity Catalog-enabled workspace.",
            "Review the out-of-the-box activity, cost, and AI-usage views before deciding what a custom workspace dashboard still needs to add.",
            "Share the dashboards with data owners and sponsors so account-level adoption is visible.",
            "Re-run the readiness assessment — the Adoption & Activity pillar reflects the visible activity.",
        ],
        "source": {
            "title": "Governance Hub (account console, docs)",
            "url": "https://docs.databricks.com/aws/en/admin/governance-hub/",
        },
        "valid_as_of": "2026-08",
    },
    {
        # Build accelerator (per plan §3): the visible adoption metric from
        # system tables. This is the only accelerator for the adoption pillar,
        # which had no coverage before.
        "key": "adoption-dashboard",
        "title": "Build an adoption & activity dashboard",
        "summary": "How to create a Lakeview dashboard over system tables that charts active users, query volume, Genie usage, and comment coverage so adoption becomes visible and trackable.",
        "capability": "adoption",
        "type": "guide",
        "effort": "~1 hour",
        "what_it_does": (
            "Build a Lakeview dashboard backed by system tables (system.access, system.query.history, "
            "and information_schema) that charts active users, query volume, Genie usage, lineage richness, "
            "and the comment-coverage trend by schema/domain. Making activity visible turns adoption from an "
            "invisible signal into a metric teams can watch climb."
        ),
        "prerequisites": [
            "System schemas (access, query) enabled with SELECT granted to the assessment SP",
            "A SQL warehouse to back the dashboard",
            "CAN MANAGE on a Lakeview dashboard to import the template",
        ],
        "improves_signals": ["adoption"],
        "target_level": 3,
        "review_mode": False,
        "steps": [
            "Build a Lakeview dashboard backed by system.access, system.query.history, and information_schema (see the linked docs).",
            "Create visualizations for active users, query volume, Genie usage, and comment-coverage trend by schema/domain.",
            "Bind it to a SQL warehouse and set scope params as needed.",
            "Share the dashboard with data owners and schedule regular snapshots so trends are tracked over time.",
            "Re-run the readiness assessment — the Adoption & Activity pillar reflects the visible activity.",
        ],
        "source": {
            "title": "System tables reference (docs)",
            "url": "https://docs.databricks.com/aws/en/admin/system-tables/",
        },
        "valid_as_of": "2026-07",
    },
    {
        "key": "adoption-readiness-trend-job",
        "title": "Track readiness over time",
        "summary": "How to schedule the readiness assessment to run on a cadence and persist each result so the readiness score is charted over time — proof that accelerators are landing.",
        "capability": "adoption",
        "type": "guide",
        "effort": "~1 hour",
        "what_it_does": (
            "Schedule the readiness assessment to run on a recurring cadence and persist each result as a Lakebase snapshot. "
            "Then chart the overall score and per-pillar scores over time. This closes the loop: as accelerators land and "
            "coverage rises, the trend visibly climbs — giving stakeholders a single chart that proves the ontology-readiness "
            "program is working."
        ),
        "prerequisites": [
            "The readiness app deployed with Lakebase snapshots enabled",
            "Permission to deploy a job (via Databricks Asset Bundles) into the workspace",
            "A service principal with the system-table and catalog read grants the assessment needs",
        ],
        "improves_signals": ["adoption"],
        "target_level": 4,
        "review_mode": False,
        "steps": [
            "Define a DAB job that runs the readiness assessment on a recurring schedule (see the linked docs for DAB syntax).",
            "Configure the job to persist each assessment result as a Lakebase snapshot for historical tracking.",
            "Apply the DAB configuration and let it run on its cadence (e.g. weekly) to accumulate a trend history.",
            "Create a dashboard or use Lakebase to visualize the overall and per-pillar score trends over time.",
            "Re-run the readiness assessment — the Adoption & Activity pillar reflects sustained activity and trend tracking.",
        ],
        "source": {
            "title": "Databricks Asset Bundles (docs)",
            "url": "https://docs.databricks.com/aws/en/dev-tools/bundles/",
        },
        "valid_as_of": "2026-07",
    },
]

ACCELERATORS_BY_KEY = {a["key"]: a for a in ACCELERATORS}


def list_accelerators() -> list[dict]:
    """All accelerators, in registry order."""
    return list(ACCELERATORS)


def accelerators_for(capability_key: str) -> list[dict]:
    """Accelerators that improve a given capability/pillar."""
    return [a for a in ACCELERATORS if a.get("capability") == capability_key]


def get_accelerator(key: str) -> dict | None:
    return ACCELERATORS_BY_KEY.get(key)
