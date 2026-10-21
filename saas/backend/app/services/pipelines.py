"""Turning registered sources into a deployed Databricks job — and removing it again.

The order of operations is what makes this safe:

  build_plan   every value that will be pasted into generated code is validated first
               (codegen_safety), including values read back from the database — the
               registry accepts client-supplied discovery_config/columns, so they are
               untrusted here too. Then the notebook is rendered.
  preview      returns that notebook + its sha256. Nothing is written anywhere.
  deploy       the caller sends the sha256 back; it must match the notebook we render now,
               so what gets deployed is exactly what a person reviewed. Under the org lock
               it checks the plan limit, reserves the name in the database, checks
               Databricks doesn't already have that job/notebook (the engine would silently
               overwrite both), deploys, and commits. Any failure rolls the database back and
               removes what was uploaded, so a failed deploy leaves nothing behind and
               doesn't burn a plan slot.
"""
import re
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Org, Pipeline, PipelineRun, PipelineSource, SourceRegistry, Tier
from app.schemas import PipelineDeployIn, PipelineIn, PipelineTableOut
from app.services import assets as assets_svc
from app.services import codegen_safety as safety
from app.services import engine
from app.services.databricks_client import ClientBuilder, client_for_org
from app.services.errors import BadGateway, Conflict, NotFound, ServiceError, Unprocessable
from app.services.tenancy import lock_org

SOURCE_TYPES = {"csv", "json", "xml", "cobol"}
# Settings that must agree between every table of one multi-file pipeline (one notebook, one reader config).
SETTING_DEFAULTS = {
    "delimiter": ",",
    "has_header": True,
    "multiline": False,
    "row_tag": "record",
    "is_record_sequence": False,
    "is_text": False,
}
BOOL_SETTINGS = ("has_header", "multiline", "is_record_sequence", "is_text")


class PlanLimitReached(Conflict):
    pass


@dataclass
class Plan:
    source_type: str
    mode: str
    links: list[tuple[SourceRegistry, str]]  # (source version, target table name)
    notebook: str
    notebook_sha256: str
    workspace_path: str
    cron: str
    source_path_param: str
    extra_parameters: dict
    schedule_time: time
    problems: list[str] = field(default_factory=list)
    # What distinguishes one kind of pipeline from another; Raw (files -> tables) is the default.
    layer: str = "raw"
    spec: dict = field(default_factory=dict)
    # Runs inside the deploy transaction, after the pipeline row exists: links it to what it reads and
    # registers the tables it writes. Anything it raises rolls the whole deploy back.
    register: Callable[[Session, Pipeline], None] | None = None


def workspace_path_for(org_id: uuid.UUID, job_name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.\-]+", "_", job_name).strip("._-") or "pipeline"
    return f"{safety.WORKSPACE_ROOT}{str(org_id)[:8]}/{slug}"


def _load_sources(db: Session, org_id: uuid.UUID, ids: list[uuid.UUID], lock: bool) -> dict[uuid.UUID, SourceRegistry]:
    stmt = select(SourceRegistry).where(SourceRegistry.id.in_(ids), SourceRegistry.org_id == org_id)  # tenant filter
    if lock:
        stmt = stmt.order_by(SourceRegistry.id).with_for_update()  # stable order; blocks a drift landing mid-deploy
    found = {r.id: r for r in db.scalars(stmt)}
    for source_id in ids:
        if source_id not in found:
            raise NotFound(f"Source {source_id} not found")
    return found


