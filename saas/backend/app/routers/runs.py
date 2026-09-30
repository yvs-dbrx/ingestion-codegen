import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import OrgContext, get_org_context
from app.models import PipelineRun
from app.schemas import OrgRunOut, RunOut, RunStatus
from app.services import runs as svc
from app.services.databricks_client import ClientBuilder, get_client_builder

router = APIRouter(tags=["runs"])

Limit = Annotated[int, Query(ge=1, le=200)]


def run_out(run: PipelineRun) -> RunOut:
    return RunOut.model_validate(run, from_attributes=True)


@router.post("/pipelines/{pipeline_id}/run", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def run_now(
    pipeline_id: uuid.UUID,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
    builder: ClientBuilder = Depends(get_client_builder),
):
    """Start the pipeline's job once, right now (in addition to its schedule). Refused with 409 if a
    run is already in progress."""
    return run_out(svc.run_now(db, org_id=ctx.org.id, pipeline_id=pipeline_id, client_builder=builder))


@router.post("/pipelines/{pipeline_id}/runs/refresh", response_model=list[RunOut])
def refresh_runs(
    pipeline_id: uuid.UUID,
    limit: Limit = 25,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
    builder: ClientBuilder = Depends(get_client_builder),
):
    """Ask Databricks for this job's latest runs (scheduled ones included) and update our record.
    There is no background sync yet, so scheduled runs appear here the first time you refresh."""
    svc.refresh(db, org_id=ctx.org.id, pipeline_id=pipeline_id, client_builder=builder)
    return [run_out(r) for r in svc.list_runs(db, org_id=ctx.org.id, pipeline_id=pipeline_id, limit=limit, offset=0)]


@router.get("/pipelines/{pipeline_id}/runs", response_model=list[RunOut])
def list_runs(
    pipeline_id: uuid.UUID,
    limit: Limit = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
):
    """Recorded runs, newest first. Reads our own record only — call `.../runs/refresh` to update it from Databricks."""
    return [run_out(r) for r in svc.list_runs(db, org_id=ctx.org.id, pipeline_id=pipeline_id, limit=limit, offset=offset)]


@router.get("/runs", response_model=list[OrgRunOut])
def org_runs(
    status: RunStatus | None = None,
    limit: Limit = 50,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
):
    """Recent runs across all of this organization's pipelines; filter with ?status=failed."""
    return [
        OrgRunOut(**run_out(r).model_dump(), job_name=name)
        for r, name in svc.org_runs(db, org_id=ctx.org.id, status=status, limit=limit)
    ]
