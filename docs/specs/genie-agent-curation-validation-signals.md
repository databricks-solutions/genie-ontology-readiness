# Spec — Genie Agents curation & validation signals (informed by Genie Workbench)

**Status:** DRAFT for review (branch `feat/genie-agent-curation-signals`). Not yet implemented — validate before build/PR.
**Author:** Allan Cao (with Claude)
**Date:** 2026-08-26

## 1. Background & motivation

Our assessment scores 7 workspace-readiness pillars. The **Genie Agents** pillar (`genie_agents`,
weight 16) currently scores from **audit counts only** (`_genie_audit_counts`): `total` distinct
agents and `active_30d`, via `system.access.audit` (`aibiGenie`). It does **not** measure whether
those agents are actually *curated* or *validated* — the qualities that determine Genie answer
accuracy.

We already built the plumbing for deeper assessment but **disconnected it**:
- `app/server/assessment/probes.py:563` `_inspect_space()` fetches a serialized space and counts
  curation dimensions (`instructions`, `sample_questions`, `example_sqls`, `functions`,
  `benchmarks`, `tables`) — but `probe_genie_agents` (line 661) no longer calls it.
- The frontend still ships `GenieSpaceCuration` (`types.ts`) and `GenieSpacesTable`
  (`PillarDetail.tsx`) that render per-space curation from `pillar.metrics.spaces` — but the probe
  no longer populates `metrics.spaces`.

**Genie Workbench** (github.com/databricks-solutions/databricks-genie-workbench, cloned locally at
`~/dev/repos/genie-workbench`) independently validates this direction. Its **IQ Scan** scores a
single Genie space on **12 checks (0–12)** across three tiers — *Not Ready → Ready to Optimize →
Trusted* — and its **Auto-Optimize** job benchmarks against the official Genie Eval-Run API. Its IQ
rubric is a battle-tested scoring model we can borrow for our per-agent curation signal. Genie
Workbench is already our `genie_agents` accelerator (`genie-space-workbench-workshop`), so the two
tools should dovetail: **we borrow its rubric for read-only signals; we keep pointing to it for the
deep fix (optimize/benchmark).**

## 2. Goals / non-goals

**Goals**
- Re-activate per-agent curation assessment and fold a curation-quality score into `genie_agents`,
  using Genie Workbench's IQ thresholds.
- Add the one dimension we're missing entirely: **validation/trust** (benchmarks present, verified/
  example SQL) — the top of Workbench's maturity curve.
- Add two smaller metadata signals Workbench surfaces: **synonyms coverage** and **column-noise
  hygiene** (as additive signals/gaps, see §4.4–4.5).
- Keep the two tools visually cohesive (§11).

**Non-goals (explicitly out of scope)**
- No LLM-as-judge and no benchmark **eval loop**. Workbench retired its 9 LLM judges and now uses
  only the official Genie Benchmark Eval-Run API inside a bounded Databricks Job (paired-evidence
  gate, hill-climbing patches). That is heavy, write-capable, and is *what Workbench is for*. We only
  **detect the outputs** of validation (do benchmarks exist? has an eval/optimize run happened?) and
  hand off to Workbench.
- No writes to Genie spaces. This app stays read-only.

## 3. Current state (baseline to preserve)

`probe_genie_agents` scoring today (existence only): `total>0 → +40`, `active_30d>0 → +40`,
`active/total ≥ 0.3 → +20`, capped at 100. Signals: `Genie Agents`, `Active agents (30d)`.
Determinism: score is a pure function of findings. The pillar degrades gracefully to "not available"
when `system.access.audit` isn't readable.

## 4. Design — signals & scoring

### 4.1 Re-wire per-agent curation into `probe_genie_agents`
- After computing audit counts, enumerate agents to deep-inspect. Source of agent ids: the audit
  scan already yields distinct `space_id`s; select up to `_MAX_INSPECT` (30) non-trashed, prefer
  `active_30d` first (so we assess the agents that matter).
- For each, call `_inspect_space(host, headers, sid, title)`. Outcomes: `ok` (serialized read),
  `forbidden` (lacks CAN_EDIT), `error`.
- **Add one dimension to `_inspect_space`**: `description_ok` — the space description meets a minimum
  (≥30 chars / ≥5 words, per IQ check 1). Read from the serialized space top-level description.
- Repopulate `metrics.spaces` with the per-agent `GenieSpaceCuration` rows (re-lights the existing
  frontend table).

### 4.2 Per-agent curation rubric (adapted from Workbench IQ Scan)
Compute per assessed agent (each maps to a Workbench IQ check; see
`genie-workbench/packages/genie-space-optimizer/src/genie_space_optimizer/iq_scan/scoring.py`):