def build_plan(db: Session, org_id: uuid.UUID, body: PipelineIn, *, lock_sources: bool = False) -> Plan:
    problems: list[str] = []

    ids = [t.source_id for t in body.tables]
    names = [t.table_name.lower() for t in body.tables]
    if len(set(ids)) != len(ids):
        problems.append("the same source can't appear twice in one pipeline")
    if len(set(names)) != len(names):
        problems.append("table names must be unique within a pipeline (case-insensitive)")
    if body.mode == "single" and len(body.tables) != 1:
        problems.append("a single-file pipeline takes exactly one table")
    if problems:
        raise Unprocessable("This pipeline can't be built", problems=problems)

    sources = _load_sources(db, org_id, ids, lock_sources)
    rows = [sources[i] for i in ids]

    for row in rows:
        if not row.record_is_active:
            raise Conflict(
                f"Source {row.source_path} (version {row.version}) has been superseded by a newer version — "
                "use the current one (GET /sources) and review the changes first"
            )

    source_type = rows[0].source_type
    if any(r.source_type != source_type for r in rows):
        problems.append("all tables in a pipeline must be the same source type")
    if source_type not in SOURCE_TYPES:
        problems.append(f"source type {source_type!r} can't be deployed")
    if body.mode == "multi" and source_type not in ("csv", "json"):
        problems.append(f"multi-file pipelines aren't supported for {source_type} sources")
    if problems:
        raise Unprocessable("This pipeline can't be built", problems=problems)

    # Reader settings come from what was recorded at discovery; validate them like any other input.
    settings = {**SETTING_DEFAULTS, **{k: v for k, v in (rows[0].discovery_config or {}).items() if k in SETTING_DEFAULTS}}
    for key in BOOL_SETTINGS:
        if not isinstance(settings[key], bool):
            problems.append(f"discovery setting {key} must be true or false")
    if not isinstance(settings["delimiter"], str) or not isinstance(settings["row_tag"], str):
        problems.append("discovery settings delimiter and row_tag must be text")
    if problems:
        raise Unprocessable("This pipeline can't be built", problems=problems)
    for row in rows[1:]:
        other = {**SETTING_DEFAULTS, **{k: v for k, v in (row.discovery_config or {}).items() if k in SETTING_DEFAULTS}}
        if other != settings:
            problems.append(
                f"{row.source_path} was discovered with different settings (delimiter/header/multiline/…) "
                "than the other tables — re-discover them together"
            )

    safety.collect(problems, safety.check_delimiter, settings["delimiter"])
    if source_type == "xml":
        safety.collect(problems, safety.check_row_tag, settings["row_tag"])
    safety.collect(problems, safety.check_identifier, body.catalog, "catalog")
    safety.collect(problems, safety.check_identifier, body.target_schema, "target schema")
    safety.collect(problems, safety.check_job_name, body.job_name)
    safety.collect(problems, safety.check_schedule, body.schedule.frequency, body.schedule.time, body.schedule.day_of_week, body.schedule.timezone)
    if body.cluster_id:
        safety.collect(problems, safety.check_cluster_id, body.cluster_id)
    if source_type == "cobol" and not body.cluster_id:
        problems.append("COBOL pipelines need cluster_id: a cluster with the Cobrix library attached (serverless can't load it)")

    gen_tables: list[engine.GenTable] = []
    for row, requested in zip(rows, body.tables):
        safety.collect(problems, safety.check_identifier, requested.table_name, "table name")
        safety.collect(problems, safety.check_path, row.source_path, "source path")
        file_name = row.source_path.rsplit("/", 1)[-1]
        if body.mode == "multi":
            safety.collect(problems, safety.check_file_name, file_name)
        if source_type == "cobol":
            if not row.schema_definition_path:
                problems.append("this COBOL source has no copybook path — re-discover it")
            else:
                safety.collect(problems, safety.check_path, row.schema_definition_path, "copybook path")
        columns = safety.collect(problems, safety.check_columns, list(row.schema_json), source_type=source_type)
        gen_tables.append(engine.GenTable(requested.table_name, file_name, row.source_path, columns or []))

    folder = rows[0].source_path.rsplit("/", 1)[0] if body.mode == "multi" else None
    if body.mode == "multi" and any(r.source_path.rsplit("/", 1)[0] != folder for r in rows):
        problems.append("all files in a multi-file pipeline must come from the same folder")

    workspace_path = workspace_path_for(org_id, body.job_name)
    safety.collect(problems, safety.check_workspace_path, workspace_path)
    if problems:
        raise Unprocessable("This pipeline can't be built", problems=problems)

    try:
        hour, minute = (int(p) for p in body.schedule.time.split(":"))
        cron = engine.build_cron(body.schedule.frequency, body.schedule.time, body.schedule.day_of_week)
        render_settings = {
            **settings,
            "folder_path": folder,
            "copybook_path": rows[0].schema_definition_path,
        }
        with tempfile.TemporaryDirectory() as tmp:
            _, notebook = engine.generate_notebook(
                source_type=source_type,
                mode=body.mode,
                tables=gen_tables,
                catalog=body.catalog,
                target_schema=body.target_schema,
                settings=render_settings,
                out_dir=Path(tmp),
            )
    except engine.EngineInputError as exc:
        raise Unprocessable(f"The notebook couldn't be generated: {exc}")
    except engine.EngineRemoteError as exc:
        raise Unprocessable(f"The notebook couldn't be generated: {exc}")

    links = list(zip(rows, [t.table_name for t in body.tables]))

    def register(db: Session, pipeline: Pipeline) -> None:
        for source, table_name in links:
            db.add(PipelineSource(pipeline_id=pipeline.id, source_registry_id=source.id, table_name=table_name))
        db.flush()
        assets_svc.register_raw_assets(db, pipeline=pipeline, links=links, tables=gen_tables)

    return Plan(
        source_type=source_type,
        mode=body.mode,
        links=links,
        notebook=notebook,
        notebook_sha256=engine.notebook_sha256(notebook),
        workspace_path=workspace_path,
        cron=cron,
        source_path_param=folder if body.mode == "multi" else rows[0].source_path,
        extra_parameters={"copybook_path": rows[0].schema_definition_path} if source_type == "cobol" else {},
        schedule_time=time(hour, minute),
        register=register,
    )


