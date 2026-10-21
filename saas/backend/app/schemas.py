import uuid
from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Path = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


# --- auth / account -------------------------------------------------------------

class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    org_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    tier: str


class RegisterOut(BaseModel):
    user_id: uuid.UUID
    email: str
    org: OrgOut
    # Signing up logs you in: use this as `Authorization: Bearer <access_token>` straight away.
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class MembershipOut(BaseModel):
    org: OrgOut
    role: str


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    memberships: list[MembershipOut]


class UsageOut(BaseModel):
    org_id: uuid.UUID
    org_name: str
    tier: str
    pipeline_limit: int
    pipeline_count: int
    can_create_pipeline: bool
    member_limit: int
    member_count: int
    pending_invites: int  # invitations waiting to be accepted; each holds a seat


# --- team ---------------------------------------------------------------------------

Role = Literal["admin", "member"]


class InviteIn(BaseModel):
    email: EmailStr
    role: Role = "member"


class InviteOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    created_at: datetime
    expires_at: datetime


class InviteCreatedOut(InviteOut):
    # Shown exactly once, here. Only its hash is stored, so it can't be shown again —
    # to get a new one, invite the same address again.
    token: str


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    role: str
    joined_at: datetime


class AcceptInviteNewAccountIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=10, max_length=128)


class AcceptInviteIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


# --- source registry ------------------------------------------------------------

class ColumnIn(BaseModel):
    name: Name
    sql_type: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    spark_type: str | None = Field(default=None, max_length=100)
    pandas_dtype: str | None = Field(default=None, max_length=100)
    nullable: bool = True
    struct_path: list[str] | None = None  # COBOL group items: original nested path


class RegisterSourceIn(BaseModel):
    source_type: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
    source_path: Path
    schema_definition_path: Path | None = None
    discovery_config: dict = Field(default_factory=dict)
    columns: list[ColumnIn] = Field(min_length=1, max_length=5000)


class SourceOut(BaseModel):
    id: uuid.UUID
    source_type: str
    source_path: str
    schema_definition_path: str | None
    discovery_config: dict
    columns: list[dict]
    schema_hash: str
    version: int
    record_is_active: bool
    record_start_ts: datetime
    record_end_ts: datetime | None
    last_verified_at: datetime


class RegisterSourceOut(BaseModel):
    status: Literal["created", "unchanged", "drifted"]
    previous_version: int | None
    source: SourceOut


class DriftOut(BaseModel):
    pipeline_id: uuid.UUID
    job_name: str
    table_name: str
    source_type: str
    source_path: str
    schema_definition_path: str | None
    built_from_source_id: uuid.UUID
    built_from_version: int
    current_source_id: uuid.UUID
    current_version: int


# --- Databricks connection ------------------------------------------------------------

class ConnectionIn(BaseModel):
    host: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    token: str = Field(min_length=10, max_length=512)  # personal access token; write-only


class ConnectionOut(BaseModel):
    host: str
    updated_at: datetime  # the token itself is never returned


# --- discovery -----------------------------------------------------------------------

SourceType = Literal["csv", "json", "xml", "cobol"]


class DiscoverIn(BaseModel):
    source_type: SourceType
    mode: Literal["single", "multi"] = "single"
    source_path: Path  # a file (single) or a folder (multi)
    schema_definition_path: Path | None = None  # copybook (cobol) or XSD (xml)
    delimiter: str = ","
    has_header: bool = True
    multiline: bool = False
    row_tag: str = "record"
    is_record_sequence: bool = False
    is_text: bool = False
    cluster_id: str | None = None  # required for COBOL (needs the Cobrix library)


class DiscoveredSourceOut(BaseModel):
    suggested_table_name: str
    status: Literal["created", "unchanged", "drifted"]
    previous_version: int | None
    source: SourceOut


class DiscoverOut(BaseModel):
    sources: list[DiscoveredSourceOut]


# --- pipelines -----------------------------------------------------------------------

class ScheduleIn(BaseModel):
    frequency: Literal["hourly", "daily", "weekly"] = "daily"
    time: str = "02:00"  # HH:MM, 24-hour, in `timezone`
    day_of_week: str = "MON"  # weekly only
    timezone: str = "UTC"  # IANA name


class PipelineTableIn(BaseModel):
    source_id: uuid.UUID
    table_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class PipelineIn(BaseModel):
    job_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    mode: Literal["single", "multi"] = "single"
    catalog: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    target_schema: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    tables: list[PipelineTableIn] = Field(min_length=1, max_length=200)
    schedule: ScheduleIn = Field(default_factory=ScheduleIn)
    cluster_id: str | None = None  # omit for serverless (COBOL needs a cluster with Cobrix)


class PipelinePreviewOut(BaseModel):
    notebook: str
    notebook_sha256: str  # send this back when deploying: proves the notebook you reviewed is the one deployed
    workspace_path: str
    cron: str


class PipelineDeployIn(PipelineIn):
    notebook_sha256: str = Field(min_length=64, max_length=64)
    run_now: bool = False


class PipelineTableOut(BaseModel):
    table_name: str
    source_id: uuid.UUID
    source_type: str
    source_path: str
    source_version: int
    is_stale: bool  # a newer version of this source exists (drift) — review the pipeline


class PipelineOut(BaseModel):
    id: uuid.UUID
    layer: Literal["raw", "bronze", "silver", "gold"] = "raw"
    job_name: str
    mode: str
    catalog: str
    target_schema: str
    schedule_frequency: str | None
    schedule_time: str | None
    schedule_timezone: str
    workspace_path: str | None
    databricks_job_id: int | None
    databricks_job_url: str | None
    cluster_id: str | None
    created_at: datetime
    tables: list[PipelineTableOut]
    last_run: "RunOut | None" = None  # the most recent run we know of (refresh to update)


