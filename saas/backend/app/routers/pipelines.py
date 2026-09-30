import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import OrgContext, get_org_context
from app.models import Pipeline
from app.schemas import PipelineDeployIn, PipelineDetailOut, PipelineIn, PipelineOut, PipelinePreviewOut
from app.routers.runs import run_out
from app.services import pipelines as svc
from app.services import runs as runs_svc
from app.services.databricks_client import ClientBuilder, get_client_builder
from app.services.errors import NotFound, ServiceError

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


class NotAllowed(ServiceError):
    status_code = 403


def _out(pipeline: Pipeline, tables: list, last_run=None) -> dict:
    return {
        "id": pipeline.id,
        "job_name": pipeline.job_name,
        "mode": pipeline.mode,
        "catalog": pipeline.catalog,
        "target_schema": pipeline.target_schema,
        "schedule_frequency": pipeline.schedule_frequency,
        "schedule_time": pipeline.schedule_time.strftime("%H:%M") if pipeline.schedule_time else None,
        "schedule_timezone": pipeline.schedule_timezone,
        "workspace_path": pipeline.workspace_path,
        "databricks_job_id": pipeline.databricks_job_id,
        "databricks_job_url": pipeline.databricks_job_url,
        "cluster_id": pipeline.cluster_id,
        "created_at": pipeline.created_at,
        "tables": tables,
        "last_run": run_out(last_run) if last_run else None,
    }


def _owned(db: Session, org_id: uuid.UUID, pipeline_id: uuid.UUID) -> Pipeline:
    row = db.scalar(select(Pipeline).where(Pipeline.id == pipeline_id, Pipeline.org_id == org_id))
    if row is None:
        raise NotFound("Pipeline not found")
    return row


@router.post("/preview", response_model=PipelinePreviewOut)
def preview(body: PipelineIn, ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)):
    """Generates the notebook for review. Changes nothing, in the database or in Databricks.
    Send the returned notebook_sha256 with the deploy request."""
    plan = svc.build_plan(db, ctx.org.id, body)
    return PipelinePreviewOut(
        notebook=plan.notebook, notebook_sha256=plan.notebook_sha256, workspace_path=plan.workspace_path, cron=plan.cron
    )


@router.post("", response_model=PipelineDetailOut, status_code=status.HTTP_201_CREATED)
def deploy(
    body: PipelineDeployIn,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
    builder: ClientBuilder = Depends(get_client_builder),
):
    """Uploads the previewed notebook to your Databricks workspace and schedules it as a job."""
    pipeline = svc.deploy_pipeline(db, org_id=ctx.org.id, user_id=ctx.user.id, body=body, client_builder=builder)
    tables = svc.table_links(db, ctx.org.id, [pipeline.id])[pipeline.id]
    last_run = runs_svc.latest_runs(db, [pipeline.id]).get(pipeline.id)
    return {**_out(pipeline, tables, last_run), "notebook_source": pipeline.notebook_source, "job_parameters": pipeline.job_parameters, "notebook_sha256": pipeline.notebook_sha256}


@router.get("", response_model=list[PipelineOut])
def list_pipelines(ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)):
    rows = db.scalars(select(Pipeline).where(Pipeline.org_id == ctx.org.id).order_by(Pipeline.created_at)).all()
    ids = [p.id for p in rows]
    links = svc.table_links(db, ctx.org.id, ids)
    latest = runs_svc.latest_runs(db, ids)
    return [_out(p, links[p.id], latest.get(p.id)) for p in rows]


@router.get("/{pipeline_id}", response_model=PipelineDetailOut)
def get_pipeline(pipeline_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)):
    pipeline = _owned(db, ctx.org.id, pipeline_id)
    tables = svc.table_links(db, ctx.org.id, [pipeline.id])[pipeline.id]
    last_run = runs_svc.latest_runs(db, [pipeline.id]).get(pipeline.id)
    return {**_out(pipeline, tables, last_run), "notebook_source": pipeline.notebook_source, "job_parameters": pipeline.job_parameters, "notebook_sha256": pipeline.notebook_sha256}


@router.delete("/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pipeline(
    pipeline_id: uuid.UUID,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
    builder: ClientBuilder = Depends(get_client_builder),
):
    """Deletes the Databricks job (and its notebook) and then the record. Admins can delete any
    pipeline; members only the ones they created."""
    pipeline = _owned(db, ctx.org.id, pipeline_id)
    if ctx.role != "admin" and pipeline.created_by != ctx.user.id:
        raise NotAllowed("Only an admin or the person who created this pipeline can delete it")
    svc.delete_pipeline(db, org_id=ctx.org.id, pipeline_id=pipeline_id, client_builder=builder)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
