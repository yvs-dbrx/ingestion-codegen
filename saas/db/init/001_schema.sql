-- Control-plane schema for the ingestion SaaS module (PRD Sections 2, 5.1, 5.7, 5.8).
--
-- Stores metadata ONLY — accounts, pipeline configs, discovered schemas, run
-- status. Customer row data never lives here (PRD Section 2: control plane vs.
-- data plane).
--
-- Three deliberately separate concerns (borrowed from the layered control-table /
-- audit-table split in Databricks' metadata-driven ETL pattern):
--   * source_registry  what a source file/copybook looks like, versioned (SCD Type-2)
--   * pipelines        the current configuration of each deployed job
--   * pipeline_runs    what happened each time a job ran (execution audit trail)

-- ---------------------------------------------------------------------------
-- Tenancy, auth, and plan limits
-- ---------------------------------------------------------------------------

-- Plan limits live in data, not code (PRD 5.8: Free 3 / Team 10 / Pro 20).
CREATE TABLE tiers (
    name           TEXT PRIMARY KEY,
    pipeline_limit INT  NOT NULL CHECK (pipeline_limit >= 0)
);
INSERT INTO tiers (name, pipeline_limit) VALUES ('free', 3), ('team', 10), ('pro', 20);

CREATE TABLE orgs (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,
    tier       TEXT NOT NULL DEFAULT 'free' REFERENCES tiers (name),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL,
    password_hash TEXT NOT NULL,   -- a salted hash from the app (argon2/bcrypt), never a password
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Case-insensitive uniqueness: Jane@x.com and jane@x.com are the same account.
CREATE UNIQUE INDEX uq_users_email ON users (lower(email));

CREATE TABLE org_members (
    org_id    UUID NOT NULL REFERENCES orgs (id)  ON DELETE CASCADE,
    user_id   UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role      TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, user_id)
);
CREATE INDEX idx_org_members_user ON org_members (user_id);

-- ---------------------------------------------------------------------------
-- Source registry: one row per VERSION of each unique source file / copybook
-- ---------------------------------------------------------------------------
--
-- SCD Type-2: when re-discovery finds a different schema for the same source,
-- the app closes the active row (record_end_ts set, record_is_active = false)
-- and inserts a new active row with version + 1 — so drift is detectable
-- (compare schema_hash) and every shape a source has ever had is kept.
--
-- "Unique source" identity = (org, source_type, source_path, schema_definition_path):
--   source_type            csv | json | xml | cobol   (an XSD-derived xml source is source_type
--                          'xml' with discovery_config {"discovery_mode": "xsd", ...})
--   source_path            the data file this pipeline reads (multi-file: one row per file)
--   schema_definition_path the file that DEFINES the schema when it isn't the data itself —
--                          the copybook for cobol, the XSD for XSD-derived xml; NULL otherwise
CREATE TABLE source_registry (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                 UUID NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    source_type            TEXT NOT NULL,
    source_path            TEXT NOT NULL,
    schema_definition_path TEXT,
    -- delimiter / has_header / multiline / row_tag / is_record_sequence / is_text / ...
    -- so a later drift re-check can re-run discovery with the SAME settings.
    discovery_config       JSONB NOT NULL DEFAULT '{}'::jsonb,
    schema_json            JSONB NOT NULL,   -- the discovered column list
    schema_hash            TEXT  NOT NULL,   -- hash of schema_json, for cheap drift comparison
    version                INT   NOT NULL DEFAULT 1 CHECK (version >= 1),
    record_start_ts        TIMESTAMPTZ NOT NULL DEFAULT now(),
    record_end_ts          TIMESTAMPTZ,      -- NULL while this is the active version
    record_is_active       BOOLEAN NOT NULL DEFAULT true,
    last_verified_at       TIMESTAMPTZ NOT NULL DEFAULT now(),  -- bumped when a re-check finds no change
    discovered_by          UUID REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT ck_source_registry_active_flag
        CHECK (record_is_active = (record_end_ts IS NULL)),
    CONSTRAINT ck_source_registry_time_order
        CHECK (record_end_ts IS NULL OR record_end_ts >= record_start_ts)
);

-- At most ONE active version per unique source...
CREATE UNIQUE INDEX uq_source_registry_one_active
    ON source_registry (org_id, source_type, source_path, COALESCE(schema_definition_path, ''))
    WHERE record_is_active;

-- ...and version numbers never repeat within a source.
CREATE UNIQUE INDEX uq_source_registry_version
    ON source_registry (org_id, source_type, source_path, COALESCE(schema_definition_path, ''), version);

CREATE INDEX idx_source_registry_org ON source_registry (org_id);

CREATE VIEW source_registry_current AS
    SELECT * FROM source_registry WHERE record_is_active;

-- ---------------------------------------------------------------------------
-- Pipelines (deployed Databricks jobs) and the sources they were generated from
-- ---------------------------------------------------------------------------

CREATE TABLE pipelines (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id             UUID NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    job_name           TEXT NOT NULL,
    mode               TEXT NOT NULL DEFAULT 'single' CHECK (mode IN ('single', 'multi')),
    catalog            TEXT NOT NULL,
    target_schema      TEXT NOT NULL,
    schedule_frequency TEXT CHECK (schedule_frequency IN ('hourly', 'daily', 'weekly')),
    schedule_time      TIME,
    schedule_timezone  TEXT NOT NULL DEFAULT 'UTC',
    workspace_path     TEXT,
    databricks_job_id  BIGINT,
    databricks_job_url TEXT,
    -- The exact reviewed-and-approved notebook that was deployed (PRD Section 7:
    -- generate once, run the approved artifact unchanged).
    notebook_source    TEXT,
    created_by         UUID REFERENCES users (id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, job_name)
);
CREATE INDEX idx_pipelines_org ON pipelines (org_id);

-- Which source VERSION each target table was generated from. Pointing at a
-- specific version row (not "the source") is what makes drift visible: if the
-- source's active version is newer than the one referenced here, the pipeline
-- needs review. A version still in use by a pipeline can't be deleted.
-- The source_registry FK is DEFERRED to commit on purpose: deleting a whole org
-- cascades to both its pipelines and its source versions, and Postgres runs each
-- cascade as its own inner statement — so an immediate check (RESTRICT, or even
-- NO ACTION) fires on the source versions before the pipeline links are gone and
-- makes org deletion fail. Checked at commit, the cascade has finished, org
-- deletion works, and deleting one in-use version by itself is still rejected.
-- (Both immediate variants were tried and failed testing.)
CREATE TABLE pipeline_sources (
    pipeline_id        UUID NOT NULL REFERENCES pipelines (id) ON DELETE CASCADE,
    source_registry_id UUID NOT NULL REFERENCES source_registry (id) DEFERRABLE INITIALLY DEFERRED,
    table_name         TEXT NOT NULL,
    PRIMARY KEY (pipeline_id, source_registry_id),
    UNIQUE (pipeline_id, table_name)
);
CREATE INDEX idx_pipeline_sources_source ON pipeline_sources (source_registry_id);

-- ---------------------------------------------------------------------------
-- Execution audit trail (PRD 5.7)
-- ---------------------------------------------------------------------------

CREATE TABLE pipeline_runs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline_id       UUID NOT NULL REFERENCES pipelines (id) ON DELETE CASCADE,
    databricks_run_id BIGINT,
    triggered_by      TEXT NOT NULL DEFAULT 'schedule'
                      CHECK (triggered_by IN ('schedule', 'manual', 'deploy')),
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'running', 'success', 'failed', 'cancelled')),
    rows_by_table     JSONB,   -- {"customers": 1204, "orders": 88} — a job can load several tables
    error_message     TEXT,
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ
);
CREATE INDEX idx_pipeline_runs_pipeline ON pipeline_runs (pipeline_id, started_at DESC);

-- ---------------------------------------------------------------------------
-- Plan-limit check (PRD 5.8): the app reads can_create_pipeline before allowing
-- a new pipeline, replacing the prototype's pipeline_store.py counter.
-- ---------------------------------------------------------------------------

CREATE VIEW org_pipeline_usage AS
    SELECT o.id AS org_id,
           o.name AS org_name,
           o.tier,
           t.pipeline_limit,
           COUNT(p.id)                     AS pipeline_count,
           COUNT(p.id) < t.pipeline_limit  AS can_create_pipeline
    FROM orgs o
    JOIN tiers t ON t.name = o.tier
    LEFT JOIN pipelines p ON p.org_id = o.id
    GROUP BY o.id, o.name, o.tier, t.pipeline_limit;