class PipelineDetailOut(PipelineOut):
    notebook_source: str | None
    job_parameters: dict
    notebook_sha256: str | None
    spec: dict = Field(default_factory=dict)  # Bronze / Silver: what it reads and what it writes


# --- bronze pipelines (distinct records from Raw tables) ------------------------------

class BronzeInputIn(BaseModel):
    asset_id: uuid.UUID  # a Raw table from GET /assets?layer=raw
    table_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]  # the Bronze table to write


class BronzePipelineIn(BaseModel):
    job_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    catalog: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    target_schema: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    inputs: list[BronzeInputIn] = Field(min_length=1, max_length=200)
    schedule: ScheduleIn = Field(default_factory=ScheduleIn)
    cluster_id: str | None = None


class BronzeDeployIn(BronzePipelineIn):
    notebook_sha256: str = Field(min_length=64, max_length=64)
    run_now: bool = False


# --- silver pipelines (integrated entities built from a Bronze table) --------------------

class _Step(BaseModel):
    model_config = ConfigDict(extra="forbid")  # an unknown field is a mistake, never silently ignored


class TrimStep(_Step):
    kind: Literal["trim"]


class UpperStep(_Step):
    kind: Literal["upper"]


class LowerStep(_Step):
    kind: Literal["lower"]


class NullIfStep(_Step):
    kind: Literal["null_if"]
    values: list[str] = Field(min_length=1, max_length=20)  # compared as trimmed text


class CastStep(_Step):
    kind: Literal["cast"]
    type: str = Field(max_length=40)


class ParseDateStep(_Step):
    kind: Literal["parse_date"]
    format: str = Field(max_length=40)


class ParseTimestampStep(_Step):
    kind: Literal["parse_timestamp"]
    format: str = Field(max_length=40)


class DefaultStep(_Step):
    kind: Literal["default"]
    value: str = Field(max_length=64)


SilverStep = Annotated[
    Union[TrimStep, UpperStep, LowerStep, NullIfStep, CastStep, ParseDateStep, ParseTimestampStep, DefaultStep],
    Field(discriminator="kind"),
]


class SilverAttributeIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    source_column: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    steps: list[SilverStep] = Field(default_factory=list, max_length=8)
    required: bool = False  # a record with no value here is quarantined


class SilverMasterKeyIn(BaseModel):
    column: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]  # the master key column to add
    crosswalk_asset_id: uuid.UUID  # a reference table from POST /assets/reference
    system_column: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    key_column: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    master_column: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    # For a crosswalk that holds several kinds of thing (customer, product, ...): which column says
    # which, and the value that means this table's kind. Give both or neither.
    entity_type_column: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)] | None = None
    entity_type: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] | None = None


class SilverEntityIn(BaseModel):
    table_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    business_key: list[str] = Field(min_length=1, max_length=5)  # attribute names
    attributes: list[SilverAttributeIn] = Field(min_length=1, max_length=200)
    master_key: SilverMasterKeyIn | None = None


class SilverPipelineIn(BaseModel):
    job_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    catalog: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    target_schema: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    source_asset_id: uuid.UUID  # a Bronze table from GET /assets?layer=bronze
    source_system: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]  # matched against the crosswalk
    unknown_master_key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] = "UNKNOWN"
    entities: list[SilverEntityIn] = Field(min_length=1, max_length=20)
    schedule: ScheduleIn = Field(default_factory=ScheduleIn)
    cluster_id: str | None = None


class SilverDeployIn(SilverPipelineIn):
    notebook_sha256: str = Field(min_length=64, max_length=64)
    run_now: bool = False


# --- runs ----------------------------------------------------------------------------

RunStatus = Literal["pending", "running", "success", "failed", "cancelled"]


class RunOut(BaseModel):
    id: uuid.UUID
    pipeline_id: uuid.UUID
    databricks_run_id: int | None
    databricks_run_url: str | None
    triggered_by: Literal["schedule", "manual", "deploy"]
    status: RunStatus
    error_message: str | None
    rows_by_table: dict | None  # superseded by `metrics`; kept for older rows
    metrics: dict | None  # counts the run's notebook reported (see services/run_metrics.py); None until fetched
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class OrgRunOut(RunOut):
    job_name: str


# --- table registry -----------------------------------------------------------------

AssetLayer = Literal["raw", "bronze", "silver", "gold", "reference"]


class AssetRef(BaseModel):
    id: uuid.UUID
    full_name: str
    layer: AssetLayer


class AssetProducer(BaseModel):
    pipeline_id: uuid.UUID
    job_name: str


class AssetOut(BaseModel):
    id: uuid.UUID
    catalog: str
    schema_name: str
    table_name: str
    full_name: str  # catalog.schema.table
    layer: AssetLayer
    source_system: str | None
    origin: str | None
    produced_by: AssetProducer | None  # None once its pipeline was deleted (the table itself remains)
    column_count: int
    row_count: int | None
    last_run_at: datetime | None
    created_at: datetime
    upstream_count: int
    downstream_count: int


class AssetColumn(BaseModel):
    name: str
    sql_type: str


class AssetDetailOut(AssetOut):
    columns: list[AssetColumn]
    upstream: list[AssetRef]
    downstream: list[AssetRef]


class ReferenceTableIn(BaseModel):
    catalog: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    schema_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    table_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


PipelineOut.model_rebuild()
PipelineDetailOut.model_rebuild()
