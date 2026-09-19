# Ingestion codegen prototype (CSV → Databricks Bronze)

This is Phase 1 from the PRD, stripped to just the core engine: no UI, no
auth, no deploy automation. It proves the metadata-driven codegen idea
works before any of the SaaS shell gets built around it.

## What it does

1. Reads a sample of your CSV and infers column names + types.
2. Shows you that schema and asks you to confirm it (the same review
   checkpoint the full product will have in its UI).
3. Generates a real Databricks notebook (`.py` source format) that
   creates the Bronze table and ingests the file into it.

## What it does NOT do (yet — by design)

- Doesn't call the Databricks API. You import and run the generated
  notebook yourself. That's the next step once this one is proven.
- Doesn't handle JSON/Parquet/COBOL copybook/XML — those are separate
  inferrers to add later; the `INFERRERS` registry in `codegen.py` is
  exactly where each one plugs in.
- Doesn't touch your actual data — it only reads a local sample to infer
  structure, matching the control-plane-only principle from the PRD.

## Step 1: Generate the notebook

```bash
pip install -r requirements.txt

python cli.py \
  --source-type csv \
  --file sample_data/sample.csv \
  --catalog <your_catalog> \
  --schema bronze \
  --table customers \
  --output notebooks/customers_ingest.py
```

Replace `<your_catalog>` with a Unity Catalog catalog you have access to
in your Databricks account (run `SHOW CATALOGS` in a Databricks SQL
editor if you're not sure of the name — commonly `main` on a fresh
workspace).

You'll see the discovered schema printed to the console and a y/n prompt
— confirm it, and the notebook gets written to
`notebooks/customers_ingest.py`.

Try it against your own CSV too, not just the sample — that's the real
test of whether the type inference holds up.

## Step 2: Run it in your Databricks workspace

The generated file is in Databricks' notebook source format, so you can
import it directly:

1. In your Databricks workspace: **Workspace → Import**, upload
   `notebooks/customers_ingest.py`. Databricks recognizes the
   `# Databricks notebook source` header and `# COMMAND ----------` cell
   markers automatically.
2. **The file references a local path** (`sample_data/sample.csv` on your
   machine) — Databricks can't read that. Upload the CSV to a volume or
   DBFS path first (e.g. **Catalog → your catalog → Volumes → Upload**,
   or `dbutils.fs.cp` from a notebook if you already have it in cloud
   storage), then edit the `source_path` widget default at the top of the
   notebook to that Databricks-accessible path before running.
3. Attach the notebook to a running cluster and run all cells.
4. Confirm the Bronze table got created and populated:
   ```sql
   SELECT * FROM <your_catalog>.bronze.customers;
   ```
   Row count should match the source file, and you should see the
   `_ingested_at`, `_source_file`, `_batch_id` lineage columns populated.

## What "success" looks like for this test

- The inferred schema matched your CSV's actual columns and reasonable
  types (dates might come back as `STRING` rather than `TIMESTAMP` — the
  current CSV inferrer doesn't attempt date parsing yet, which is a fair
  thing to tighten in the next iteration if it matters to you).
- The generated notebook ran without edits beyond the source path.
- The Bronze table exists with the right structure and the right row
  count.

If any of that breaks, that's useful — it tells us exactly which part of
the codegen (type inference, DDL generation, or the write logic) needs
fixing before we build automated deployment on top of it.

## Step 3: Automate deployment (Databricks Jobs API)

Once you've confirmed the generated notebook runs correctly by hand
(Step 2), this step automates that: uploads the notebook into your
workspace and creates a scheduled Job, without you touching the UI.

### One-time setup: a Databricks token

1. In your Databricks workspace: **User icon (top right) → Settings →
   Developer → Access tokens → Generate new token.** Copy it immediately
   — you won't be able to see it again.
