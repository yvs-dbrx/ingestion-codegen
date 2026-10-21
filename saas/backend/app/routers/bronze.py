from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import OrgContext, get_org_context
from app.routers.pipelines import detail_out
from app.schemas import BronzeDeployIn, BronzePipelineIn, PipelineDetailOut, PipelinePreviewOut
from app.services import bronze as svc
from app.services.databricks_client import ClientBuilder, get_client_builder

router = APIRouter(prefix="/bronze", tags=["bronze"])


@router.post("/preview", response_model=PipelinePreviewOut)
def preview(body: BronzePipelineIn, ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)):
    """Generates the Bronze notebook for review. Changes nothing, in the database or in Databricks.
    Send the returned notebook_sha256 with the deploy request."""
    plan = svc.build_bronze_plan(db, ctx.org.id, body)
    return PipelinePreviewOut(
        notebook=plan.notebook, notebook_sha256=plan.notebook_sha256, workspace_path=plan.workspace_path, cron=plan.cron
    )


@router.post("", response_model=PipelineDetailOut, status_code=status.HTTP_201_CREATED)
def deploy(
    body: BronzeDeployIn,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
    builder: ClientBuilder = Depends(get_client_builder),
):
    """Uploads the previewed notebook to your Databricks workspace and schedules it as a job. It keeps one
    copy of each distinct record from the chosen Raw tables, and never updates or deletes a Bronze row."""
    pipeline = svc.deploy_bronze(db, org_id=ctx.org.id, user_id=ctx.user.id, body=body, client_builder=builder)
    return detail_out(db, ctx.org.id, pipeline)
