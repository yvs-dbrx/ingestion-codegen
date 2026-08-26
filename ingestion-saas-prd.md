# Product Requirement Document: Metadata-Driven Ingestion SaaS

**Status:** Draft v1
**Owner:** [fill in]
**Last updated:** 2026-08-14

---

## 1. Summary

A multi-tenant SaaS control plane that lets a user configure a data source
(CSV, JSON, SQL Server, Oracle, Salesforce, Workday, and more) and a target
(Databricks or Snowflake Bronze layer) entirely through a UI, then:

1. Reads the source's structure (headers, columns, types) to discover
   metadata automatically.
2. Generates the ingestion code for that specific source-target
   combination (PySpark notebook for Databricks, Snowpark/SQL for
   Snowflake).
3. Deploys that code into the customer's own Databricks or Snowflake
   environment.
4. Creates a scheduled job (daily, or another cadence chosen in the UI) to
   run it.

The product is sold as a subscription: **Free** (3 active ingestion
pipelines) and **Pro** ($20/month, 20 active ingestion pipelines), billed
through Polar.sh.

---

## 2. Core architecture decision: control plane vs. data plane

This is the single most important design choice in the product and it
should be treated as a hard requirement, not an implementation detail.

**The SaaS is a control plane. It never touches customer data.**

- The SaaS backend stores: user accounts, pipeline configurations, discovered
  schema *metadata* (column names/types, not row data), encrypted
  credential references, job status, and billing state.
- The generated PySpark/Snowpark code runs **inside the customer's own
  Databricks or Snowflake compute**. It reads directly from the configured
  source and writes directly to the Bronze table. Source data never
  transits through the SaaS's own servers.

Why this matters: it collapses the compliance surface enormously. If
customer row-level data flowed through the SaaS, this product would need
to be scoped for SOC 2 Type II, data residency guarantees, and breach
liability for every tenant's data on day one. As a metadata-and-code-only
control plane, the exposure is limited to configuration and credentials —
still serious, but a fundamentally smaller problem.

```
┌─────────────────────────────┐        ┌───────────────────────────────┐
│  Your SaaS (control plane)  │        │   Customer's cloud (data plane) │
│  - UI                       │ deploy │   - Databricks or Snowflake     │
│  - Metadata store           │ + sched├──►│   - Scheduled ingestion job    │
│  - Codegen engine           │        │   - Reads source, writes Bronze │
│  - Credential vault (refs)  │        │                                  │
└─────────────────────────────┘        └────────────┬────────────────────┘
                                                      │
                                          Sources: files, databases, APIs
```

---

## 3. Goals

- Let a non-engineer configure a working, scheduled ingestion pipeline in
  under 10 minutes, without writing code.
- Guarantee that every generated pipeline is schema-accurate on first run
  (Bronze table structure matches source structure) without manual DDL.
- Keep the SaaS out of the data path entirely (see Section 2).
- Support a clear self-serve upgrade path from Free to Pro.

### Non-goals (for v1)

