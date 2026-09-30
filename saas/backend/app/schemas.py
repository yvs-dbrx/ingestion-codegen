import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, StringConstraints

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
    rows_by_table: dict | None  # not filled yet: the generated notebooks don't report row counts
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class OrgRunOut(RunOut):
    job_name: str


PipelineOut.model_rebuild()
PipelineDetailOut.model_rebuild()
