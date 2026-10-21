-- Module 3c: remember each table's column list.
--
-- A Bronze pipeline is generated from a Raw TABLE (not from a file), so the registry has to know
-- what that table's columns are. Stored as [{"name": ..., "sql_type": ...}] in table order, and
-- only ever the source columns (never the _ingested_at/_source_file/_batch_id lineage columns).
-- Safe to run more than once.
ALTER TABLE data_assets ADD COLUMN IF NOT EXISTS columns JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Tables registered before this change: take the columns from the source version their pipeline
-- was built from. Only fills tables that don't have a column list yet.
UPDATE data_assets a
SET columns = COALESCE(
    (
        SELECT jsonb_agg(jsonb_build_object('name', c ->> 'name', 'sql_type', c ->> 'sql_type') ORDER BY ord)
        FROM jsonb_array_elements(s.schema_json) WITH ORDINALITY AS t(c, ord)
    ),
    '[]'::jsonb
)
FROM pipelines p
JOIN pipeline_sources ps ON ps.pipeline_id = p.id
JOIN source_registry s ON s.id = ps.source_registry_id
WHERE a.produced_by = p.id
  AND a.table_name = ps.table_name
  AND a.layer = 'raw'
  AND a.columns = '[]'::jsonb;
