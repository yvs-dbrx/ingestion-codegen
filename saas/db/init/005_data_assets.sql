-- Module 3a: the table registry ("data assets") — every table the platform produces or relies
-- on, with its layer and lineage. Safe to run more than once.
--
-- This is the catalog the Silver builder reads from and, later, the marketplace lists from.
-- It holds METADATA only (names, layer, lineage, last-known counts); table contents stay in the
-- customer's own Databricks workspace.
--
-- Separate concern, deliberately: the registry says what EXISTS and its latest state; what
-- HAPPENED on each execution stays in the append-only pipeline_runs log (see `metrics` below).

-- A pipeline now belongs to a layer and carries the spec it was generated from. Everything
-- deployed so far is Bronze, so the defaults are right for existing rows.
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS layer TEXT NOT NULL DEFAULT 'bronze';
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS spec JSONB NOT NULL DEFAULT '{}'::jsonb;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_pipelines_layer') THEN
        ALTER TABLE pipelines
            ADD CONSTRAINT ck_pipelines_layer CHECK (layer IN ('bronze', 'silver', 'gold'));
    END IF;
END $$;

-- Structured per-run counts (rows read / deduplicated / quarantined / inserted / updated ...).
-- rows_by_table (001) stays for the simple per-table totals.
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS metrics JSONB;

CREATE TABLE IF NOT EXISTS data_assets (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         UUID NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    catalog        TEXT NOT NULL,
    schema_name    TEXT NOT NULL,
    table_name     TEXT NOT NULL,
    -- bronze/silver/gold are produced by a pipeline; 'reference' is an input the customer
    -- maintains themselves (e.g. a source-key -> master-key crosswalk from identity resolution).
    layer          TEXT NOT NULL CHECK (layer IN ('bronze', 'silver', 'gold', 'reference')),
    -- Which upstream system the rows come from (e.g. 'legacy_cobol_crm'): half of the key a
    -- crosswalk is looked up by. NULL until a layer that needs it sets it.
    source_system  TEXT,
    -- For Bronze: the file or folder it is loaded from. Display/lineage context only.
    origin         TEXT,
    -- Deleting a pipeline never drops the table in Databricks, so the asset outlives it.
    produced_by    UUID REFERENCES pipelines (id) ON DELETE SET NULL,
    -- Latest known state, refreshed from run results. NULL = never measured.
    row_count      BIGINT,
    last_run_at    TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, catalog, schema_name, table_name),
    -- lets lineage rows prove both ends belong to the SAME organization (below)
    UNIQUE (id, org_id)
);
CREATE INDEX IF NOT EXISTS idx_data_assets_org_layer ON data_assets (org_id, layer);
CREATE INDEX IF NOT EXISTS idx_data_assets_producer ON data_assets (produced_by);

-- Table-level lineage: downstream is built from upstream. The composite foreign keys make it
-- impossible (not merely unchecked) to link assets across organizations.
CREATE TABLE IF NOT EXISTS asset_lineage (
    org_id         UUID NOT NULL,
    downstream_id  UUID NOT NULL,
    upstream_id    UUID NOT NULL,
    PRIMARY KEY (downstream_id, upstream_id),
    CHECK (downstream_id <> upstream_id),
    FOREIGN KEY (downstream_id, org_id) REFERENCES data_assets (id, org_id) ON DELETE CASCADE,
    FOREIGN KEY (upstream_id, org_id) REFERENCES data_assets (id, org_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_asset_lineage_upstream ON asset_lineage (upstream_id);

-- Existing Bronze pipelines get their tables registered. Idempotent: ON CONFLICT leaves anything
-- already registered alone.
INSERT INTO data_assets (org_id, catalog, schema_name, table_name, layer, origin, produced_by)
SELECT p.org_id, p.catalog, p.target_schema, ps.table_name, 'bronze', s.source_path, p.id
FROM pipelines p
JOIN pipeline_sources ps ON ps.pipeline_id = p.id
JOIN source_registry s ON s.id = ps.source_registry_id
ON CONFLICT (org_id, catalog, schema_name, table_name) DO NOTHING;
