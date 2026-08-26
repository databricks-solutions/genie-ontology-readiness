# Spec — Genie Agents curation & validation signals (informed by Genie Workbench)

**Status:** Decisions resolved (§9) — ready to implement on branch `feat/genie-agent-curation-signals`. PR not yet opened.
**Author:** Allan Cao (with Claude)
**Date:** 2026-08-26 (decisions logged 2026-08-26)

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

### 4.5 Synonyms — RESOLVED (Q3): not a UC surface; fold into genie_agents curation
**Research finding (genie-workbench code + dogfood):** synonyms are **not** a Unity Catalog
`information_schema` attribute. They are a *curation property* held in two places:
- **Genie serialized space** — columns/measures carry a `synonyms` array (genie-workbench reads
  `col["synonyms"]` and its scanner warns "No column synonyms defined";
  `backend/genie_creator.py:21,61`, `backend/tests/test_scanner.py:185`).
- **Metric-view definition** — dimensions/measures may declare `synonyms` in the MV spec
  (`genie-workbench/backend/references/schema.md:28,143,153`).

There is **no clean workspace-wide `information_schema` read for column synonyms** — a metastore-wide
`columns` scan for a synonym field is both nonexistent (UC columns expose comment + tags, not
synonyms) and impractically slow on a large metastore (confirmed: the dogfood metastore-wide scan
timed out). So:
- **Reliable path (adopt):** count `synonyms` per agent in `_inspect_space` (we already fetch the
  serialized space; add a `synonyms` dimension). This makes synonyms a **genie_agents curation
  sub-signal**, not a `metadata` signal. Rubric: an agent with described columns but zero synonyms
  earns a curation warning (mirrors Workbench).
- **Follow-up (feasibility-gated):** metric-view synonyms require reading each MV's *definition*
  (`SHOW CREATE` / `information_schema.views.view_definition`) — `probe_metrics` today only counts by
  `table_type='METRIC_VIEW'` + comment (`probes.py:472,498`), it does not read the spec. Bounded
  per-MV definition reads are feasible (cap like `_MAX_INSPECT`) but are a separate change; defer to
  a follow-up rather than block this PR.
- **Dropped:** the earlier idea of a workspace-wide "column synonyms" `metadata` signal — it has no
  UC surface. No `metadata` rescore.

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

### 4.8 Genie feedback signal — RESOLVED (Q4): confirmed, adopt
**Research finding (genie-workbench GenieWatch + dogfood live):** Genie thumbs feedback is in
`system.access.audit`:
```sql
service_name = 'aibiGenie'
action_name  = 'updateConversationMessageFeedback'
request_params.feedback_rating IN ('THUMBS_UP', 'THUMBS_DOWN')
-- per-space via request_params.space_id ; per-user via user_identity.email
```
(Source: `genie-workbench/backend/watch/services/system_tables.py:580`,
`backend/watch/routers/feedback.py`.) **Validated live on dogfood (60d): 169 THUMBS_UP / 150
THUMBS_DOWN across ~160 spaces, current through 2026-08-23.**

**Adopt** as a signal — rides the *same* `system.access.audit` grant `adoption`/`genie_agents`
already need (no new permission). Emit on `adoption` (answer-satisfaction is an adoption-quality
measure) and/or as a `genie_agents` signal:
- `Genie answer feedback` — `👍 N / 👎 M (last 30d)` and a **satisfaction ratio** `pos/(pos+neg)`.
- Optional gap: high 👎 ratio → "review low-rated agents; benchmark & optimize with Genie Workbench."
Keep it a **signal** (not a rescore) initially to avoid comparability churn; it's a strong
qualitative indicator regardless.

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
only movement is for CAN_EDIT-enabled runs — an intended improvement. **Decision (Q5): accept the
one-time step; no scoring-version stamp** (consistent with the Pages reweight call). Note it in
release notes.

## 8. Testing & rollout
- Unit: per-agent tier logic (Ready/Trusted) against fixture serialized spaces; degradation when
  `|A|==0` returns the exact current existence-only score.
- Frontend: `tsc` + `vitest` (the `accelerators.test.tsx` invariant: every scored pillar keeps ≥1
  accelerator); build.