# --- deploy ------------------------------------------------------------------------------

def _plan_limit(db: Session, org_id: uuid.UUID) -> tuple[int, int, str]:
    limit, tier = db.execute(
        select(Tier.pipeline_limit, Tier.name).join(Org, Org.tier == Tier.name).where(Org.id == org_id)
    ).one()
    used = db.scalar(select(func.count()).select_from(Pipeline).where(Pipeline.org_id == org_id)) or 0
    return used, limit, tier


def deploy_pipeline(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    body: PipelineDeployIn,
    client_builder: ClientBuilder,
) -> Pipeline:
    """Deploys a Raw ingestion pipeline (files -> tables)."""
    return deploy_plan(
        db,
        org_id=org_id,
        user_id=user_id,
        body=body,
        client_builder=client_builder,
        build=lambda lock: build_plan(db, org_id, body, lock_sources=lock),
    )


def deploy_plan(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    body,
    client_builder: ClientBuilder,
    build: Callable[[bool], Plan],
) -> Pipeline:
    """The one deploy flow every kind of pipeline goes through. `body` supplies job_name, catalog,
    target_schema, schedule, cluster_id, notebook_sha256 and run_now; `build(lock)` produces the Plan.

    Commits (or rolls back) itself: after Databricks has been changed, a failed commit must
    trigger cleanup there, so this function has to own the whole transaction."""
    client = client_for_org(db, org_id, client_builder)

    lock_org(db, org_id)  # serializes plan-limit checks (and member changes) for this org
    plan = build(True)
    if plan.notebook_sha256 != body.notebook_sha256:
        raise Conflict(
            "The notebook no longer matches the one you previewed (a source changed, or the request differs) — "
            "preview again and review it before deploying"
        )

    used, limit, tier = _plan_limit(db, org_id)
    if used >= limit:
        raise PlanLimitReached(f"The {tier} plan allows {limit} pipelines and this organization has {used} — delete one or upgrade")

    pipeline = Pipeline(
        org_id=org_id,
        job_name=body.job_name,
        mode=plan.mode,
        layer=plan.layer,
        spec=plan.spec,
        catalog=body.catalog,
        target_schema=body.target_schema,
        schedule_frequency=body.schedule.frequency,
        schedule_time=plan.schedule_time,
        schedule_timezone=body.schedule.timezone,
        workspace_path=plan.workspace_path,
        notebook_source=plan.notebook,
        notebook_sha256=plan.notebook_sha256,
        job_parameters={**({"source_path": plan.source_path_param} if plan.source_path_param else {}), **plan.extra_parameters},
        cluster_id=body.cluster_id,
        created_by=user_id,
    )
    db.add(pipeline)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict(f"A pipeline named {body.job_name!r} already exists in this organization")
    # Same transaction as the pipeline: every failure path below rolls the registry entries back too.
    if plan.register is not None:
        try:
            plan.register(db, pipeline)
        except ServiceError:
            db.rollback()
            raise

    try:
        if engine.job_name_taken(client, body.job_name):
            db.rollback()
            raise Conflict(f"Your Databricks workspace already has a job named {body.job_name!r} — choose another name")
        if engine.workspace_path_taken(client, plan.workspace_path):
            db.rollback()
            raise Conflict(f"Something already exists at {plan.workspace_path} in your Databricks workspace — choose another job name")
    except engine.EngineRemoteError as exc:
        db.rollback()
        raise BadGateway(f"Couldn't check your Databricks workspace: {exc}")

    with tempfile.TemporaryDirectory() as tmp:
        notebook_file = Path(tmp) / "notebook.py"
        notebook_file.write_bytes(plan.notebook.encode("utf-8"))  # exactly the reviewed text
        try:
            result = engine.deploy(
                client,
                local_notebook_path=str(notebook_file),
                workspace_path=plan.workspace_path,
                job_name=body.job_name,
                source_path_param=plan.source_path_param,
                cron_expression=plan.cron,
                timezone=body.schedule.timezone,
                existing_cluster_id=body.cluster_id,
                run_now=body.run_now,
                extra_parameters=plan.extra_parameters or None,
            )
        except (engine.EngineInputError, engine.EngineRemoteError) as exc:
            db.rollback()
            engine.delete_notebook(client, plan.workspace_path)  # the path was free before we started
            raise BadGateway(f"Databricks rejected the deployment: {exc}")

    try:
        pipeline.databricks_job_id = result["job_id"]
        pipeline.databricks_job_url = result.get("job_url")
        if result.get("run_id") is not None:
            db.add(PipelineRun(pipeline_id=pipeline.id, databricks_run_id=result["run_id"], triggered_by="deploy", status="pending"))
        db.commit()
    except Exception:
        db.rollback()
        try:
            engine.delete_job(client, result["job_id"])
        except engine.EngineRemoteError:
            pass
        engine.delete_notebook(client, plan.workspace_path)
        raise
    db.refresh(pipeline)
    return pipeline


