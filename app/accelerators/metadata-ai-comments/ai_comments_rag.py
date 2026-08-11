# Databricks notebook source
# MAGIC %md
# MAGIC # AI-generated, glossary-grounded column comments (review → apply)
# MAGIC
# MAGIC Bulk-generates Unity Catalog **column comments** grounded in *your own* data
# MAGIC dictionary / standards docs, using Vector Search + `ai_query`, then applies them
# MAGIC **only after a steward approves** them. This fills the gap that AI Comments has no
# MAGIC API endpoint for programmatic, grounded, bulk generation.
# MAGIC
# MAGIC **Flow:** docs → Vector Search index → generate drafts into a *review* table →
# MAGIC steward approves → apply `ALTER COLUMN … COMMENT`.
# MAGIC
# MAGIC Tested on DBR 15.4 LTS / 16.x. Part of the **Genie Ontology Readiness** app
# MAGIC (Metadata Richness pillar). Re-run the readiness assessment afterwards to see the
# MAGIC comment-coverage signal — and the Metadata score — rise.

# COMMAND ----------

# MAGIC %pip install --upgrade --quiet databricks-vectorsearch
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Parameters
# MAGIC Set the scope and the docs source. Leave `tables` empty to scope to every table
# MAGIC in the schema. Point `docs_source_table` at an existing table with a `text`
# MAGIC column (your glossary/standards), or set `docs_volume` to a Volume of `.txt`
# MAGIC files. If neither is set, a tiny inline example is used so the notebook runs.

# COMMAND ----------

dbutils.widgets.text("catalog", "", "Target catalog")
dbutils.widgets.text("schema", "", "Target schema")
dbutils.widgets.text("tables", "", "Tables (comma-separated; empty = all in schema)")
dbutils.widgets.text("docs_source_table", "", "Existing docs table (col: text)  [optional]")
dbutils.widgets.text("docs_volume", "", "Volume path of .txt docs  [optional]")
dbutils.widgets.text("vs_endpoint", "genie-readiness-vs", "Vector Search endpoint name")
dbutils.widgets.text("embedding_endpoint", "databricks-bge-large-en", "Embedding serving endpoint")
dbutils.widgets.text("llm_endpoint", "databricks-meta-llama-3-3-70b-instruct", "LLM serving endpoint")
dbutils.widgets.dropdown("mode", "generate", ["generate", "apply"], "Mode: generate drafts or apply approved")

catalog = dbutils.widgets.get("catalog").strip()
schema = dbutils.widgets.get("schema").strip()
tables = [t.strip() for t in dbutils.widgets.get("tables").split(",") if t.strip()]
docs_source_table = dbutils.widgets.get("docs_source_table").strip()
docs_volume = dbutils.widgets.get("docs_volume").strip()
vs_endpoint = dbutils.widgets.get("vs_endpoint").strip()
embedding_endpoint = dbutils.widgets.get("embedding_endpoint").strip()
llm_endpoint = dbutils.widgets.get("llm_endpoint").strip()
mode = dbutils.widgets.get("mode").strip()

assert catalog and schema, "Set the catalog and schema widgets."

# Derived names (all kept inside the target schema).
docs_table = docs_source_table or f"{catalog}.{schema}._readiness_docs"
vs_index = f"{catalog}.{schema}._readiness_col_docs_idx"
review_table = f"{catalog}.{schema}._comment_review"

for k, v in {
    "catalog": catalog, "schema": schema, "docs_table": docs_table,
    "vs_index": vs_index, "review_table": review_table,
    "llm_endpoint": llm_endpoint,
}.items():
    spark.conf.set(f"acc.{k}", v)