2. Set two environment variables in your terminal (don't pass these as
   CLI flags — that puts them in your shell history):
   ```bash
   export DATABRICKS_HOST="https://<your-workspace>.cloud.databricks.com"
   export DATABRICKS_TOKEN="<the token you just generated>"
   ```
   (Windows PowerShell: `$env:DATABRICKS_HOST = "..."` / `$env:DATABRICKS_TOKEN = "..."`)

   This is fine for testing. It is **not** how the real SaaS should handle
   credentials long-term — see PRD Section 8 (vault-backed storage, no
   raw tokens sitting in an environment or database). This is a
   deliberate shortcut for prototyping, not the production design.

### Upload your CSV to a Volume first

The job needs to read the real file from somewhere Databricks can reach
— not your laptop. Upload it via **Catalog → your catalog → your schema
→ Volumes → Upload**, and note the resulting path (something like
`/Volumes/main/bronze/landing/customers.csv`).

### Deploy and schedule

```bash
python deploy_cli.py \
  --notebook notebooks/customers_ingest.py \
  --workspace-path /Workspace/Users/<your-email>/ingestion/customers_ingest \
  --job-name customers-daily-ingest \
  --source-path /Volumes/main/bronze/landing/customers.csv \
  --frequency daily --time 02:00 --timezone America/Chicago \
  --run-now
```

Note that `--source-path` here is the **Volumes** path from the step
above — it can (and usually will) be different from the local file you
used to originally infer the schema in Step 1. The schema was captured
once at generation time; this just tells the deployed job where the real
file lives when it actually runs.

`--run-now` triggers an immediate run so you don't have to wait for 2am
to find out if it worked — check the printed job URL for status.

### What "success" looks like for this test

- The job appears in your workspace's **Workflows** tab.
- The triggered run (from `--run-now`) succeeds, and the Bronze table
  gets the expected row count.
- Re-running `deploy_cli.py` with the same `--job-name` **updates** the
  existing job instead of creating a duplicate — worth testing
  deliberately, since duplicate jobs would be a real problem once this is
  running unattended on a schedule.

## COBOL / mainframe setup (Cobrix)

COBOL sources (via the Streamlit app, `streamlit run app.py` — not `cli.py`,
which doesn't support this source type) are always remote: Cobrix, the
library that understands copybook syntax and EBCDIC decoding, is a
Spark/JVM data source, not something this app can run locally. You need
an existing Databricks cluster with it attached, for both schema
discovery and the deployed job.

1. **Create a cluster** — Compute → Create compute. Use **Single User**
   access mode (Standard/Shared mode needs an extra Unity Catalog
   allowlist step from a metastore admin before a Maven library will
   install — Single User skips that). Not serverless — serverless isn't
   known to support attaching custom JARs.
2. **Attach the Cobrix library** — open the cluster → **Libraries** tab →
   **Install New** → source **Maven** → coordinates:
   - `za.co.absa.cobrix:spark-cobol_2.12:2.11.0` for **Spark 3.x / DBR
     16.x and below**
   - `za.co.absa.cobrix:spark-cobol_2.13:2.11.0` for **Spark 4.0+ / DBR
     17.x and above**

   **Get the Scala suffix wrong and it doesn't fail cleanly** — this bit
   the first real test of this feature (DBR 17.3 / Spark 4.0, which
   dropped Scala 2.12 entirely): every discovery/deploy run against that
   cluster failed with a generic `RunLifeCycleState.INTERNAL_ERROR` /
   "Workload failed" instead of a clear version-mismatch message. If
   COBOL discovery or deploy fails outright with that kind of error,
   check the Libraries tab status and the Scala suffix before anything
   else.
3. Wait for the library to show **Installed** (green). Restart the
   cluster if it was already running when you installed it.
4. Copy the **cluster ID** from the cluster's URL
   (`.../compute/clusters/<cluster-id>`) — paste it into both the
   "Existing cluster ID" field in the app's Source section (used for
   discovery) and the separate one in the Deploy section (used for the
   scheduled job — it defaults to the same cluster but is editable if you
   want the actual job to run somewhere else; wherever it runs still
   needs Cobrix attached).

## Next step after this works

Deployment automation for CSV → Databricks is the last piece of that one
connector's full loop: generate → review → deploy → schedule, all
working end to end. From here the next increment is either adding a
second source type (JSON is next per the plan) or a second target
(Snowflake) — come back and we'll pick based on what you want to
prioritize.
