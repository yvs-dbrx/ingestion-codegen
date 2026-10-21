-- Module 3b: the layer model becomes  raw -> bronze -> silver -> gold  (+ 'reference' inputs).
--
--   raw     every file loaded one-to-one, every load, duplicates included  (what the ingestion
--           pipelines have produced all along — they were labelled 'bronze' before this change)
--   bronze  distinct records only
--   silver  integrated with master keys, third normal form
--   gold    facts, dimensions, views and aggregates
--
-- Safe to run more than once. The one-time part (relabelling 'bronze' -> 'raw') is guarded: it
-- only runs while the layer rule doesn't yet know about 'raw'. Without that guard, re-running
-- this file after real Bronze (deduplicated) tables exist would wrongly relabel them as raw.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_pipelines_layer' AND strpos(pg_get_constraintdef(oid), '''raw''') > 0
    ) THEN
        ALTER TABLE pipelines DROP CONSTRAINT IF EXISTS ck_pipelines_layer;
        ALTER TABLE data_assets DROP CONSTRAINT IF EXISTS data_assets_layer_check;
        ALTER TABLE data_assets DROP CONSTRAINT IF EXISTS ck_data_assets_layer;

        ALTER TABLE pipelines
            ADD CONSTRAINT ck_pipelines_layer CHECK (layer IN ('raw', 'bronze', 'silver', 'gold'));
        ALTER TABLE data_assets
            ADD CONSTRAINT ck_data_assets_layer CHECK (layer IN ('raw', 'bronze', 'silver', 'gold', 'reference'));

        -- Everything registered so far was produced by the append-every-load ingestion notebooks.
        UPDATE pipelines SET layer = 'raw' WHERE layer = 'bronze';
        UPDATE data_assets SET layer = 'raw' WHERE layer = 'bronze';
    END IF;
END $$;

-- New ingestion pipelines are raw.
ALTER TABLE pipelines ALTER COLUMN layer SET DEFAULT 'raw';