- Transformation logic beyond Bronze-layer raw ingestion (no Silver/Gold
  layer modeling — that's a plausible v2 expansion, not v1 scope).
- Real-time/streaming ingestion. v1 is batch/scheduled only.
- On-prem source connectivity requiring VPN/private networking — assume
  cloud-reachable sources for v1 (revisit with a customer-hosted agent
  model if on-prem access becomes a hard requirement).

---

## 4. Users & personas

| Persona | Needs |
|---|---|
| Data analyst / BI person | Wants a CSV or a Salesforce object landed in Snowflake without waiting on a data engineer. |
| Small data team lead | Wants to stand up several source-to-Bronze pipelines quickly, without hand-writing boilerplate ingestion code for each one. |
| Solo founder / small company | Wants the cheapest possible path to a working lakehouse Bronze layer from a handful of sources. |

---

## 5. Functional requirements

### 5.1 Authentication & multi-tenant sessions

- Standard email/password + OAuth (Google, Microsoft) sign-up and login.
- Each user belongs to an organization (tenant). Organizations can invite
  additional users (v1: simple invite-by-email, role = admin or member).
- **Session history:** every login shows the user's previously configured
  pipelines, their run history, and status — this is core to the product,
  not an afterthought. The UI should default to a dashboard of existing
  pipelines on login, not a blank "create new" screen.

### 5.2 Source configuration UI

A source-type selector drives a dynamic form. Required fields per source
type:

| Source type | Required UI fields |
|---|---|
| CSV | File upload or object storage path (S3/ADLS/GCS URI), delimiter, header row present (yes/no), encoding |
| JSON | File upload or storage path, single-object vs. newline-delimited (NDJSON) |
| SQL Server | Host, port, database, auth method (SQL auth or Azure AD), credential, table or query |
| Oracle | Host, port, service name/SID, credential, table or query |
| Salesforce | OAuth connected-app login (not raw username/password — see Section 8), object name (e.g. `Account`, `Opportunity`), field selection (default: all fields) |
| Workday | RaaS report URL, credential, output format (the UI should note that this requires the customer to have already built a custom report in Workday exposing a RaaS endpoint — this is the highest-friction connector and should say so plainly in the UI, not just the docs) |
| (extensible) | New source types are added by extending the form-field schema + a codegen template — see Section 7 |

### 5.3 Target configuration UI

- Target platform: Databricks or Snowflake (radio choice).
- **Databricks:** workspace URL, auth (personal access token or OAuth
  service principal — recommend service principal), target catalog +
  schema (Unity Catalog), Bronze table name (default: auto-suggested from
  source name, editable).
- **Snowflake:** account identifier, auth (key-pair preferred over
  password — see Section 8), warehouse, database, schema, Bronze table
  name (same auto-suggest behavior).
- Schedule: frequency picker (daily is the default/primary case per the
  original spec; also support weekly and hourly for flexibility), time of
  day, timezone.

### 5.4 Metadata-driven schema discovery

- **CSV/JSON:** parse the header row / a sample of records to infer column
  names and types (string, integer, float, boolean, date/timestamp).
- **SQL Server/Oracle:** query `INFORMATION_SCHEMA.COLUMNS` (SQL Server) or
  `ALL_TAB_COLUMNS` (Oracle) for the target table, or run the user-provided
  query with a `LIMIT 0`/`WHERE 1=0` to get result-set metadata without
  pulling data.
- **Salesforce:** use the object `describe()` call to get field names and
  types directly — no data pull needed.
- **Workday RaaS:** fetch one page of the report and infer structure from
  the returned schema/fields, since RaaS doesn't expose a separate
  metadata-only endpoint.
- Discovered schema is shown back to the user in the UI for confirmation
  before code generation — this is a required checkpoint, not optional,
  since it's the basis for the generated DDL.

### 5.5 Code generation engine

See Section 7 for the full rule-based vs. AI design. Functionally:

- Given a confirmed source schema + target config, generate:
  - **Databricks:** a PySpark notebook that reads the source, applies the
    discovered schema, adds standard lineage columns (`_ingested_at`,
    `_source_name`, `_batch_id`), and writes to the target Unity Catalog
    Bronze table (creating it if it doesn't exist).
  - **Snowflake:** either a Snowpark Python script or a SQL script (`CREATE
    TABLE IF NOT EXISTS` + `COPY INTO` / a Snowpark DataFrame write),
    same lineage columns, targeting the specified Bronze table.
- Generated code is shown to the user for review before first deployment.

### 5.6 Deployment & orchestration

- **Databricks:** upload the notebook via the Workspace API, create a Job
  via the Jobs API 2.1 with the user-selected schedule, using Unity
  Catalog for the target table.
- **Snowflake:** deploy the script and create a **Snowflake Task** with a
  `CRON` schedule matching the user's selection — Snowflake Tasks are the
  right native primitive here, no external orchestrator needed for v1.
- On first deployment, require explicit user confirmation (see Section 7's
  validation gate). On subsequent scheduled runs, the job runs
  unattended using the already-approved code.

### 5.7 Job monitoring & run history

- Dashboard per pipeline: last run status (success/failure), row count
  ingested, duration, and a link to the underlying Databricks Job run /
  Snowflake Task history for full logs.
- Failure notifications via email at minimum; Slack webhook as a
  nice-to-have.

### 5.8 Subscription & billing

| Tier | Price | Active pipelines | $/pipeline |
|---|---|---|---|
| Free | $0 | 3 | — |
| Team | $10/month | 10 | $1.00 |
| Pro | $20/month | 20 | $1.00 |

- Limit is on **active configured pipelines**, not runs — a daily
  schedule already implies ~30 runs/month per pipeline regardless of
  tier, so metering on run count would be a different (and less
  intuitive) model. *(Confirmed with the user as the intended
  interpretation.)*
- Team tier priced to keep $/pipeline consistent with Pro ($1.00 each) —
  the free tier is the only non-linear step, which is standard SaaS
  practice (free tier is an acquisition cost, not priced to the same
  economics as paid tiers). This closes the gap for teams that land
  between 3 and 20 pipelines instead of forcing an early jump to Pro's
  full price. *(Confirmed with the user as an added tier.)*
- Deleting a pipeline frees up a slot; the app must enforce the
  create-pipeline limit itself — Polar's usage meters record consumption
  but don't block actions, so the "can this user create a 4th pipeline on
  Free tier" check is app-side logic, gated on a Polar customer meter read
  or a simple counted-pipelines-per-org query.
- Self-serve upgrade/downgrade via Polar's hosted Customer Portal.
- See Section 8 for the full billing integration design.

---

## 6. Non-functional requirements

- **Security:** no plaintext credentials at rest anywhere in the app
  database (see Section 8). TLS in transit everywhere. Least-privilege
  service accounts/tokens for every source and target connection.
- **Auditability:** every code generation, deployment, and job schedule
  change is logged with who/when/what, independent of the billing audit
  trail.
- **Isolation:** tenants must not be able to see each other's pipeline
  configs, schemas, or credentials, even metadata-only.
- **Reliability target (v1):** 99% successful scheduled-run rate,
  excluding failures caused by source-side outages/credential expiry
  outside the app's control.

---

## 7. Code generation design: hybrid, not pure rules or pure AI

**Recommendation: a template engine as the backbone, with AI used only for
two narrow, bounded jobs — never as the primary code generator for every
pipeline creation.**

### Why not pure rule-based

A fixed template per source-target combination is fast, deterministic, and
safe, but doesn't scale to "and more" — every new source type or every
unusual schema shape (deeply nested JSON, an Oracle table with an
unsupported data type, a Salesforce object with a field name that
collides with a Snowflake reserved word) requires a developer to write a
new template by hand before a customer can use it.

### Why not pure AI-generated code

This code creates tables and runs unattended in production, daily, against
a customer's real data platform. A subtly wrong AI-generated join, type
cast, or pagination handler doesn't fail loudly — it might just silently
ingest incomplete or malformed data for weeks before anyone notices.
Regenerating code via AI on every scheduled run would also make pipeline
behavior non-deterministic, which is the opposite of what a customer wants
from a job that's supposed to run the same way every day.

### The hybrid design

1. **Template library (primary path).** Jinja2-based templates, one per
   source-type × target-type combination, parameterized by the discovered
   schema. This handles the well-understood majority of cases
   deterministically, with zero AI involvement and zero API cost per
   pipeline.
2. **AI-assisted template authoring (bootstrapping new connectors).** When
   a user configures a source/target combination without an existing
   template, an AI drafts one. That draft does **not** go straight to
   production — it passes through automated validation (syntax check +
   dry run against the discovered schema sample) and a one-time human
   review before being promoted into the reusable template library. Over
   time this grows the template library instead of requiring a developer
   to hand-write every connector.
3. **AI-assisted schema edge cases (within an otherwise templated
   pipeline).** Narrow, bounded decisions: nested JSON flattening
   strategy, ambiguous type mapping, column name collisions with target
   reserved words. The AI must ground every decision in the actual
   discovered schema, must choose only from valid target-platform types,
   and must flag low-confidence cases for human resolution rather than
   guess — the same pattern used for the job-application tool's field
   resolver.
4. **Validation gate before every first deployment.** Regardless of which
   path generated the code, nothing deploys to the customer's environment
   without: a syntax/lint check, a dry run against the schema sample, and
   explicit user confirmation in the UI.
5. **Generate once, reuse on every scheduled run.** AI is not called again
   for the daily scheduled execution — the validated, approved code
   artifact from step 4 runs unchanged. Regeneration only happens if the
   user edits the pipeline config or the tool detects schema drift on the
   source, and drift should surface as a flagged review item, not a
   silent redeploy.

---

## 8. Security & credential handling

- **No plaintext credentials in the app database.** Store references to
  secrets held in a proper vault (AWS Secrets Manager, Azure Key Vault, or
  HashiCorp Vault), encrypted at rest, decrypted only at deployment time
  and injected directly into the target platform's own secret store
  (Databricks Secret Scopes, Snowflake's native secret objects) — not
  passed through the SaaS backend at runtime after initial setup.
- **OAuth over passwords wherever the source supports it.** Salesforce in
  particular should use a proper OAuth connected app, not raw
  username/password — this is a straightforward win the original spec's
  "assume credentials are given in the UI" glossed over, and it's worth
  building correctly from the start rather than retrofitting later.
- **Key-pair auth for Snowflake** in preference to static passwords, per
  Snowflake's own recommended practice for programmatic access.
- **Least-privilege service accounts** for both source read access and
  target write access — the UI's credential-entry step should nudge users
  toward a scoped service account rather than their personal admin login.

---

## 9. Billing integration: Polar.sh

Polar.sh is a Merchant-of-Record billing platform — it handles global tax
compliance, subscription lifecycle (renewals, dunning, cancellation), and
a hosted Customer Portal, which removes a substantial amount of billing
infrastructure the team would otherwise have to build.

**Pricing reality to design around:** Polar's own fee structure changed in
2026 — new organizations start on the free Starter plan at 5% + $0.50 per
transaction; paid Polar plans (Pro at $20/mo, Growth at $100/mo, Scale at
$400/mo) buy down that per-transaction rate. For v1, start on Polar's free
Starter plan — at low transaction volume the monthly fee on a paid Polar
tier won't pay for itself yet; revisit once the customer base is large
enough that the rate reduction outweighs the monthly fee.

**Implementation:**

- Three Polar products: **Free** (no checkout needed, just an app-side
  flag), **Team** ($10/month recurring subscription product in Polar),
  and **Pro** ($20/month recurring subscription product in Polar).
- Use Polar's **usage-based billing primitives** (events → meters) to
  track active-pipeline-count per organization, even though the actual
  plan is flat-rate rather than metered — this gives an accurate,
  queryable, always-current count via the Polar API/Customer Portal
  without the app maintaining its own duplicate ledger.
- **Polar does not enforce quotas** — it exposes the meter, and the
  product decides what to do at the limit. The app must check the current
  pipeline count against the plan's limit at the moment a user tries to
  create a new pipeline, and block with an upgrade prompt if at the cap.
- Upgrade/downgrade self-serve via Polar's hosted Customer Portal
  (`portal()` — no custom billing UI needed for plan changes).
- Webhooks from Polar (subscription created/canceled/payment failed) sync
  plan state back into the app's own database so pipeline-limit checks
  don't require a live API call on every action.

---

## 10. Technology recommendations

| Layer | Recommendation | Why |
|---|---|---|
| Backend | Python (FastAPI) | Same language as the PySpark/Snowpark codegen and connector libraries — one language across the stack |
| Frontend | React | Multi-step wizard UI (source config → target config → schema review → deploy) fits component-based UI well |
| Control-plane DB | PostgreSQL | Tenant/user/pipeline/session/billing metadata |
| Secrets | AWS Secrets Manager / Azure Key Vault / HashiCorp Vault | Never store raw credentials in Postgres |
| Codegen templating | Jinja2 | Standard, well-understood Python templating for the rule-based layer |
| Databricks deployment | Databricks Jobs API 2.1 + Workspace API | Native scheduling and notebook deployment, no external orchestrator needed |
| Snowflake deployment | Snowflake Tasks (native CRON scheduling) | Avoids standing up Airflow/Dagster for v1 |
| Billing | Polar.sh | Merchant of Record, native subscription + usage-metering primitives |

---

## 11. Phased rollout

Given the combined scope (multi-source connectors, dual-target codegen,
auto-deploy, scheduling, multi-tenant auth, billing), this should ship in
phases rather than as one release:

1. **Phase 1:** CSV/JSON → Databricks only, manual "generate code" review
   step, no auto-deploy yet. Proves the metadata-driven schema discovery
   and template-based codegen pattern end to end.
2. **Phase 2:** Add Snowflake as a target. Add auto-deploy + native
   scheduling (Jobs API / Tasks).
3. **Phase 3:** Add database sources (SQL Server, Oracle).
4. **Phase 4:** Add API sources (Salesforce, then Workday — Workday last,
   given its RaaS-based integration is the highest-friction connector).
5. **Phase 5:** Multi-tenant hardening, session history polish, and the
   full Polar.sh subscription/billing integration.
6. **Phase 6:** AI-assisted template authoring for new connector
   bootstrapping (Section 7, item 2) — this is deliberately last, since it
   depends on having a mature template library and validation pipeline to
   plug into first.

---

## 12. Open questions / assumptions to confirm

- **Pipeline limit confirmed as active-pipeline-count**, not run-count
  (Section 5.8) — confirmed with the user during PRD review.
- **Payment processor confirmed as Polar.sh** (Section 9) — confirmed with
  the user during PRD review.
- **Code generation confirmed as hybrid** (Section 7) — confirmed with the
  user during PRD review.
- **Pro is $20/month recurring** (not one-time) — confirmed with the user.
- **Team tier added** at $10/month for 10 pipelines — confirmed with the
  user, closes the gap between Free (3) and Pro (20).
- On-prem source connectivity (Section 5, non-goals) — confirm this is
  genuinely out of scope for v1, since it changes the networking
  architecture significantly if not.
