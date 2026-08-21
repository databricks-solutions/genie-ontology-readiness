---
name: deployment-integration-test
description: Integration-test a DEPLOYED Genie Ontology Readiness app end-to-end. Drives the live UI (Assess / Plan / Learn) via a browser, verifies the expected UI result for every feature, cross-checks the backend (config, logs, identity mode), and reports PASS/FAIL plus any regressions from recent changes. Use after deploying to a workspace (staging or prod), before promoting a build, or to validate a PR's changes against a live deployment.
---

# Deployment integration test

Validate a **running deployment** of this app, feature by feature, by walking through
the real UI and asserting the expected result at each step — then report regressions.

This is a **live, manual-style walkthrough**: you drive the deployed app in a browser
(not the local dev server) and confirm the UI matches the documented expectations
below. Pair it with backend checks (config endpoint, app logs) so a green UI is backed
by green internals.

## 1. Inputs (ask the user if not given)

- **App URL** — e.g. `https://genie-ontology-readiness-stg-….databricksapps.com`
- **Workspace profile** — the `databricks` CLI profile for that workspace (for `apps get` / `apps logs`), e.g. `gor-serverless`, `canada-eh`.
- **App name** — the Databricks App resource name, e.g. `genie-ontology-readiness-stg`.
- **What changed** — the PR/branch under test (so you can flag regressions against intended behavior). If none given, treat every deviation from the expectations below as a candidate regression.

## 2. Prerequisites & tooling

- **Auth:** Databricks Apps are behind SSO. Browser automation must run in a Chrome
  that is **already logged in** to that workspace (the user completes SSO once). If the
  page returns an OIDC redirect, stop and ask the user to sign in, then continue.
- **UI driving:** use the **chrome-devtools MCP** tools (`navigate_page`, `take_snapshot`,
  `take_screenshot`, `click`, `fill`, `wait_for`, `list_console_messages`,
  `list_network_requests`). Alternatively delegate the UI walkthrough to the
  `fe-specialized-agents:web-devloop-tester` agent. Take a screenshot at each major step
  and keep it with the step's PASS/FAIL.
- **Backend checks:** `databricks apps get <app> --profile <p> -o json` (state/resources),
  `databricks apps logs <app> --profile <p>` (startup + errors), and the app's own
  `GET /api/config` (via an authenticated browser tab / `evaluate_script`, since curl is
  unauthenticated).

## 3. Keep expectations honest (source of truth)

The **pillar set, tabs, and routes can change between versions.** Before asserting, re-derive
the current truth from the checked-out code so this skill never tests a stale spec:

- Pillars → `app/server/pillars.py` (`PILLARS`: `key`, `name`, `weight`).
- Tabs → `app/frontend/src/App.tsx` (`TABS`).
- API routes → `app/server/routes/*.py` (`@router`).
- Learn capabilities & accelerators → `app/server/content/accelerators.py` (`capability`).

**As of this skill's writing**, the expected baseline is:

- **7 scored pillars** (keys → display name, weight): `uc_foundation` → Unity Catalog Foundation (15) · `metadata` → Metadata Richness (22) · `relationships` → Relationships & Modeling (12) · `metrics` → Metrics (20) · `genie_agents` → Genie Agents (16) · `domains` → Domains & Stewardship (10) · `adoption` → Adoption & Activity (5). Weights sum to **100**.
  - *(If the Pages & Business Concepts pillar has merged, expect 8 pillars, Pages shown as a Beta "not scored" `score_exempt` card excluded from the radar/overall — re-derive from `pillars.py`.)*
- **3 tabs:** Assess, Plan, Learn.
- **Learn capability keys** (≥1 accelerator each): `unity_catalog`, `metadata`, `relationships`, `metric_views`, `genie_agents`, `domains`, `adoption`. (Note: capability keys differ from pillar keys — `uc_foundation`↔`unity_catalog`, `metrics`↔`metric_views`.)

## 4. Preflight (backend)

| Check | How | Expected |
|---|---|---|
| App running | `databricks apps get <app> --profile <p> -o json` | `app_status.state = RUNNING`, `compute_status.state = ACTIVE` |
| Latest deploy OK | same JSON → `active_deployment.status.state` | `SUCCEEDED` |
| Resources | same JSON → `resources` | `sql-warehouse` present; `lakebase-db` present iff history expected |
| Startup clean | `databricks apps logs <app> --profile <p>` | no tracebacks; `SQL warehouse warmup complete` |
| Lakebase state | logs | `USE_LAKEBASE: True` **and** `Lakebase pool ready — assessment history + plans enabled` when history is expected; `USE_LAKEBASE: False` otherwise |
| Identity mode | logs / `app.yml` env | `FORCE_SP` value as intended; `user_api_scopes:[sql]` on the app when OBO is expected |
| Config endpoint | authenticated tab → `GET /api/config` | `app_name`, `brand_name`, `ai_models` non-empty, `default_model` set, `lakebase_enabled` matches the deploy |

## 5. Functional walkthrough (UI + expected results)

For each step: navigate, snapshot/screenshot, assert the **Expected**, record PASS/FAIL.

