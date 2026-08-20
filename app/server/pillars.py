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
        "detail": "Metadata and a semantic layer exist; stand up and tune Genie Agents.",
    },
    {
        "min_score": 72,
        "label": "Ready for Session 3 — Validation & Business Onboarding",
        "detail": "Genie Agents are curated; validate accuracy and onboard business users.",
    },
    {
        "min_score": 85,
        "label": "Ontology-ready — Session 4 & beyond",
        "detail": "Mature semantics, domains, and adoption. Strong candidate for the learned Genie Ontology preview.",
    },
]

# Relative weight of each pillar in the overall score. Weights sum to 100, but a
# score_exempt pillar (see the NOTE below) is excluded from the live score's
# weight denominator — so today the headline number normalizes over 84, not 100.
#
# Weighting reflects what most determines Genie Ontology readiness: the
# ontology-modeled layers the customer authors and governs — Metrics (metric
# views), Domains & Stewardship, and Pages & Business Concepts — carry
# the most weight, followed by Metadata Richness. The infrastructural (UC
# foundation, relationships), consumption (Genie Agents), and activity
# (adoption) pillars weigh less.
#
# NOTE on Pages: it is intentionally in the top weight tier, but its probe is
# currently a placeholder (Pages is a Beta UC Semantics feature with no public
# metadata API). While a probe returns "score_exempt", scoring.py excludes that
# pillar from the overall score entirely — so the placeholder does NOT drag the
# headline number. Remove the score_exempt flag from probe_pages once real
# detection lands and Pages will count at its full weight.
PILLARS = [
    {
        "key": "uc_foundation",
        "name": "Unity Catalog Foundation",
        "weight": 10,
        "short": "Governed catalogs, schemas, and tables in Unity Catalog.",
        "capability": "unity_catalog",
    },
    {
        "key": "metadata",
        "name": "Metadata Richness",
        "weight": 15,
        "short": "Comments and descriptions on tables and columns, plus tags.",
        "capability": "metadata",
    },
    {
        "key": "relationships",
        "name": "Relationships & Modeling",
        "weight": 7,
        "short": "Primary/foreign keys and a curated gold layer for analytics.",
        "capability": "relationships",
    },
    {
        "key": "metrics",
        "name": "Metrics",
        "weight": 18,
        "short": "Metric views — the governed metrics foundation that feeds the ontology.",
        "capability": "metric_views",
    },
    {
        "key": "genie_agents",
        "name": "Genie Agents",
        "weight": 12,
        "short": "Curated Genie Agents with instructions, example SQL, and benchmarks.",
        "capability": "genie_agents",
    },
    {
        "key": "domains",
        "name": "Domains & Stewardship",
        "weight": 17,
        "short": "Business-aligned domains with named stewards and certification.",
        "capability": "domains",
    },
    {
        "key": "pages",
        "name": "Pages & Business Concepts",
        "weight": 16,
        "short": "Governed Pages defining business concepts, terms, and acronyms — the authoritative layer Genie One cites over inferred context.",
        "capability": "pages",
        # Authoritative exemption flag: scoring excludes this pillar from the
        # overall score while it is unmeasurable (placeholder probe). Set here on
        # the definition — not only on the probe — so the exemption holds even if
        # the probe raises and falls back to _error_probe. Drop it (and the
        # placeholder in probe_pages) once a real detection signal exists.
        "score_exempt": True,
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