print(f"Scope:   {catalog}.{schema}  tables={tables or 'ALL'}")
print(f"Mode:    {mode}")
print(f"Docs:    {docs_table}{'  (existing)' if docs_source_table else '  (managed by this notebook)'}")
print(f"Review:  {review_table}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Build the docs table (skipped if you supplied `docs_source_table`)
# MAGIC The docs table needs a `text` column and Change Data Feed enabled (for the
# MAGIC Delta-sync Vector Search index).

# COMMAND ----------

from pyspark.sql.functions import monotonically_increasing_id

if not docs_source_table:
    if docs_volume:
        src = (spark.read.text(docs_volume, wholetext=True)
               .withColumnRenamed("value", "text"))
    else:
        # Minimal inline example so the notebook is runnable end-to-end.
        src = spark.createDataFrame(
            [("Region codes use ISO 3166-1 alpha-2 (e.g. AL = Albania). Quantities are in thousands of units.",),
             ("The language column uses ISO 639 three-letter codes (e.g. ENG, FRA).",)],
            ["text"],
        )
    (src.withColumn("id", monotonically_increasing_id())
        .write.format("delta")
        .option("delta.enableChangeDataFeed", "true")
        .option("overwriteSchema", "true")
        .mode("overwrite")
        .saveAsTable(docs_table))
    print(f"Wrote docs table {docs_table}")
else:
    # Ensure CDF is on for an existing docs table (required by delta-sync index).
    try:
        spark.sql(f"ALTER TABLE {docs_table} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
    except Exception as e:
        print(f"(could not enable CDF on {docs_table}: {e}) — ensure it is enabled.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Vector Search endpoint + index over the docs

# COMMAND ----------

from databricks.vector_search.client import VectorSearchClient
import time

vsc = VectorSearchClient(disable_notice=True)

existing = {e["name"] for e in vsc.list_endpoints().get("endpoints", [])}
if vs_endpoint not in existing:
    vsc.create_endpoint(name=vs_endpoint, endpoint_type="STANDARD")
    print(f"Creating endpoint {vs_endpoint} …")

# Create the delta-sync index if absent, then wait until ONLINE.
try:
    idx = vsc.get_index(endpoint_name=vs_endpoint, index_name=vs_index)
except Exception:
    idx = vsc.create_delta_sync_index(
        endpoint_name=vs_endpoint,
        source_table_name=docs_table,
        index_name=vs_index,
        pipeline_type="TRIGGERED",
        primary_key="id",
        embedding_source_column="text",
        embedding_model_endpoint_name=embedding_endpoint,
    )
    print(f"Creating index {vs_index} …")

while not idx.describe().get("status", {}).get("detailed_state", "").startswith("ONLINE"):
    print("Waiting for index to be ONLINE…")
    time.sleep(10)
print("Index ONLINE")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Generate drafts → review table  (mode = generate)
# MAGIC For every in-scope column **missing a comment**, retrieve the most relevant
# MAGIC doc snippet and draft a grounded comment. Drafts are written to the review
# MAGIC table tagged `validated = 'no'` — nothing is applied yet.

# COMMAND ----------

if mode == "generate":
    table_filter = ""
    if tables:
        in_list = ", ".join("'" + t.replace("'", "''") + "'" for t in tables)
        table_filter = f"AND table_name IN ({in_list})"

    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {review_table} (
            table_catalog STRING, table_schema STRING, table_name STRING,
            column_name STRING, draft_comment STRING, validated STRING
        )
    """)

    # Generate grounded drafts via VECTOR_SEARCH + ai_query.
    spark.sql(f"""
        CREATE OR REPLACE TEMP VIEW _drafts AS
        SELECT
            c.table_catalog, c.table_schema, c.table_name, c.column_name,
            ai_query(
                '{llm_endpoint}',
                CONCAT(
                    'Write a concise, business-friendly column comment. Plain text only ',
                    '(text, commas, periods). Add an example if helpful. Validate any claim ',
                    'against the data type. Table: ', c.table_name,
                    '. Column: ', c.column_name, '. Data type: ', c.full_data_type,
                    '. Relevant internal documentation (use only what fits the column): ',
                    search.text
                )
            ) AS draft_comment
        FROM system.information_schema.columns c,
        LATERAL (
            SELECT text FROM VECTOR_SEARCH(
                index => '{vs_index}', query_text => c.column_name, num_results => 1
            )
        ) AS search
        WHERE c.table_catalog = '{catalog}'
          AND c.table_schema = '{schema}'
          AND (c.comment IS NULL OR c.comment = '')
          {table_filter}
    """)

    # Insert only columns not already pending review.
    spark.sql(f"""
        INSERT INTO {review_table}
        SELECT d.*, 'no' AS validated FROM _drafts d
        LEFT ANTI JOIN {review_table} r
          ON r.table_name = d.table_name AND r.column_name = d.column_name
    """)
    n = spark.table(review_table).where("validated = 'no'").count()
    print(f"{n} drafts awaiting review in {review_table}")
    display(spark.table(review_table).where("validated = 'no'"))
else:
    print("mode != generate — skipping draft generation.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Review (human in the loop)
# MAGIC A steward reviews the drafts and **approves** good ones by setting
# MAGIC `validated = 'yes'` (edit `draft_comment` in place if needed). Example:
# MAGIC ```sql
# MAGIC -- approve everything for one table after reading it
# MAGIC UPDATE ${acc.review_table} SET validated = 'yes'
# MAGIC WHERE table_name = 'orders' AND validated = 'no';
# MAGIC -- or fix one then approve
# MAGIC UPDATE ${acc.review_table}
# MAGIC SET draft_comment = 'Net order amount in USD, excluding tax.', validated = 'yes'
# MAGIC WHERE table_name = 'orders' AND column_name = 'net_amount';
# MAGIC ```

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT validated, COUNT(*) AS columns FROM ${acc.review_table} GROUP BY validated

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Apply approved comments  (mode = apply)
# MAGIC Applies `ALTER COLUMN … COMMENT` only for rows marked `validated = 'yes'`, then
# MAGIC marks them `applied` so re-runs are idempotent.

# COMMAND ----------

if mode == "apply":
    approved = spark.table(review_table).where("validated = 'yes'").collect()
    applied = 0
    for r in approved:
        fq = f"`{r['table_catalog']}`.`{r['table_schema']}`.`{r['table_name']}`"
        comment = (r["draft_comment"] or "").replace("'", "''")
        spark.sql(f"ALTER TABLE {fq} ALTER COLUMN `{r['column_name']}` COMMENT '{comment}'")
        applied += 1
    if approved:
        spark.sql(f"UPDATE {review_table} SET validated = 'applied' WHERE validated = 'yes'")
    print(f"Applied {applied} approved comments.")
else:
    print("mode != apply — set the mode widget to 'apply' after reviewing.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Done
# MAGIC Re-run the **Genie Ontology Readiness** assessment — the Metadata Richness
# MAGIC pillar's *Columns commented* / *Tables commented* signals (and the pillar score)
# MAGIC should climb. Keep the review table as your audit trail; delete the temporary
# MAGIC Vector Search index when finished if you no longer need it:
# MAGIC ```python
# MAGIC # vsc.delete_index(index_name=vs_index)
# MAGIC ```