### 5.1 App shell / config
1. Load the app URL (authenticated). **Expected:** header shows app name + `brand_name`; three tabs (Assess/Plan/Learn); AI model selector populated from `ai_models`; no console errors (`list_console_messages`). The Help (`?`) panel opens and lists environment requirements.

### 5.2 Assess tab
1. Click **Run assessment** (streams via `POST /api/assess/stream`, SSE — one event per pillar).
   - **Expected:** each pillar card appears as it completes; then an overall **0–100** score, a maturity **stage** label, and a **radar** with one axis per **scored** pillar (7 today).
2. Expand a pillar. **Expected:** signals (label/value/detail), gaps, and — where the identity can't read a source — a graceful **"not available"** note (never a crash / 500).
3. **Top gaps** section lists the lowest-scoring, highest-weight pillars.
4. **Lakebase banner:** when `lakebase_enabled=false`, the amber "Assessments aren't saved… No Lakebase database is attached" note shows; when `true`, it's absent and runs are saved to history.
5. Re-run: score is **deterministic** for the same workspace (repeat runs are comparable).

### 5.3 Identity (OBO / SP / FORCE_SP) — the auth model
1. **OBO on** (`user_api_scopes:[sql]`, no `FORCE_SP`): pillars reflect **the viewer's** grants. In logs, catalog/metadata reads carry the user token; a read the viewer can't do (system tables) shows the **SP fallback** log line (`OBO read failed …; falling back to app SP`) rather than dropping the signal.
2. **FORCE_SP=true**: every read runs as the SP — no OBO attempt (no fallback log). Assessment reflects the **SP's** grants regardless of viewer. **Expected:** consistent workspace-wide signals (e.g. Adoption populated only if the SP holds `system.access`/`system.query`).
3. **Adoption pillar** specifically: populated only when the querying identity holds `system.access` + `system.query`. On workspaces where neither the viewer nor SP is account-admin-granted, "not available" is **expected**, not a regression.

### 5.4 Plan tab
Behavior **depends on Lakebase**:

- **Lakebase ON (history persists):**
  1. Tab shows a **"Base assessment"** dropdown of saved snapshots (newest first).
  2. **Generate plan** → streams a plan with: *Where you are*, *Top recommendations*, *Suggested sequence* (numbered), *Example Genie use cases*; names real accelerators.
  3. Generated plan is **saved** → appears in the left **Plans** history; reload persists it.
  4. **Export to PDF** opens a branded PDF (`POST /api/plan/pdf`).
- **Lakebase OFF (no history):**
  1. If an assessment was run this session → composer shows a **"Current assessment · N/100 (this session)"** base (no dropdown), helper text notes the plan lives only in-session.
  2. **Generate plan** works from the in-session scorecard (`POST /api/plan/generate` with an inline `scorecard`, no `snapshot_id`). **Expected:** a full plan streams — **no "run an assessment first" wall** just because nothing is saved.
  3. Only when **nothing** has been run this session AND no history → the "Run an assessment first" page is shown.
  4. Export to PDF still works; the plan is **not** added to a persistent Plans list.

### 5.5 Learn tab
1. Open Learn. **Expected:** a capability card for each capability key (§3); each shows technical + business framing and best practices.
2. Each scored capability surfaces **≥1 accelerator** card with steps and, where present, a downloadable artifact/guide (`GET /api/accelerators/{key}/artifact`).
3. If a scored pillar shows **zero** accelerators → **regression** (the `accelerators.test.tsx` invariant is that every scored pillar has ≥1).

## 6. Regression report

Produce a table and a verdict. Tie each FAIL to the change under test where possible.

```
| Area                      | Expected                              | Observed | Verdict |
|---------------------------|---------------------------------------|----------|---------|
| Preflight / health        | RUNNING, deploy SUCCEEDED             |          |         |
| Config endpoint           | models + lakebase_enabled correct     |          |         |
| Assess — run + score      | N pillars, 0–100, radar, stage        |          |         |
| Assess — degradation      | "not available", no 500s              |          |         |
| Identity — OBO/SP/FORCE_SP| matches deploy; SP fallback logged    |          |         |
| Plan — lakebase on         | dropdown → generate → saved → PDF     |          |         |
| Plan — lakebase off        | in-session generate, no "run first"   |          |         |
| Learn — cards+accelerators | 1 card/capability, ≥1 accelerator each|          |         |
```

- **Verdict:** overall PASS only if every functional area passes.
- **Regressions:** list each deviation, the exact UI step + screenshot, and the suspected commit/PR. Distinguish *environment-expected* gaps (e.g. Adoption "not available" for lack of account-admin grants) from genuine regressions.
- **Evidence:** attach screenshots per step and any console/network errors.

## 7. Notes

- This tests a **deployment**, not local dev. Deploy first (see `CLAUDE.md` / the project's deploy flow); `app.yml` and `frontend/dist/` are `.gitignore`d and must actually reach the app or the frontend/env silently fall back to defaults.
- Read-only by design: running an assessment makes no writes to the customer's data; the only writes are the app's own history/plans in Lakebase (when attached).
