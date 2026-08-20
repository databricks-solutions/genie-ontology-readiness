"""Methodology — how to actually *build* AI-ready business semantics.

Where `library.py` explains *what* each pillar is and `accelerators.py` gives
the customer something *runnable*, this module encodes the *method*: the
phased, iterative process for curating metric views, Genie Agents, and the
governed tags/domains that make them discoverable — so an AI agent can answer
accurately and consistently.

It exists for two consumers:

  1. The **Plan** LLM — `methodology_prompt()` returns a compact, token-frugal
     digest that grounds generated plans in a real build methodology (not
     generic project-management filler), so the "Suggested sequence" reflects
     how semantics are actually built and validated.
  2. Future **Learn**-tab surfacing — the structured `PHASES` / `PRACTICES`
     registries can back a "How to build this" view per capability.

This is a data registry (like the other content modules): edit the phases and
practices here via PR. Keep it product-accurate and free of any customer- or
engagement-specific detail — it ships in a public, customer-deployable app.

The longer, human-readable version of this same methodology lives at
`docs/building-ai-ready-semantics.md` (repo-only; not synced into the app).
"""

# ---------------------------------------------------------------------------
# The five phases. Each builds on the previous; the last two loop.
# ---------------------------------------------------------------------------
PHASES: list[dict] = [
    {
        "key": "prepare",
        "name": "Prepare",
        "goal": "Scope the KPIs and pin their source of truth in Unity Catalog.",
        "steps": [
            "Pick the KPIs to onboard from your KPI catalog — with names, business descriptions, and technical definitions.",
            "For each KPI, identify its single source (a table, view, or existing metric view) and the dimension tables it needs for filtering and group-by.",
            "Capture, per KPI, the dimensions, the business/technical owners, and a few ground-truth questions with known answers to test against later.",
            "If you are migrating an existing BI semantic model, locate the source measure definitions and the reports that use them so nothing is dropped.",
        ],
    },
    {
        "key": "build",
        "name": "Build the semantic layer",
        "goal": "Create governed metric views in Unity Catalog — one fact source each — with rich metadata.",
        "steps": [
            "Create one metric view per fact source. The hard rule: a metric view has exactly ONE source (fact table, view, or metric view).",
            "Add LEFT OUTER JOINs to the dimension tables the KPI filters or groups by (e.g. a date dimension for MTD/YTD).",
            "Add and validate ONE measure at a time. Confirm each measure matches the trusted number before adding the next to the same metric view.",
            "Only co-locate measures in one metric view when they share the exact same single source AND the exact same dimension tables. Use MEASURE() so measures can compose from other measures/dimensions in the view.",
            "Write comments at all three levels — metric view, dimension, and measure. The agent reasons over all three, so keep them consistent with the names and definitions (spell out abbreviations, expected value formats).",
            "Add agent metadata: synonyms on measures and dimensions (map business language to fields; up to ten each) and format specification (e.g. date formats) so the agent doesn't have to guess.",
            "For KPIs that span multiple fact tables or contain nested logic, first build a base view (CTEs to join the sources), then build the metric view on top of it.",
        ],
    },
    {
        "key": "organize",
        "name": "Organize by domain",
        "goal": "Structure Genie Agents and metric views around the business, not around reports.",
        "steps": [
            "Model a Genie Agent as a business domain/subdomain (e.g. 'Online Marketing'); model a metric view as a KPI group within it (e.g. 'Conversion Metrics').",
            "Name metric views by convention — {subdomain}_{kpi_group} — so they sort and read predictably.",
            "Tag Genie Agents and metric views with their domain/subdomain for discoverability and observability. Optionally mirror the structure as one Unity Catalog schema per domain.",
            "Keep each agent focused — a Genie Agent supports up to 30 tables/views/metric views, and tighter domains answer better. If you approach the limit, split into more granular subdomain agents.",
        ],
    },
    {
        "key": "test",
        "name": "Test incrementally",
        "goal": "Onboard one measure at a time, prove it, and capture what worked as reusable examples and benchmarks.",
        "steps": [
            "Add one measure to the agent, ask sample questions, and validate the answer before adding the next measure.",
            "Save each validated query as an example SQL query in the agent so it reuses it. Keep example SQL simple (prefer WHERE over CASE) — complexity adds reasoning load.",
            "Leave prompt matching enabled on columns so the agent maps user language to real values and tolerates misspellings; for ambiguous categorical values, add exact-match filter instructions.",
            "Always give the Agent itself a name AND a description — multi-agent/multi-agent routing depends on the description to delegate the question correctly.",
            "Write agent instructions as: (1) trigger condition — when the user asks about X, (2) required action — always do Y, (3) an example question and expected behavior.",
            "Build benchmarks: for each validated measure, add two to four phrasings of the same question with ground-truth SQL; add more phrasings for questions likely to be misread.",
        ],
    },
    {
        "key": "validate",
        "name": "Validate & release",
        "goal": "Regression-test every change, then pilot with real business users and loop their feedback back in.",
        "steps": [
            "Re-run all benchmarks after every change (new measure, new instruction). If a previously passing benchmark now fails, the latest addition is the likely cause.",
            "Regression testing is per agent; if you connect multiple agents (a supervisor/multi-agent setup), also test the cross-agent interactions.",
            "Share the curated agent with pilot business users; have them use the built-in feedback and keep conversations reviewable by agent managers so curators can see and act on responses.",
            "Feed both regression failures and user feedback back into the testing phase — this is a loop, not a one-time release.",
        ],
    },
]

