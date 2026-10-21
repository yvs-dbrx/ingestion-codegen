// Mirrors saas/backend/app/schemas.py — keep in sync by hand for now.

export interface OrgOut {
  id: string
  name: string
  tier: string
}

export interface MembershipOut {
  org: OrgOut
  role: 'admin' | 'member'
}

export interface MeOut {
  user_id: string
  email: string
  memberships: MembershipOut[]
}

export interface TokenOut {
  access_token: string
  token_type: 'bearer'
}

export interface RegisterOut {
  user_id: string
  email: string
  org: OrgOut
  access_token: string
  token_type: 'bearer'
}

// --- source registry -------------------------------------------------------------

export type SourceType = 'csv' | 'json' | 'xml' | 'cobol'
export type SourceStatus = 'created' | 'unchanged' | 'drifted'

export interface ColumnOut {
  name: string
  sql_type: string
  spark_type?: string | null
  pandas_dtype?: string | null
  nullable: boolean
  struct_path?: string[] | null
}

export interface SourceOut {
  id: string
  source_type: string
  source_path: string
  schema_definition_path: string | null
  discovery_config: Record<string, unknown>
  columns: ColumnOut[]
  schema_hash: string
  version: number
  record_is_active: boolean
  record_start_ts: string
  record_end_ts: string | null
  last_verified_at: string
}

export interface DriftOut {
  pipeline_id: string
  job_name: string
  table_name: string
  source_type: string
  source_path: string
  schema_definition_path: string | null
  built_from_source_id: string
  built_from_version: number
  current_source_id: string
  current_version: number
}

// --- discovery ---------------------------------------------------------------------

export interface DiscoverIn {
  source_type: SourceType
  mode: 'single' | 'multi'
  source_path: string
  schema_definition_path?: string | null
  delimiter?: string
  has_header?: boolean
  multiline?: boolean
  row_tag?: string
  is_record_sequence?: boolean
  is_text?: boolean
  cluster_id?: string | null
}

export interface DiscoveredSourceOut {
  suggested_table_name: string
  status: SourceStatus
  previous_version: number | null
  source: SourceOut
}

export interface DiscoverOut {
  sources: DiscoveredSourceOut[]
}

// --- Databricks connection -----------------------------------------------------------

export interface ConnectionOut {
  host: string
  updated_at: string
}

export interface ConnectionIn {
  host: string
  token: string
}

export interface UsageOut {
  org_id: string
  org_name: string
  tier: string
  pipeline_limit: number
  pipeline_count: number
  can_create_pipeline: boolean
  member_limit: number
  member_count: number
  pending_invites: number
}

// --- pipelines -------------------------------------------------------------------------

export type RunStatus = 'pending' | 'running' | 'success' | 'failed' | 'cancelled'

/** What a run's notebook reported about itself (see backend services/run_metrics.py). */
export interface TableMetrics {
  source?: string
  target?: string
  rows_read?: number
  rows_written?: number
  duplicates_removed?: number
  distinct_records?: number
  rows_inserted?: number
  already_in_target?: number
  rows_in_target?: number
  sightings_inserted?: number
  sightings_in_target?: number
  // Silver
  null_key_records?: number
  distinct_keys?: number
  conflicting_keys?: number
  quarantined_keys?: number
  rows_updated?: number
  unchanged?: number
  unmapped_keys?: number
  crosswalk_rows?: number
  crosswalk_version?: number
  extra_source_columns?: string[]
  reconciled?: boolean
}

export interface RunMetrics {
  layer?: 'raw' | 'bronze' | 'silver'
  mode?: string
  reconciled?: boolean
  tables?: Record<string, TableMetrics>
  /** The run finished but its notebook reported nothing (generated before counts existed). */
  unavailable?: boolean
}

