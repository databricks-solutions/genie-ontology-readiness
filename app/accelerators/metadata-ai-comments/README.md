# Accelerator: AI-generated, glossary-grounded column comments

**Pillar:** Metadata Richness · **Type:** notebook · **Effort:** ~1 hour

Bulk-generates Unity Catalog column comments grounded in *your own* data
dictionary / standards docs (via Vector Search + `ai_query`), then applies them
only after a steward approves. Fills the gap that AI Comments has no API endpoint
for programmatic, grounded, bulk generation.

## Why it raises the readiness score

Genie reads comments as ground truth, so comment coverage strongly predicts answer
accuracy. This notebook lifts the **Metadata Richness** pillar's *Columns
commented* / *Tables commented* signals. Re-run the readiness assessment after
applying to see the score climb.

## Prerequisites

- Vector Search enabled in the workspace
- A serving endpoint for embeddings (default `databricks-bge-large-en`) and an LLM
  (default `databricks-meta-llama-3-3-70b-instruct`)
- `SELECT`/`MODIFY` on the target catalog (to read columns and `ALTER` comments)
- Your data-dictionary / standards text in a table (column `text`) or a Volume of
  `.txt` files. If you supply neither, a tiny inline example runs so you can try it.

## Use it

Import into the customer workspace:

```bash
databricks workspace import \
  --file ai_comments_rag.py \
  --language PYTHON --format SOURCE \
  /Workspace/Users/<you>/ai_comments_rag
```

(Or download it from the app's **Learn → Metadata Richness → Accelerators** card.)

Then in the notebook:

1. Set the widgets: `catalog`, `schema`, optional `tables`, and a docs source
   (`docs_source_table` or `docs_volume`).
2. Run with `mode = generate` — drafts land in `<catalog>.<schema>._comment_review`
   tagged `validated = 'no'`. **Nothing is applied yet.**
3. Review the drafts (cell 5) and set `validated = 'yes'` on the good ones.
4. Switch `mode = apply` and run — only approved rows get
   `ALTER COLUMN … COMMENT`; applied rows are marked so re-runs are idempotent.
5. Re-run the readiness assessment to confirm the lift.

The review table is your audit trail. Delete the temporary Vector Search index
when finished if you no longer need it.