| Check | Rule | `_inspect_space` dim |
|-------|------|----------------------|
| `has_description` | ≥30 chars & ≥5 words | new `description_ok` |
| `has_instructions` | `instructions > 0` | `instructions` |
| `has_sql_guidance` | `functions > 0` OR `example_sqls > 0` | `functions`, `example_sqls` |
| `sources_in_range` | `1 ≤ tables ≤ 12` | `tables` |
| `has_benchmarks` (validation) | `benchmarks ≥ 10` | `benchmarks` |
| `has_examples` (validation) | `example_sqls > 0` | `example_sqls` |

Per-agent tiers (mirroring Workbench):
- **Ready to Optimize** = `has_description AND has_instructions AND has_sql_guidance AND sources_in_range`
- **Trusted** = Ready **AND** `has_benchmarks`

### 4.3 New `genie_agents` scoring model
Blend existence/adoption (kept, rebalanced) with curation quality. Weight of the pillar stays **16**.

```
existence_band  (max 40): total>0 → 20 ; active_30d>0 → 10 ; active/total≥0.3 → 10
curation_band   (max 60), computed ONLY over curation-assessed agents A (|A| = readable serialized):
    ready_frac   = (# agents that are "Ready to Optimize") / |A|
    trusted_frac = (# agents that are "Trusted")           / |A|
    curation_band = 60 * (0.6 * ready_frac + 0.4 * trusted_frac)
score = existence_band + curation_band   (cap 100)
```

**Graceful degradation (important):** if `|A| == 0` (no agent is CAN_EDIT-readable — the common
SP-only / restricted-viewer case), **curation_band is not computed**; fall back to the *current*
existence-only formula (40/40/20) so there is **no regression or false penalty**, and set a note via
`_EDIT_HINT`. This keeps the score identical to today whenever curation can't be assessed.

**OBO/identity note:** because curation depends on CAN_EDIT, the pillar score can differ between a
viewer who can edit agents (curation-inclusive) and the SP/read-only path (existence-only) — same
class of OBO variance already documented for `adoption`. Surface it in the pillar note.

### 4.4 New signals emitted by `genie_agents`
Existing: `Genie Agents`, `Active agents (30d)`. Add:
- `Curation-assessed` — "N of M agents" (how many we could read serialized; M = total).
- `Well-curated agents` — count/percent meeting the **Ready** bar.
- `Agents with ≥10 benchmarks` — **validation** signal (Trusted-tier gate).
- `Agents with example/verified SQL`.
- Per-agent breakdown restored in `metrics.spaces` (frontend `GenieSpacesTable`), now including the
  Ready/Trusted tier chip.

New gaps (drive the plan): "N agents have no benchmarks — add ≥10 ground-truth Q&A and validate with
Genie Workbench Auto-Optimize"; "N agents lack example/verified SQL"; "N agents have no
instructions." Each names **Genie Workbench** as the accelerator (already in `accelerators.py`).

### 4.5 `metadata` — synonyms coverage signal (additive; see Open Questions)
Genie resolves natural-language phrasing via synonyms; Workbench treats synonyms as first-class
(`add_column_synonym` lever, entity matching). We count comments+tags but not synonyms.
- **Reliable path:** metric-view synonyms — parse from metric-view definitions the `metrics` probe
  already reads; emit "Metric-view fields with synonyms (%)".
- **Best-effort path:** column-level synonyms — detection surface is uncertain (may not be in
  `information_schema`); see Open Question Q3. Scope column synonyms as a follow-up if no clean read.
- Emit as an **additive signal + gap only** on the pillar it's most measurable in (`metrics` for
  metric-view synonyms). **Do not rescore** `metadata`/`metrics` weights (avoid comparability churn).

