import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import OrgContext, get_org_context
from app.models import SourceRegistry
from app.schemas import DriftOut, RegisterSourceIn, RegisterSourceOut, SourceOut
from app.services.source_registry import register_source_version, stale_pipelines

router = APIRouter(tags=["sources"])


def _to_out(row: SourceRegistry) -> SourceOut:
    return SourceOut(
        id=row.id,
        source_type=row.source_type,
        source_path=row.source_path,
        schema_definition_path=row.schema_definition_path,
        discovery_config=row.discovery_config,
        columns=row.schema_json,
        schema_hash=row.schema_hash,
        version=row.version,
        record_is_active=row.record_is_active,
        record_start_ts=row.record_start_ts,
        record_end_ts=row.record_end_ts,
        last_verified_at=row.last_verified_at,
    )


def _get_owned(db: Session, org_id: uuid.UUID, source_id: uuid.UUID) -> SourceRegistry:
    # Filtering by org here (not just by id) is what stops one tenant reading another's source.
    row = db.scalar(select(SourceRegistry).where(SourceRegistry.id == source_id, SourceRegistry.org_id == org_id))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    return row


@router.post("/sources", response_model=RegisterSourceOut)
def register_source(
    body: RegisterSourceIn,
    response: Response,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
):
    """Record a discovered schema for a source file/copybook.

    201 when a new version was written (first sight, or the schema drifted);
    200 when the active version already matches and was just re-verified.
    """
    result = register_source_version(
        db,
        org_id=ctx.org.id,
        user_id=ctx.user.id,
        source_type=body.source_type,
        source_path=body.source_path,
        schema_definition_path=body.schema_definition_path,
        discovery_config=body.discovery_config,
        columns=body.columns,
    )
    db.commit()
    response.status_code = status.HTTP_200_OK if result.status == "unchanged" else status.HTTP_201_CREATED
    return RegisterSourceOut(
        status=result.status, previous_version=result.previous_version, source=_to_out(result.source)
    )


@router.get("/sources", response_model=list[SourceOut])
def list_sources(
    source_type: str | None = None,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
):
    """The current (active) version of every source in this organization."""
    stmt = select(SourceRegistry).where(SourceRegistry.org_id == ctx.org.id, SourceRegistry.record_is_active)
    if source_type:
        stmt = stmt.where(SourceRegistry.source_type == source_type)
    rows = db.scalars(stmt.order_by(SourceRegistry.source_type, SourceRegistry.source_path)).all()
    return [_to_out(r) for r in rows]


@router.get("/drift", response_model=list[DriftOut])
def drift(ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)):
    """Pipelines built from a schema version that has since been superseded — flagged for review."""
    return stale_pipelines(db, ctx.org.id)


@router.get("/sources/{source_id}", response_model=SourceOut)
def get_source(source_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)):
    return _to_out(_get_owned(db, ctx.org.id, source_id))


@router.get("/sources/{source_id}/history", response_model=list[SourceOut])
def source_history(source_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)):
    """Every version this source has had, newest first. The id can be any version's id."""
    row = _get_owned(db, ctx.org.id, source_id)
    versions = db.scalars(
        select(SourceRegistry)
        .where(
            SourceRegistry.org_id == ctx.org.id,
            SourceRegistry.source_type == row.source_type,
            SourceRegistry.source_path == row.source_path,
            func.coalesce(SourceRegistry.schema_definition_path, "") == (row.schema_definition_path or ""),
        )
        .order_by(SourceRegistry.version.desc())
    ).all()
    return [_to_out(v) for v in versions]