# ---------------------------------------------------------------------------
# Cross-cutting principles, grouped by the capability/pillar they most inform.
# Keys align with library.py capability keys so a future Learn view can join them.
# ---------------------------------------------------------------------------
PRACTICES: dict[str, list[str]] = {
    "metric_views": [
        "Exactly one source per metric view (fact table, view, or metric view) — never join two fact sources directly inside a metric view.",
        "Validate one measure at a time against the trusted number before adding the next.",
        "Comment at all three levels (view, dimension, measure) and add synonyms + format specification — the agent reasons over every level.",
        "Use MEASURE() for composability so a measure can build on other measures/dimensions in the same view.",
        "For multi-fact or nested KPIs, build a base view (CTEs) first, then the metric view on top — the metric view pre-defines aggregation and cuts hallucination risk.",
        "Once a metric view sits on top of a base view, remove the raw base view/tables from the Genie Agent — expose only the metric view to avoid redundancy and ambiguity.",
    ],
    "genie_agents": [
        "Scope an agent to one business domain/subdomain and keep it under the 30-item limit; split when it grows.",
        "Always add an agent description — routing across agents depends on it.",
        "Save validated queries as example SQL and keep them simple.",
        "Structure instructions as trigger condition → required action → example.",
        "Enable prompt matching; add exact-match instructions for ambiguous categorical values.",
        "Benchmark with two to four phrasings per question and ground-truth SQL; regression-test after every change.",
    ],
    "domains": [
        "Genie Agent = domain/subdomain; metric view = KPI group within it.",
        "Name metric views {subdomain}_{kpi_group}; tag agents and metric views with their domain for discoverability and observability.",
        "Assign a named owner/steward per domain and certify the canonical assets so the agent (and users) know what to trust.",
    ],
    "pages": [
        "Author a Page for each contested business concept — a governed, owned definition beats a dozen inferred ones.",
        "Give every published Page an owner and a domain; add synonyms and link the metrics/tables it defines.",
        "Publish and certify canonical definitions so Genie One cites them over inferred context; keep unfinished Pages as drafts.",
        "Keep definitions fresh via the review/suggestion workflow — Genie One trusts and cites Pages, so stale ones mislead.",
    ],
}


def list_phases() -> list[dict]:
    """The five phases in order (for a future Learn 'how to build' view)."""
    return list(PHASES)


def practices_for(capability_key: str) -> list[str]:
    """Methodology practices most relevant to a capability/pillar, if any."""
    return PRACTICES.get(capability_key, [])


def methodology_prompt() -> str:
    """A compact, token-frugal digest of the build methodology for the Plan LLM.

    Kept deliberately terse: it grounds the *sequence* and *technique* of a plan
    in how semantics are actually built and validated, without bloating the
    system prompt. Edit `PHASES`/`PRACTICES` above rather than this string —
    it is generated from them so the two never drift.
    """
    lines = [
        "BUILD METHODOLOGY (how AI-ready semantics are actually built — ground the plan's sequence and technique in this, not generic project phases):"
    ]
    for i, p in enumerate(PHASES, 1):
        lines.append(f"Phase {i} — {p['name']}: {p['goal']}")
    lines.append(
        "Non-negotiable techniques: one source per metric view; validate one measure at a time against the trusted number; "
        "comment at view/dimension/measure level and add synonyms; build a base view (CTEs) first for multi-fact/nested KPIs, then the metric view on top; "
        "one Genie Agent per domain (<=30 items) with a description; save validated queries as example SQL and keep it simple; "
        "structure agent instructions as trigger->action->example; benchmark 2-4 phrasings per question with ground-truth SQL and regression-test after every change; "
        "name metric views {subdomain}_{kpi_group} and tag agents/metric views with their domain; certify canonical assets and name a steward per domain; "
        "author and certify Pages for the business concepts users argue about so Genie One cites authoritative definitions over inferred ones."
    )
    return "\n".join(lines)
