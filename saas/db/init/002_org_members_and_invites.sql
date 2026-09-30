-- Team membership: invite-by-email (PRD 5.1) and a per-org member cap.
--
-- Written to be SAFE TO RUN TWICE, because this is the first change to a database that
-- already exists: fresh databases get it automatically after 001 (Postgres runs every
-- file in this folder in name order, once, on first start), and an existing database gets it
-- by running this file by hand (see the command in saas/README-style notes / the chat).

-- Member cap lives in data next to the pipeline limit (plan limits are data, not code).
-- 5 for every plan today; a later plan can raise it with a single UPDATE.
ALTER TABLE tiers ADD COLUMN IF NOT EXISTS member_limit INT NOT NULL DEFAULT 5
    CHECK (member_limit >= 1);

-- An invitation to join an org. The raw token is shown to the inviting admin once and is
-- never stored — only its SHA-256, so a database leak can't be turned into working invites.
CREATE TABLE IF NOT EXISTS org_invites (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    email       TEXT NOT NULL,                       -- stored lowercase; the invitee can only join as this address
    role        TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    token_hash  TEXT NOT NULL UNIQUE,
    invited_by  UUID REFERENCES users (id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ,
    revoked_at  TIMESTAMPTZ,
    CONSTRAINT ck_org_invites_one_outcome CHECK (accepted_at IS NULL OR revoked_at IS NULL)
);

-- At most one open (not accepted, not revoked) invitation per org + address. Re-inviting the
-- same address revokes the earlier one first, so a lost link is simply re-issued.
CREATE UNIQUE INDEX IF NOT EXISTS uq_org_invites_one_open
    ON org_invites (org_id, email)
    WHERE accepted_at IS NULL AND revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_org_invites_org ON org_invites (org_id);
