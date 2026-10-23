# Overall scoring, levels, stages & output

After every pillar has a 0-100 score (or is marked unavailable → 0), roll them up.

## Pillar weights

| Pillar | key | weight |
|---|---|---|
| Unity Catalog Foundation | `uc_foundation` | 15 |
| Metadata Richness | `metadata` | 22 |
| Relationships & Modeling | `relationships` | 12 |
| Metric Views | `metrics` | 20 |
| Genie Agents | `genie_agents` | 16 |
| Domains & Stewardship | `domains` | 10 |
| Adoption & Activity | `adoption` | 5 |

Weights sum to **100**.

## Overall score

```
overall = round( sum(pillar.score * pillar.weight) / sum(pillar.weight), 1 )
```

`sum(pillar.weight)` is 100, so this is a weighted average. An **unavailable**
pillar contributes score 0 (it still counts in the denominator — missing
capability legitimately lowers readiness).

## Maturity level (0-4)

Applies to both a single pillar score and the overall score:

| score | level | label |
|---|---|---|
| `>= 85` | 4 | Optimized |
| `65–84` | 3 | Established |
| `40–64` | 2 | Developing |
| `1–39` | 1 | Initial |
| `0` | 0 | Absent |

## Readiness stage (overall score → tier)

Pick the highest tier whose `min_score` the overall score meets:

| min_score | stage | generic detail (fallback only) |
|---|---|---|
| 0 | Foundation building | Establish Unity Catalog governance and a curated gold layer as the base for Genie. |
| 35 | Core foundation in place | Core Unity Catalog governance is in place; strengthen metadata and the semantic layer next. |
| 55 | Semantics and Genie forming | Metadata and a semantic layer are forming; curate and tune Genie Agents. |
| 72 | Curated and validating | Genie Agents are curated; validate accuracy and onboard business users. |
| 85 | Ontology-ready | Mature semantics, domains, and adoption. A strong candidate for the learned Genie Ontology preview. |

## Prioritized gaps (top 6)

1. Rank pillars **lowest score first, then highest weight** (i.e. sort key
   `(score ascending, weight descending)`).
2. Walk the ranked pillars and collect each pillar's gaps in order, tagging each
   with its pillar name: `{pillar, gap}`.
3. Keep the **first 6**.

## Readiness guidance (the overall next-step line)

Derive the headline next step from the customer's *actual* gaps, not the stage's
generic detail:

- From the ranked pillars (same order as above), take the **names of up to 3
  pillars that have at least one gap** (dedup, preserve order).
- If any: `"Focus next on {names}: your lowest-scoring, highest-impact areas."`
  where `{names}` is a natural-language join — `"A"`, `"A and B"`, or
  `"A, B, and C"`.
- If **no** pillar has gaps: fall back to the selected stage's generic `detail`.

## Worked example

Say the pillar scores come out:
`uc_foundation 85, metadata 40, relationships 50, metrics 30, genie_agents 40, domains 20, adoption 50`.

```
weighted = 85*15 + 40*22 + 50*12 + 30*20 + 40*16 + 20*10 + 50*5
         = 1275 + 880 + 600 + 600 + 640 + 200 + 250 = 4445
overall  = 4445 / 100 = 44.5  -> level 2 (Developing)
stage    = 44.5 -> "Core foundation in place" (>=35, <55)
```

Ranked lowest→highest (score asc, weight desc): domains (20/10), metrics (30/20),
metadata (40/22), genie_agents (40/16), relationships (50/12), adoption (50/5),
uc_foundation (85/15). The first 3 *with gaps* (all but adoption have gaps here)
→ Domains, Metric Views, Metadata → guidance:
`"Focus next on Domains & Stewardship, Metric Views, and Metadata Richness: your lowest-scoring, highest-impact areas."`

## Output — the scorecard

Present the result as a readiness scorecard. Suggested Markdown shape (adapt to
the host's rendering):

```markdown
# Genie Ontology Readiness — <workspace / estate name>
_Assessed <ISO timestamp> · read-only_

**Overall: <score>/100 — Level <n> (<label>) · Stage: <readiness stage>**
> <readiness guidance line>

## Pillars
| Pillar | Score | Level | Weight | Key signals |
|---|---|---|---|---|
| Unity Catalog Foundation | 85 | Established | 15 | 6 catalogs · 92% managed · 100% in UC |
| Metadata Richness | 40 | Developing | 22 | 55% tables / 25% columns commented |
| … | | | | |
<mark any unavailable pillar as "n/a — <reason>" instead of a score>

## Top gaps (do these next)
1. [Domains & Stewardship] No domain-style governed tags found …
2. [Metric Views] No metric views found …
3. … (up to 6)

## Where the gaps are (drill-downs)
### Metadata Richness — comment coverage by schema (worst first)
| Catalog | Schema | Tables | Tables commented | Columns commented |
|---|---|---|---|---|
| acme | raw.events | 42 | 0% | 0% |
| … (top ~10 worst rows) | | | | |

### Domains & Stewardship — governance tags by schema
| Catalog | Schema | Domain-tagged | Stewarded | Certified |
|---|---|---|---|---|
| … | | | | |
```

Include a drill-down table only for the pillars that have a gap (and where the
drill-down returned rows) — that's what makes each gap actionable. Show the top
~10 rows in the pillar's native order (metadata worst-first, relationships
thinnest-first, others largest-first); link to or offer the full ≤500-row result
on request rather than dumping it.

Keep it faithful to the numbers: never round a pillar up to hide a gap, always
show *unavailable* pillars as unavailable (with the reason) rather than 0, and
list the gaps in the prioritized order above.
