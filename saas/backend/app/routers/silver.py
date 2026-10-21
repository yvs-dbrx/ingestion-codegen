from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import OrgContext, get_org_context
from app.routers.pipelines import detail_out
from app.schemas import PipelineDetailOut, PipelinePreviewOut, SilverDeployIn, SilverPipelineIn
from app.services import silver as svc
from app.services.databricks_client import ClientBuilder, get_client_builder

router = APIRouter(prefix="/silver", tags=["silver"])


@router.post("/preview", response_model=PipelinePreviewOut)
def preview(body: SilverPipelineIn, ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)):
    """Validates the design and generates the Silver notebook for review. Changes nothing, in the database
    or in Databricks. Send the returned notebook_sha256 with the deploy request."""
    plan = svc.build_silver_plan(db, ctx.org.id, body)
    return PipelinePreviewOut(
        notebook=plan.notebook, notebook_sha256=plan.notebook_sha256, workspace_path=plan.workspace_path, cron=plan.cron
    )


@router.post("", response_model=PipelineDetailOut, status_code=status.HTTP_201_CREATED)
def deploy(
    body: SilverDeployIn,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
    builder: ClientBuilder = Depends(get_client_builder),
):
    """Uploads the previewed notebook to your Databricks workspace and schedules it as a job. It builds one
    current row per business key, quarantines records that fail the design's rules, and resolves master keys
    through your crosswalk."""
    pipeline = svc.deploy_silver(db, org_id=ctx.org.id, user_id=ctx.user.id, body=body, client_builder=builder)
    return detail_out(db, ctx.org.id, pipeline)