# --- delete ------------------------------------------------------------------------------

def ensure_same_workspace(client, pipeline: Pipeline) -> None:
    """A job id from one workspace must never be used against another: it could act on an
    unrelated job there. Refuses if the org is now connected somewhere else than where this
    pipeline was deployed."""
    connection_host = urlsplit(client.config.host).netloc.lower()
    deployed_host = urlsplit(pipeline.databricks_job_url or "").netloc.lower()
    if deployed_host and connection_host != deployed_host:
        raise Conflict(
            f"This pipeline was deployed to {deployed_host}, but your organization is now connected to "
            f"{connection_host} — reconnect the original workspace first"
        )


def delete_pipeline(
    db: Session, *, org_id: uuid.UUID, pipeline_id: uuid.UUID, client_builder: ClientBuilder
) -> None:
    pipeline = db.scalar(
        select(Pipeline)
        .where(Pipeline.id == pipeline_id, Pipeline.org_id == org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if pipeline is None:
        raise NotFound("Pipeline not found")

    if pipeline.databricks_job_id is not None:
        client = client_for_org(db, org_id, client_builder)
        ensure_same_workspace(client, pipeline)
        try:
            engine.delete_job(client, pipeline.databricks_job_id)
        except engine.EngineRemoteError as exc:
            raise BadGateway(f"Couldn't delete the Databricks job: {exc}")
        if pipeline.workspace_path:
            engine.delete_notebook(client, pipeline.workspace_path)

    db.delete(pipeline)  # pipeline_sources and pipeline_runs go with it (ON DELETE CASCADE)
    db.commit()


# --- reading -----------------------------------------------------------------------------

def table_links(db: Session, org_id: uuid.UUID, pipeline_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[PipelineTableOut]]:
    out: dict[uuid.UUID, list[PipelineTableOut]] = {pid: [] for pid in pipeline_ids}
    if not pipeline_ids:
        return out
    rows = db.execute(
        select(PipelineSource.pipeline_id, PipelineSource.table_name, SourceRegistry)
        .join(SourceRegistry, SourceRegistry.id == PipelineSource.source_registry_id)
        .where(PipelineSource.pipeline_id.in_(pipeline_ids), SourceRegistry.org_id == org_id)
        .order_by(PipelineSource.table_name)
    ).all()
    for pipeline_id, table_name, source in rows:
        out[pipeline_id].append(
            PipelineTableOut(
                table_name=table_name,
                source_id=source.id,
                source_type=source.source_type,
                source_path=source.source_path,
                source_version=source.version,
                is_stale=not source.record_is_active,
            )
        )
    return out