export interface RunOut {
  id: string
  pipeline_id: string
  databricks_run_id: number | null
  databricks_run_url: string | null
  triggered_by: 'schedule' | 'manual' | 'deploy'
  status: RunStatus
  error_message: string | null
  rows_by_table: Record<string, number> | null
  metrics: RunMetrics | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export interface ScheduleIn {
  frequency: 'hourly' | 'daily' | 'weekly'
  time: string // HH:MM, 24-hour, in `timezone`
  day_of_week: string // MON..SUN, weekly only
  timezone: string // IANA name
}

export interface PipelineIn {
  job_name: string
  mode: 'single' | 'multi'
  catalog: string
  target_schema: string
  tables: { source_id: string; table_name: string }[]
  schedule: ScheduleIn
  cluster_id?: string | null
}

export interface PipelinePreviewOut {
  notebook: string
  notebook_sha256: string
  workspace_path: string
  cron: string
}

export interface PipelineDeployIn extends PipelineIn {
  notebook_sha256: string
  run_now: boolean
}

export interface PipelineTableOut {
  table_name: string
  source_id: string
  source_type: string
  source_path: string
  source_version: number
  is_stale: boolean
}

export interface PipelineOut {
  id: string
  layer: 'raw' | 'bronze' | 'silver' | 'gold'
  job_name: string
  mode: string
  catalog: string
  target_schema: string
  schedule_frequency: string | null
  schedule_time: string | null
  schedule_timezone: string
  workspace_path: string | null
  databricks_job_id: number | null
  databricks_job_url: string | null
  cluster_id: string | null
  created_at: string
  tables: PipelineTableOut[]
  last_run: RunOut | null
}

export interface PipelineDetailOut extends PipelineOut {
  notebook_source: string | null
  job_parameters: Record<string, unknown>
  notebook_sha256: string | null
  spec: {
    inputs?: { asset_id: string; raw: string; bronze: string; table_name: string }[]
    source?: { asset_id: string; bronze: string; system: string }
    unknown_master_key?: string
    entities?: SilverEntitySpec[]
  }
}

/** A Silver entity as stored with its pipeline (validated and normalized by the server). */
export interface SilverEntitySpec {
  name: string
  target: string
  quarantine: string
  business_key: string[]
  attributes: { name: string; from: string; type: string; required: boolean; steps: SilverStep[] }[]
  master_key: {
    column: string
    crosswalk: string
    system_column: string
    key_column: string
    master_column: string
    entity_type_column?: string
    entity_type?: string
  } | null
}

// --- team -----------------------------------------------------------------------------

export type Role = 'admin' | 'member'

export interface MemberOut {
  user_id: string
  email: string
  role: Role
  joined_at: string
}

export interface InviteOut {
  id: string
  email: string
  role: Role
  created_at: string
  expires_at: string
}

export interface InviteCreatedOut extends InviteOut {
  token: string // shown exactly once, only in this response
}

export interface OrgRunOut extends RunOut {
  job_name: string
}

// --- table registry -------------------------------------------------------------------

export type AssetLayer = 'raw' | 'bronze' | 'silver' | 'gold' | 'reference'

export interface AssetRef {
  id: string
  full_name: string
  layer: AssetLayer
}

export interface AssetOut {
  id: string
  catalog: string
  schema_name: string
  table_name: string
  full_name: string
  layer: AssetLayer
  source_system: string | null
  origin: string | null
  produced_by: { pipeline_id: string; job_name: string } | null
  column_count: number
  row_count: number | null
  last_run_at: string | null
  created_at: string
  upstream_count: number
  downstream_count: number
}

export interface AssetColumn {
  name: string
  sql_type: string
}

export interface AssetDetailOut extends AssetOut {
  columns: AssetColumn[]
  upstream: AssetRef[]
  downstream: AssetRef[]
}

// --- bronze pipelines -------------------------------------------------------------------

export interface BronzePipelineIn {
  job_name: string
  catalog: string
  target_schema: string
  inputs: { asset_id: string; table_name: string }[]
  schedule: ScheduleIn
  cluster_id?: string | null
}

export interface BronzeDeployIn extends BronzePipelineIn {
  notebook_sha256: string
  run_now: boolean
}

// --- reference tables and silver pipelines ----------------------------------------------

export interface ReferenceTableIn {
  catalog: string
  schema_name: string
  table_name: string
}

export type SilverStep =
  | { kind: 'trim' }
  | { kind: 'upper' }
  | { kind: 'lower' }
  | { kind: 'null_if'; values: string[] }
  | { kind: 'cast'; type: string }
  | { kind: 'parse_date'; format: string }
  | { kind: 'parse_timestamp'; format: string }
  | { kind: 'default'; value: string }

export interface SilverAttributeIn {
  name: string
  source_column: string
  steps: SilverStep[]
  required: boolean
}

export interface SilverMasterKeyIn {
  column: string
  crosswalk_asset_id: string
  system_column: string
  key_column: string
  master_column: string
  entity_type_column?: string | null
  entity_type?: string | null
}

export interface SilverEntityIn {
  table_name: string
  business_key: string[]
  attributes: SilverAttributeIn[]
  master_key: SilverMasterKeyIn | null
}

export interface SilverPipelineIn {
  job_name: string
  catalog: string
  target_schema: string
  source_asset_id: string
  source_system: string
  unknown_master_key: string
  entities: SilverEntityIn[]
  schedule: ScheduleIn
  cluster_id?: string | null
}

export interface SilverDeployIn extends SilverPipelineIn {
  notebook_sha256: string
  run_now: boolean
}

/** The steps a transform may use, in the words the form shows. Mirrors the server's closed list. */
export const DATE_FORMATS = ['yyyyMMdd', 'yyyy-MM-dd', 'yyyy/MM/dd', 'MM/dd/yyyy', 'dd/MM/yyyy', 'dd-MM-yyyy', 'MMddyyyy', 'ddMMyyyy']
export const TIMESTAMP_FORMATS = [
  'yyyy-MM-dd HH:mm:ss',
  'yyyy-MM-dd HH:mm:ss.SSS',
  "yyyy-MM-dd'T'HH:mm:ss",
  'yyyyMMddHHmmss',
  'MM/dd/yyyy HH:mm:ss',
  'dd/MM/yyyy HH:mm:ss',
]
export const CAST_TYPES = ['STRING', 'INT', 'BIGINT', 'DOUBLE', 'BOOLEAN', 'DATE', 'TIMESTAMP', 'DECIMAL(18,2)']
