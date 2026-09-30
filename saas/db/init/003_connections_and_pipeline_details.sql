-- Per-org Databricks connection, plus the extra detail a deployed pipeline needs.
-- Safe to run twice (see 002 for how migrations are applied to an existing database).

-- One Databricks workspace connection per organization. The token is stored ENCRYPTED
-- (Fernet, application-side); the key lives outside the database, so a database dump alone
-- doesn't expose working credentials. This is the local-dev stand-in for the secrets
-- vault the PRD calls for (Section 8): the app talks to it through one small interface
-- (app/services/secrets.py), so a real vault replaces it without touching the rest.
CREATE TABLE IF NOT EXISTS org_connections (
    org_id           UUID PRIMARY KEY REFERENCES orgs (id) ON DELETE CASCADE,
    host             TEXT NOT NULL,
    token_ciphertext TEXT NOT NULL,
    created_by       UUID REFERENCES users (id) ON DELETE SET NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- What was deployed, exactly: the job parameters (source_path / copybook_path), the cluster
-- it runs on (required for COBOL), and a hash of the notebook that was reviewed and shipped.
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS job_parameters   JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS cluster_id       TEXT;
ALTER TABLE pipelines ADD COLUMN IF NOT EXISTS notebook_sha256  TEXT;
