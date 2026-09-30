from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import OrgContext, get_org_context, require_admin
from app.models import OrgConnection
from app.schemas import ConnectionIn, ConnectionOut
from app.services import databricks_client, engine, secrets
from app.services.databricks_client import ClientBuilder, get_client_builder
from app.services.errors import Unprocessable

router = APIRouter(prefix="/org/connection", tags=["databricks connection"])


@router.put("", response_model=ConnectionOut)
def connect(
    body: ConnectionIn,
    ctx: OrgContext = Depends(require_admin),
    db: Session = Depends(get_db),
    builder: ClientBuilder = Depends(get_client_builder),
):
    """Connect (or replace) this organization's Databricks workspace. The host is checked against
    Databricks' own domains and the token is proven to work before anything is stored; the token
    is encrypted at rest and never returned by any endpoint."""
    host = databricks_client.normalize_host(body.host)
    try:
        engine.verify_connection(builder(host, body.token))
    except Exception:
        # Deliberately generic: the SDK's message can echo request details.
        raise Unprocessable("Databricks didn't accept that workspace address and token")

    row = db.scalar(select(OrgConnection).where(OrgConnection.org_id == ctx.org.id).with_for_update())
    if row is None:
        row = OrgConnection(org_id=ctx.org.id, host=host, token_ciphertext=secrets.encrypt(body.token), created_by=ctx.user.id)
        db.add(row)
    else:
        row.host = host
        row.token_ciphertext = secrets.encrypt(body.token)
        row.updated_at = func.now()
    db.commit()
    db.refresh(row)
    return ConnectionOut(host=row.host, updated_at=row.updated_at)


@router.get("", response_model=ConnectionOut)
def get_connection(ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)):
    row = db.scalar(select(OrgConnection).where(OrgConnection.org_id == ctx.org.id))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No Databricks workspace connected")
    return ConnectionOut(host=row.host, updated_at=row.updated_at)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(ctx: OrgContext = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.scalar(select(OrgConnection).where(OrgConnection.org_id == ctx.org.id))
    if row is not None:
        db.delete(row)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
