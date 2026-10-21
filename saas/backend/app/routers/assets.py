import uuid
from typing import Literal

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import OrgContext, get_org_context
from app.schemas import AssetDetailOut, AssetOut, AssetProducer, AssetRef, ReferenceTableIn
from app.services import assets as svc
from app.services.databricks_client import ClientBuilder, client_for_org, get_client_builder

router = APIRouter(prefix="/assets", tags=["tables"])


def _out(view: svc.AssetView) -> dict:
    a = view.asset
    return {
        "id": a.id,
        "catalog": a.catalog,
        "schema_name": a.schema_name,
        "table_name": a.table_name,
        "full_name": svc.full_name(a),
        "layer": a.layer,
        "source_system": a.source_system,
        "origin": a.origin,
        "produced_by": AssetProducer(pipeline_id=view.producer.id, job_name=view.producer.job_name)
        if view.producer
        else None,
        "column_count": len(a.columns or []),
        "row_count": a.row_count,
        "last_run_at": a.last_run_at,
        "created_at": a.created_at,
        "upstream_count": view.upstream_count,
        "downstream_count": view.downstream_count,
    }


def _ref(a) -> AssetRef:
    return AssetRef(id=a.id, full_name=svc.full_name(a), layer=a.layer)


@router.get("", response_model=list[AssetOut])
def list_assets(
    layer: Literal["raw", "bronze", "silver", "gold", "reference"] | None = None,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
):
    """Every table this organization's pipelines produce (and reference tables it relies on)."""
    return [_out(v) for v in svc.list_assets(db, ctx.org.id, layer)]


@router.post("/reference", response_model=AssetDetailOut, status_code=status.HTTP_201_CREATED)
def register_reference(
    body: ReferenceTableIn,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
    builder: ClientBuilder = Depends(get_client_builder),
):
    """Registers a table that already exists in your workspace (such as a source-key to master-key crosswalk)
    so Silver pipelines can look values up in it. Its columns are read from Databricks; its rows never are."""
    client = client_for_org(db, ctx.org.id, builder)
    asset = svc.register_reference(
        db, org_id=ctx.org.id, client=client, catalog=body.catalog, schema_name=body.schema_name, table_name=body.table_name
    )
    db.commit()
    view, upstream, downstream = svc.get_asset(db, ctx.org.id, asset.id)
    return {**_out(view), "columns": asset.columns or [], "upstream": [_ref(a) for a in upstream], "downstream": [_ref(a) for a in downstream]}


@router.get("/{asset_id}", response_model=AssetDetailOut)
def get_asset(asset_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)):
    view, upstream, downstream = svc.get_asset(db, ctx.org.id, asset_id)
    return {**_out(view), "columns": view.asset.columns or [], "upstream": [_ref(a) for a in upstream], "downstream": [_ref(a) for a in downstream]}
