-- Module 1d: run tracking. Safe to run more than once.
--
-- pipeline_runs already has status / started_at / finished_at / error_message / rows_by_table
-- (001). This adds a link to the run's page in Databricks, a created_at (a run we started
-- exists before Databricks reports a start time), and — most importantly — a uniqueness rule so
-- the same Databricks run can never be recorded twice, however many times we sync.

ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS databricks_run_url TEXT;
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS uq_pipeline_runs_databricks_run
    ON pipeline_runs (pipeline_id, databricks_run_id)
    WHERE databricks_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_pipeline_created
    ON pipeline_runs (pipeline_id, created_at DESC);
