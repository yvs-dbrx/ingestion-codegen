"""ORM mappings for the tables defined in ../db/init/001_schema.sql.

The SQL file is the source of truth for the schema; these classes only map the
columns the app reads or writes (columns with database defaults can be omitted).
"""
import uuid
from datetime import datetime, time

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Text, Time, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


_uuid_pk = dict(primary_key=True, server_default=text("gen_random_uuid()"))


class Tier(Base):
    __tablename__ = "tiers"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    pipeline_limit: Mapped[int] = mapped_column(Integer)
    member_limit: Mapped[int] = mapped_column(Integer, server_default="5")


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_uuid_pk)
    name: Mapped[str] = mapped_column(Text)
    tier: Mapped[str] = mapped_column(Text, server_default="free")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_uuid_pk)
    email: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrgMember(Base):
    __tablename__ = "org_members"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(Text, server_default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrgInvite(Base):
    __tablename__ = "org_invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_uuid_pk)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"))
    email: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, server_default="member")
    token_hash: Mapped[str] = mapped_column(Text)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceRegistry(Base):
    __tablename__ = "source_registry"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_uuid_pk)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"))
    source_type: Mapped[str] = mapped_column(Text)
    source_path: Mapped[str] = mapped_column(Text)
    schema_definition_path: Mapped[str | None] = mapped_column(Text)
    discovery_config: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    schema_json: Mapped[list] = mapped_column(JSONB)
    schema_hash: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    record_start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    record_end_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    record_is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    last_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    discovered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class OrgConnection(Base):
    __tablename__ = "org_connections"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"), primary_key=True)
    host: Mapped[str] = mapped_column(Text)
    token_ciphertext: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_uuid_pk)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"))
    job_name: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text, server_default="single")
    catalog: Mapped[str] = mapped_column(Text)
    target_schema: Mapped[str] = mapped_column(Text)
    schedule_frequency: Mapped[str | None] = mapped_column(Text)
    schedule_time: Mapped[time | None] = mapped_column(Time)
    schedule_timezone: Mapped[str] = mapped_column(Text, server_default="UTC")
    workspace_path: Mapped[str | None] = mapped_column(Text)
    databricks_job_id: Mapped[int | None] = mapped_column(BigInteger)
    databricks_job_url: Mapped[str | None] = mapped_column(Text)
    notebook_source: Mapped[str | None] = mapped_column(Text)
    job_parameters: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    cluster_id: Mapped[str | None] = mapped_column(Text)
    notebook_sha256: Mapped[str | None] = mapped_column(Text)
    layer: Mapped[str] = mapped_column(Text, server_default="raw")
    spec: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_uuid_pk)
    pipeline_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipelines.id"))
    databricks_run_id: Mapped[int | None] = mapped_column(BigInteger)
    databricks_run_url: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str] = mapped_column(Text, server_default="schedule")
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    rows_by_table: Mapped[dict | None] = mapped_column(JSONB)
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineSource(Base):
    __tablename__ = "pipeline_sources"

    pipeline_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipelines.id"), primary_key=True)
    source_registry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_registry.id"), primary_key=True)
    table_name: Mapped[str] = mapped_column(Text)


class DataAsset(Base):
    __tablename__ = "data_assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_uuid_pk)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"))
    catalog: Mapped[str] = mapped_column(Text)
    schema_name: Mapped[str] = mapped_column(Text)
    table_name: Mapped[str] = mapped_column(Text)
    layer: Mapped[str] = mapped_column(Text)
    source_system: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str | None] = mapped_column(Text)
    produced_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pipelines.id"))
    columns: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))  # [{name, sql_type}], source columns only
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssetLineage(Base):
    __tablename__ = "asset_lineage"

    downstream_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_assets.id"), primary_key=True)
    upstream_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_assets.id"), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"))
