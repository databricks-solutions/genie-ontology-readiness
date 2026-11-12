"""Impact statements: what a pillar or a gap costs for Genie Ontology.

Dependency-free, like ``server.pillars``. Every statement is a fixed string, so
the same findings always give the same text and repeated runs stay comparable.
No LLM is used.

Form: "Because you do not X, Genie Ontology will likely be less Y."

Genie Ontology (the learned layer) is in Private Preview. The statements describe
the governed Unity Catalog Business Semantics foundation that feeds it. They do
not claim that the learned layer is generally available.

Keys:
    PILLAR_IMPACT  — pillar key (``server.pillars``) -> one statement per pillar.
    GAP_IMPACT     — gap id (``server.signal_spec`` ids and the probe ``gap_ids``)
                     -> one statement per gap. A gap without an id shows only its
                     pillar-level statement.
"""

from server.pillars import LEVEL_BANDS

# A pillar with no gap shows its pillar-level statement only when its score is below
# this line. Adoption has no gap probe, so its gap list is always empty. The line is
# the minimum score of the "Developing" maturity band in server.pillars.LEVEL_BANDS.
NO_GAP_IMPACT_MAX_SCORE = next(b["min_score"] for b in LEVEL_BANDS if b["level"] == 2)

# Shown above the overall score. Frames the score as readiness for Genie Ontology.
OVERALL_FRAMING = (
    "This score measures readiness for Genie Ontology. Genie Ontology is in Private "
    "Preview. The score measures the governed Unity Catalog Business Semantics "
    "foundation that feeds it."
)

PILLAR_IMPACT: dict[str, str] = {
    "uc_foundation": (
        "Because you do not use Unity Catalog for most of your data, Genie Ontology will "
        "likely be less complete. It has less governed data to learn from."
    ),
    "metadata": (
        "Because you do not describe most tables and columns, Genie Ontology will likely be "
        "less precise. The learned context has little business meaning to work with."
    ),
    "relationships": (
        "Because you do not declare keys or a curated gold layer, Genie Ontology will likely "
        "be less accurate on multi-table questions. It has to guess the join paths."
    ),
    "metrics": (
        "Because you do not define metric views, Genie Ontology will likely be less consistent. "
        "Each user recalculates KPIs ad hoc."
    ),
    "genie_agents": (
        "Because you do not curate Genie Agents, Genie Ontology will likely be less accurate on "
        "business questions. It has few validated examples to learn from."
    ),
    "domains": (
        "Because you do not assign domains and stewards, Genie Ontology will likely be less "
        "trusted. Its concepts have no clear owners."
    ),
    "adoption": (
        "Because you have few users who query your data, Genie Ontology will likely be less tuned "
        "to real business questions. It learns from fewer real questions."
    ),
}

GAP_IMPACT: dict[str, str] = {
    # Metadata Richness
    "table_comments_low": (
        "Because you do not describe most tables, Genie Ontology will likely be less accurate "
        "when it picks a table for a question."
    ),
    "column_comments_low": (
        "Because you do not describe most columns, Genie Ontology will likely be less precise. "
        "The learned context has little business meaning to work with."
    ),
    "no_tagged_tables": (
        "Because you do not tag tables, Genie Ontology will likely be less able to group assets "
        "by domain or sensitivity."
    ),
    # Unity Catalog Foundation
    "no_user_catalogs": (
        "Because you do not use your own catalogs, Genie Ontology will likely have no governed "
        "data to learn from."
    ),
    "small_table_footprint": (
        "Because you govern few tables in Unity Catalog, Genie Ontology will likely cover fewer "
        "business concepts."
    ),
    "hive_metastore_tables": (
        "Because you keep some tables in the legacy hive_metastore, Genie Ontology will likely be "
        "less governed. Those tables lack Unity Catalog controls and metadata."
    ),
    # Relationships & Modeling
    "no_gold_layer": (
        "Because you do not have a curated gold layer, Genie Ontology will likely be less accurate. "
        "It has no trusted, pre-joined tables to answer from."
    ),
    "no_fk_constraints": (
        "Because you do not declare foreign keys, Genie Ontology will likely be less reliable at "
        "inferring join paths. Multi-table questions are less accurate."
    ),
    # Metrics
    "no_metric_views": (
        "Because you do not define metric views, Genie Ontology will likely give less consistent "
        "KPI answers. Each user recalculates KPIs ad hoc."
    ),
    "few_metric_views": (
        "Because you define few metric views, Genie Ontology will likely cover fewer shared KPIs. "
        "Answers vary more between users."
    ),
    "metric_views_uncommented": (
        "Because you do not describe your metric views, Genie Ontology will likely be less clear "
        "about what each KPI means."
    ),
    # Genie Agents
    "no_genie_agents": (
        "Because you have no Genie Agents, Genie Ontology will likely have no curated entry point. "
        "It learns less from real use."
    ),
    "no_active_genie_agents": (
        "Because you have no active Genie Agent, Genie Ontology will likely learn less from real "
        "questions and feedback."
    ),
}


def pillar_impact(pillar_key: str, *, score: float, has_gaps: bool, available: bool) -> str | None:
    """The pillar-level statement to show, or None.

    Shown only when the pillar was assessed and either has a gap, or (for a pillar
    with no gaps) scores below ``NO_GAP_IMPACT_MAX_SCORE``. A well-scoring pillar with
    no gaps gets None, so it never reads "Because you do not ..." when that is false.
    """
    if not available:
        return None
    if has_gaps or score < NO_GAP_IMPACT_MAX_SCORE:
        return PILLAR_IMPACT.get(pillar_key)
    return None


def gap_impact(gap_id: str | None) -> str | None:
    """The statement for one gap id, or None when the id has no statement."""
    if not gap_id:
        return None
    return GAP_IMPACT.get(gap_id)