### 4.6 `metadata` — column-noise hygiene signal (additive)
Workbench IQ check 10 penalizes many noisy visible columns (`id/uuid/hash/etl_*/raw_*/…`). Compute
from `information_schema.columns` over assessed catalogs: per table, `noisy_ratio` = noisy columns /
total; flag tables that are "noise-heavy" (e.g. `visible_cols ≥ 20 AND noisy_ratio ≥ 0.30`, matching
Workbench). Emit workspace signal "Noise-heavy tables (N)" + a gap ("prune/hide low-signal columns so
Genie isn't distracted"). **Additive signal + gap, no rescore.** Noise regex to mirror Workbench's
list (see `iq_scan/scoring.py` check 10).

### 4.7 (Optional) Plan lever taxonomy
Structure the plan's "Top recommendations" by Workbench's 6 levers (descriptions; synonyms; joins;
instructions/examples; SQL expressions/measures/filters; metric views) so each gap → a concrete
lever. Light touch in `plan.py` `_generate_system` prompt; the methodology already covers most.

### 4.8 (Future) Genie feedback signal
Workbench's GenieWatch reads Genie **cost/usage/feedback** from system tables (SP-only). Our
`adoption` pillar counts generic users+queries; a Genie-specific **thumbs/feedback** signal would
sharpen it. Deferred pending confirmation of a feedback system-table surface (Open Question Q4).

## 5. Data sources & permissions
- **Curation:** `GET /api/2.0/genie/spaces/{id}?include_serialized_space=true` — requires **CAN_EDIT**
  (same constraint Workbench documents). Runs OBO (viewer token) or SP per the existing
  `get_auth_headers` path. Bounded by `_MAX_INSPECT`.
- **Counts/activity:** `system.access.audit` (`aibiGenie`) — existing, needs `SELECT on system.access`.
- **Synonyms / column-noise:** `information_schema` (columns / metric-view metadata) — existing
  per-catalog fallback applies.
- All reads ride the OBO→SP fallback + identity-attribution added in PR #5.

## 6. Files to change
- `app/server/assessment/probes.py` — extend `_inspect_space` (`description_ok`); re-wire it into
  `probe_genie_agents`; new curation scoring + signals + gaps; new `metadata`/`metrics` signals
  (synonyms, column-noise).
- `app/server/pillars.py` — no weight change (keep 16); optionally refine `genie_agents` `short`.
- `app/frontend/src/types.ts` — extend `GenieSpaceCuration` (tier, description_ok).
- `app/frontend/src/components/PillarDetail.tsx` — show Ready/Trusted tier chip in `GenieSpacesTable`.
- `app/server/content/accelerators.py` / `content/library.py` — ensure gap→Workbench accelerator
  wording; add synonyms/noise best-practice bullets.
- `app/server/routes/plan.py` — (optional §4.7) lever-structured recommendations.
- `docs/` — this spec.

## 7. Scoring / comparability impact
Changing `genie_agents` internal scoring changes historical comparability for workspaces where
curation becomes assessable (like the Pages reweight tradeoff). Because the pillar **weight is
unchanged (16)** and the score is **identical to today whenever curation can't be assessed**, the
only movement is for CAN_EDIT-enabled runs — an intended improvement. Call it out in release notes;
consider a scoring-version stamp on snapshots if trend continuity matters (same open item flagged for
Pages).

## 8. Testing & rollout
- Unit: per-agent tier logic (Ready/Trusted) against fixture serialized spaces; degradation when
  `|A|==0` returns the exact current existence-only score.
- Frontend: `tsc` + `vitest` (the `accelerators.test.tsx` invariant: every scored pillar keeps ≥1
  accelerator); build.
- Deployment: `deployment-integration-test` on staging (`genie-ontology-readiness-stg`) — verify
  `genie_agents` shows curation signals when the viewer can edit agents, and falls back cleanly when
  not; per-agent table renders; determinism holds for a fixed identity.
- Isaac Review on the PR before deploy (per the repo's run-isaac-review-on-every-PR rule).

## 9. Open questions (please validate)
- **Q1 — scoring blend:** is 40/60 existence-vs-curation with `0.6*ready + 0.4*trusted` the right
  emphasis, or should validation (benchmarks) weigh more heavily?
- **Q2 — degradation:** confirm the "fall back to existence-only when no CAN_EDIT" behavior (avoids
  penalizing the common SP-only case) vs. showing a lower "curation unknown" score.
- **Q3 — synonyms detection:** is there a clean read for column/metric-view synonyms
  (`information_schema` or metric-view definition)? If not, defer §4.5 column synonyms.
- **Q4 — feedback:** is there a Genie feedback/thumbs system-table surface for §4.8?
- **Q5 — scoring-version stamp:** add one now (for trend continuity) or accept a one-time step?

## 10. UI cohesion with Genie Workbench — viability

**Verdict: viable and largely low-friction for the *structural* design language and — conveniently —
for exactly the components the new curation signals need. But do NOT wholesale-adopt Workbench's
palette/fonts; keep our Databricks brand.** Two real decisions gate "full" cohesion (Tailwind major
version, and brand palette). Details below.

### 10.1 Stack comparison
| Aspect | This app | Genie Workbench | Cohesion note |
|--------|----------|-----------------|---------------|
| Framework | React 18 + Vite 6 | React + Vite | ✅ same |
| **Tailwind** | **v3.4** (`tailwind.config.js`, `extend.colors`) | **v4.2** (`@theme` in `index.css`, `@tailwindcss/vite`, no config file) | ⚠️ **major-version gap** — different config model |
| Component pattern | hand-rolled `.card`/`.btn` `@apply` utilities | hand-rolled `ui/` primitives + **CVA + tailwind-merge + clsx** | portable; would add cva/clsx/tailwind-merge (tiny) |
| Icons | lucide-react ^0.468 | lucide-react ^0.560 | ✅ same lib |
| Charts | recharts ^2.15 | recharts ^3.8 | ⚠️ major-version gap (only matters if reusing charts — we don't) |
| **Palette** | `databricks` **#FF3621** (brand red) + `ink` #1B3139 | `--color-accent` **#4F46E5** (Electric Indigo) + slate | ❌ **different brand identity** |
| Fonts | Inter + system | Cabinet Grotesk + General Sans (premium, self-hosted woff2) + Inter fallback | ⚠️ licensing + asset weight |
| Dark mode | none (light only) | full light/dark via CSS vars + `.dark` | Workbench more advanced |
| Maturity vocab | `LevelBadge` L0–L4 (`levelStyle`) | Not Ready / Ready to Optimize / Trusted (red/blue/emerald) | different scales, but see 10.3 |

### 10.2 The two real decisions (flag for the user)
- **D1 — Brand palette.** Workbench's indigo is *its own* identity (it's a `databricks-solutions` OSS
  tool, not Databricks-branded). Our app deliberately uses the real Databricks brand color
  (#FF3621). "Seamless" can mean either (a) **keep Databricks brand**, adopt only Workbench's
  *structure* (shapes/spacing/tier pattern/dark mode) → sibling-by-form, on-brand; or (b) **match
  Workbench's indigo** → sibling-by-color, off Databricks brand. **Recommendation: (a).** Adopt the
  structural language, keep our palette (map Workbench's semantic success/warning/danger to our
  emerald/amber/red, which we already use for `IdentityBadge`).
- **D2 — Tailwind v3 → v4.** Workbench's tokens live in v4's `@theme`. Pragmatic path is **not** a v4
  migration; instead **translate** the pieces we want (radius scale, semantic color tokens, dark-mode
  CSS vars) into our v3 `tailwind.config.js` + `index.css`. Full v4 migration only if we want drop-in
  reuse of their `ui/` primitives verbatim — defer unless separately planned.

### 10.3 What's genuinely worth folding in (high value ↔ low friction) — and it aligns with §4
- **Maturity-tier badges (Not Ready / Ready to Optimize / Trusted).** This is the *lucky alignment*:
  those are **exactly** the per-agent curation tiers this spec introduces (§4.2). Adopt Workbench's
  tier vocabulary + badge shape (`lib/utils.ts` `MATURITY_COLORS`, `ui/badge.tsx`) for the per-agent
  curation chip in `GenieSpacesTable` — semantically identical AND visually cohesive. Recolor to our
  emerald/amber/red. **Drop-in, minimal.**
- **IQ check-row grid (pass / warning / fail, 3-column).** `IQScoreTab.tsx:104–220` — perfect for
  rendering the per-agent curation checks (§4.2) in `PillarDetail`. Hand-rolled Tailwind + lucide;
  **drop-in portable.**
- **`cn()` + CVA variant pattern** (`lib/utils.ts`, `ui/button.tsx`, `ui/badge.tsx`) — small, clean
  upgrade to our ad-hoc classname strings. Low effort; improves both apps' consistency.
- **Radius scale + card conventions** (`--radius-*`, `ui/card.tsx` `rounded-xl`) — translate into our
  v3 config for shape parity. Low effort.

### 10.4 Medium / deferred
- **MaturityCurve S-curve viz** (`MaturityCurve.tsx`) — Workbench's signature visual; could front the
  genie_agents curation view. Portable-with-rework (parameterize the hardcoded tier colors to our
  palette). Medium effort; nice-to-have, not required for the signals.
- **Dark mode** — Workbench has a full CSS-var light/dark system; our app is light-only. Adopting it
  is a broader design effort — defer unless we want it product-wide.
- **Premium fonts (Cabinet Grotesk / General Sans)** — licensing + self-hosted assets. **Skip** —
  keep Inter (free, already loaded); it's a fine shared base and both apps fall back to it anyway.
- **Recharts 3 upgrade / IterationChart** — only relevant to the optimize-loop UI we are explicitly
  not building (§2). Skip.

### 10.5 Cohesion effort summary
- **Now (with this spec's signals):** tier badges + check-row grid for the curation UI → sibling look
  *for the genie_agents surface* with near-zero extra cost, since we're building that UI anyway.
- **Small follow-up:** `cn()`/CVA + radius/token translation for app-wide shape parity.
- **Deferred:** MaturityCurve viz, dark mode, any palette/font/Tailwind-v4 change (needs D1/D2 calls).

**Bottom line:** ~90% of Workbench's UI *code* is portable and ~75% of its *look* — but true seamlessness
is a branding decision (D1), not a technical blocker. The technically clean, on-brand win is to adopt
its **structure and the tier/check components** (which we need for §4 regardless) while keeping the
Databricks palette and Inter.

