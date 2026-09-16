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

Readiness is anchored on Databricks' "Genie Ready" framing. The user-defined UC
Business Semantics foundation FEEDS the (gated) learned Genie Ontology layer, so
"preparing for Genie Ontology" = maturing this foundation.
"""

LEVEL_LABELS = ["Absent", "Initial", "Developing", "Established", "Optimized"]

# Overall readiness tiers: generic, engagement-neutral maturity bands. Each tier
# describes the customer's *state*, not a fixed sequence of work. The next-step
# guidance shown to the user is derived from the customer's actual pillar gaps
# (see readiness_guidance below); the per-tier "detail" here is only the generic
# fallback used when there are no gaps to surface.
READINESS_STAGES = [
    {
        "min_score": 0,
        "label": "Foundation building",
        "detail": "Establish Unity Catalog governance and a curated gold layer as the base for Genie.",
    },
    {
        "min_score": 35,
        "label": "Core foundation in place",
        "detail": "Core Unity Catalog governance is in place; strengthen metadata and the semantic layer next.",
    },
    {
        "min_score": 55,
        "label": "Semantics and Genie forming",
        "detail": "Metadata and a semantic layer are forming; curate and tune Genie Agents.",
    },
    {
        "min_score": 72,
        "label": "Curated and validating",
        "detail": "Genie Agents are curated; validate accuracy and onboard business users.",
    },
    {
        "min_score": 85,
        "label": "Ontology-ready",
        "detail": "Mature semantics, domains, and adoption. A strong candidate for the learned Genie Ontology preview.",
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
        "key": "metrics",
        "name": "Metrics",
        "weight": 20,
        "short": "Metric views — the governed metrics foundation that feeds the ontology.",
        "capability": "metric_views",
    },
    {
        "key": "genie_agents",
        "name": "Genie Agents",
        "weight": 16,
        "short": "Curated Genie Agents with instructions, example SQL, and benchmarks.",
        "capability": "genie_agents",
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
    """Map an overall 0-100 score to a generic readiness tier."""
    stage = READINESS_STAGES[0]
    for s in READINESS_STAGES:
        if overall_score >= s["min_score"]:
            stage = s
    return stage


def _join_names(names: list[str]) -> str:
    """Natural-language join: [a] -> "a", [a,b] -> "a and b", [a,b,c] -> "a, b, and c"."""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def readiness_guidance(ranked_pillars: list[dict], fallback: str) -> str:
    """Gap-driven next-step guidance for the overall readiness tier.

    Names the customer's lowest-scoring, highest-weight pillars that actually
    have gaps. The score tier alone does not tell you which gaps a customer has,
    so guidance comes from the assessment, not a static per-tier agenda. Falls
    back to the tier's generic detail when there are no gaps to surface.

    `ranked_pillars` must be pre-sorted lowest-score first, then highest-weight;
    each item needs a "name" and a "gaps" list. At most three pillars are named.
    """
    gap_pillars: list[str] = []
    for pil in ranked_pillars:
        if pil.get("gaps") and pil["name"] not in gap_pillars:
            gap_pillars.append(pil["name"])
        if len(gap_pillars) == 3:
            break
    if not gap_pillars:
        return fallback
    return f"Focus next on {_join_names(gap_pillars)}: your lowest-scoring, highest-impact areas."