- Deployment: `deployment-integration-test` on staging (`genie-ontology-readiness-stg`) — verify
  `genie_agents` shows curation signals when the viewer can edit agents, and falls back cleanly when
  not; per-agent table renders; determinism holds for a fixed identity.
- Isaac Review on the PR before deploy (per the repo's run-isaac-review-on-every-PR rule).

## 9. Decisions (resolved 2026-08-26)
- **Q1 — scoring blend: ✅ APPROVED.** 40/60 existence-vs-curation, `0.6*ready + 0.4*trusted`.
- **Q2 — degradation: ✅ fall back to existence-only when no CAN_EDIT** — chosen for least customer
  friction (this is an assessment tool; never penalize a workspace for a grant the app lacks).
- **Q3 — synonyms: ✅ RESOLVED (§4.5).** No UC/`information_schema` surface; synonyms are a
  Genie-space property → fold into `genie_agents` curation (`_inspect_space` synonyms count).
  Metric-view synonyms = feasibility-gated follow-up. No `metadata` synonyms signal.
- **Q4 — feedback: ✅ RESOLVED (§4.8).** `system.access.audit` / `aibiGenie` /
  `updateConversationMessageFeedback` / `feedback_rating` — validated live on dogfood. Adopt.
- **Q5 — scoring-version stamp: ✅ accept one-time step, no stamp** (§7).

### UI decisions (§10)
- **D1 — palette: ✅ keep the Databricks palette** (`databricks` #FF3621 + `ink`); do not adopt
  Workbench indigo.
- **D2 — Tailwind: ✅ upgrade this app v3 → v4** (adopt Workbench's `@theme` token model) so
  Workbench's `ui/` primitives + tier/check components are drop-in, then re-express our palette as v4
  `@theme` tokens.

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

### 10.2 The two decisions — DECIDED (2026-08-26)
- **D1 — Brand palette: KEEP the Databricks palette.** Workbench's indigo is *its own* identity (it's
  a `databricks-solutions` OSS tool, not Databricks-branded); our app deliberately uses the real
  Databricks brand color (#FF3621 + `ink` #1B3139). We adopt Workbench's *structure*
  (shapes/spacing/tier pattern/components) but keep our palette, mapping Workbench's semantic
  success/warning/danger to our emerald/amber/red (already used by `IdentityBadge`).
- **D2 — Tailwind: UPGRADE this app v3 → v4.** Adopt Workbench's `@theme` CSS-variable token model so
  its `ui/` primitives + tier/check components are drop-in, then **re-express our `databricks`/`ink`
  palette as v4 `@theme` tokens** (D1). This is a real migration (see 10.5) but the user chose it over
  token-translation-on-v3, because it makes ongoing component sharing between the two tools clean.

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

### 10.5 Cohesion plan (per D1/D2)
Sequence the UI work as its own track (can land before or alongside the signal work):
1. **Tailwind v3 → v4 migration** (D2): switch to `@tailwindcss/vite`, move config into `index.css`
   via `@theme`, port existing utilities (`.card`/`.btn-*`), verify `tsc`/`vitest`/build stay green.
   Add `class-variance-authority`, `clsx`, `tailwind-merge` + a `cn()` util (copy
   `genie-workbench/frontend/src/lib/utils.ts`).
2. **Re-express our palette as `@theme` tokens** (D1): `databricks`/`ink` scales + semantic
   success/warning/danger → emerald/amber/red. Do **not** import Workbench's indigo or premium fonts;
   keep Inter.
3. **Port drop-in components** from `genie-workbench/frontend/src`: `ui/{button,badge,card,tabs}.tsx`,
   the `MATURITY_COLORS` tier badge (recolored), and the IQ check-row grid (`IQScoreTab.tsx:104–220`)
   — these render the §4 curation tiers/checks, so this dovetails with the signal work.
4. **Optional later:** `MaturityCurve` S-curve viz (recolored), dark mode. **Skip:** premium fonts,
   Recharts v3 bump (not needed).

**Bottom line:** same core stack → ~90% of Workbench's UI code is portable. With D1/D2 decided, the
plan is a real-but-bounded **Tailwind v4 upgrade + keep the Databricks palette**, then port the
tier/check components (which §4 needs anyway) so the two tools read as siblings by form while staying
on-brand.

