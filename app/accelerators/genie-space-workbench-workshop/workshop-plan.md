# Genie Space Quality Workshop

**Take one Genie Space from cold‑start failures to a trusted, benchmarked, production‑ready space in a half day — and leave with a repeatable playbook for the rest.**

A brand‑new Genie Space with just a table and a question usually fails: no
descriptions, no instructions, no examples, no benchmarks — so business users
lose trust before they start. This workshop uses the open‑source **Genie
Workbench** app to score a space, benchmark it against *your own* questions, and
run a guided fix + auto‑optimize loop with measurable proof of lift.

> **Want this facilitated?** Ask your Databricks account team to run this
> workshop with you. A Databricks specialist can co‑deliver the half‑day session,
> help design your benchmark set, and advise on production rollout. This document
> is the plan you can share internally to get the right people in the room.

---

## Who should be in the room

- **A BI developer / data engineer** who owns the space's data model and can edit it.
- **One to three business power users** who know what a *correct* answer looks like and can validate results.

## What you'll walk away with

- A **scored, versioned Genie Space** that reaches the "Trusted" maturity tier.
- **≥ 85% benchmark accuracy** on your tier‑1, deal‑breaker questions.
- **Proof of lift** (baseline vs. optimized) captured with MLflow.
- A **reproducible playbook** — with named owners — to roll to your next 2–3 spaces.

---

## Pre‑work (about one week before)

1. **Deploy Genie Workbench** as a Databricks App in your workspace — it's open
   source (repo linked in *Resources* below).
2. **Pick one Genie Space** backed by a real data model. 5–7 tables is ideal;
   fewer than 16 is strongly preferred (there is a 30‑table hard limit).
3. **Prepare 10–20 benchmark questions** with the expected answer or expected SQL
   for each. This is the single most common gap and the top reason spaces fail —
   don't skip it.
4. **Do baseline curation** where it's obvious: add table/column descriptions,
   define join relationships, and hide internal/staging tables.

## Half‑day agenda (≈ 4 hours + a 15‑minute break)

| # | Step | Time | What happens |
|---|------|------|--------------|
| 1 | **Use‑case discovery** | 30 min | Frame the business questions the space must answer. Surface the definition layer (how revenue is calculated, what counts as "active"), access constraints (row‑level security, multi‑tenancy), and the decisions users make from these answers today. |
| 2 | **Benchmark questions & "what good looks like"** | 45 min | Power users walk their 10–20 questions; capture expected SQL/answers and tag the ~5 tier‑1 deal‑breakers. Load the suite into Workbench. |
| 3 | **Scan + deep analysis** | 45 min | Run the deterministic checks and the LLM‑evaluated assessment. Whiteboard the priority order: **data model → knowledge store → SQL examples → instructions.** |
| — | *Break* | 15 min | |
| 4 | **Fix + power‑user curation** | 60 min | The BI developer runs the fix step; power users review each proposed patch in a side‑by‑side diff and **accept / modify / reject** — the customer team authors the config. |
| 5 | **Auto‑optimize, validate & playbook** | 60 min | Kick off the optimization loop (LLM‑judged, with auto‑rollback). While it runs, cover production architecture (entity matching, token budget, scaling, ops). Then A/B compare baseline vs. optimized on the tier‑1 questions, review the proof of lift, and write the playbook for the next spaces. |

## Success metrics

- Maturity score moves from baseline → **Trusted** tier.
- **≥ 85%** accuracy on tier‑1 deal‑breaker questions.
- Business users are querying the optimized space **within 30 days**.
- The playbook is rolled to **2–3 more spaces within 60 days**.

## Good to know (current limitations)

- **30‑table hard limit** per space; denormalize aggressively — wide/pre‑joined views beat many small tables.
- **Don't mix metric views and regular tables** in the same space.
- There is a **per‑minute query rate limit** — design for it when planning rollout.
- Genie Workbench uses **Claude Sonnet via the Foundation Model API** — make sure that's available in your workspace.

---

## Resources (all public)

- **Genie Workbench (open source):** https://github.com/databricks-solutions/databricks-genie-workbench
- **Getting your Genie Spaces production-ready with Genie Workbench (Medium):** https://medium.com/@jenny.j.park/getting-your-genie-spaces-production-ready-with-genie-workbench-e9e7db8a88ca
- **Curate an effective Genie Space (docs):** https://docs.databricks.com/aws/en/genie/best-practices
- **Create and manage a Genie Space (docs):** https://docs.databricks.com/aws/en/genie/set-up
- **Build a knowledge store for Genie (docs):** https://docs.databricks.com/aws/en/genie/knowledge-store
- **From Data to Dialogue — best‑practices guide (blog):** https://www.databricks.com/blog/data-dialogue-best-practices-guide-building-high-performing-genie-spaces
- **How to build production‑ready Genie Spaces (blog):** https://www.databricks.com/blog/how-build-production-ready-genie-spaces-and-build-trust-along-way
- **Building confidence with benchmarks & Ask Review (blog):** https://www.databricks.com/blog/building-confidence-your-genie-space-benchmarks-and-ask-review

---

*Ready to run it? Reach out to your Databricks account team to schedule a facilitated session.*
