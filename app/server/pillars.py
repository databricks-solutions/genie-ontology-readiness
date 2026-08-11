"""Canonical Genie Ontology readiness pillars — the single source of truth.

Imported by the assessment engine (scoring), the content library, and surfaced
to the frontend via /api/config. Keep keys stable; they are used as IDs across
the API and UI.

Maturity levels (0-4) apply to every pillar:
    0 Absent      — capability not present
    1 Initial     — present but ad hoc / uncurated
    2 Developing   — partial coverage, inconsistent
    3 Established  — broad coverage, governed
    4 Optimized    — comprehensive, certified, actively used

Readiness is anchored on Databricks' "Genie Ready" framing plus the Genie
Foundations 4-session delivery model. The user-defined UC Business Semantics
foundation FEEDS the (gated) learned Genie Ontology layer, so "preparing for
Genie Ontology" = maturing this foundation.
"""

LEVEL_LABELS = ["Absent", "Initial", "Developing", "Established", "Optimized"]

# Overall readiness stages, mapped to the Genie Foundations engagement.
READINESS_STAGES = [
    {
        "min_score": 0,
        "label": "Foundation building",
        "detail": "Establish Unity Catalog governance and a curated gold layer before a Genie engagement.",
    },
    {
        "min_score": 35,
        "label": "Ready for Session 1 — Data & Governance Readiness",
        "detail": "Core UC is in place; validate gold-layer data and metadata quality.",
    },
    {
        "min_score": 55,
        "label": "Ready for Session 2 — Genie Room Setup & Tuning",
        "detail": "Metadata and a semantic layer exist; stand up and tune Genie Spaces.",
    },
    {
        "min_score": 72,
        "label": "Ready for Session 3 — Validation & Business Onboarding",
        "detail": "Genie Spaces are curated; validate accuracy and onboard business users.",
    },
    {
        "min_score": 85,
        "label": "Ontology-ready — Session 4 & beyond",
        "detail": "Mature semantics, domains, and adoption. Strong candidate for the learned Genie Ontology preview.",
    },
]

# Relative weight of each pillar in the overall score (sums to 100).
PILLARS = [
    {
        "key": "uc_foundation",
        "name": "Unity Catalog Foundation",
        "weight": 15,
        "short": "Governed catalogs, schemas, and tables in Unity Catalog.",
        "capability": "unity_catalog",
    },
    {
        "key": "metadata",
        "name": "Metadata Richness",
        "weight": 22,
        "short": "Comments and descriptions on tables and columns, plus tags.",
        "capability": "metadata",
    },
    {
        "key": "relationships",
        "name": "Relationships & Modeling",
        "weight": 12,
        "short": "Primary/foreign keys and a curated gold layer for analytics.",
        "capability": "relationships",
    },
    {
        "key": "semantic_layer",
        "name": "Semantic Layer (Business Semantics)",
        "weight": 20,
        "short": "Metric views, glossary, synonyms — the foundation that feeds the ontology.",
        "capability": "metric_views",
    },
    {
        "key": "genie_spaces",
        "name": "Genie Spaces",
        "weight": 16,
        "short": "Curated Genie Spaces with instructions, example SQL, and benchmarks.",
        "capability": "genie_spaces",
    },
    {
        "key": "domains",
        "name": "Domains & Stewardship",
        "weight": 10,
        "short": "Business-aligned domains with named stewards and certification.",
        "capability": "domains",
    },
    {
        "key": "adoption",
        "name": "Adoption & Activity",
        "weight": 5,
        "short": "Active users, query activity, and lineage richness.",
        "capability": "adoption",
    },
]

PILLAR_KEYS = [p["key"] for p in PILLARS]
PILLARS_BY_KEY = {p["key"]: p for p in PILLARS}


def level_from_score(score: float) -> int:
    """Map a 0-100 pillar score to a 0-4 maturity level."""
    if score >= 85:
        return 4
    if score >= 65:
        return 3
    if score >= 40:
        return 2
    if score > 0:
        return 1
    return 0


def readiness_stage(overall_score: float) -> dict:
    """Map an overall 0-100 score to a Genie Foundations readiness stage."""
    stage = READINESS_STAGES[0]
    for s in READINESS_STAGES:
        if overall_score >= s["min_score"]:
            stage = s
    return stage
