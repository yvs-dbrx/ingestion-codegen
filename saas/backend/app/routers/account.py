from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import OrgContext, get_current_user, get_org_context
from app.models import Org, OrgMember, User
from app.schemas import MembershipOut, MeOut, OrgOut, UsageOut
from app.services.membership import seat_counts

router = APIRouter(tags=["account"])


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Org, OrgMember.role)
        .join(OrgMember, OrgMember.org_id == Org.id)
        .where(OrgMember.user_id == user.id)
        .order_by(Org.created_at)
    ).all()
    return MeOut(
        user_id=user.id,
        email=user.email,
        memberships=[
            MembershipOut(org=OrgOut(id=o.id, name=o.name, tier=o.tier), role=role) for o, role in rows
        ],
    )


@router.get("/org/usage", response_model=UsageOut)
def org_usage(ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)):
    """Plan usage (PRD 5.8) — the check that gates creating a new pipeline."""
    row = db.execute(
        text(
            "SELECT org_id, org_name, tier, pipeline_limit, pipeline_count, can_create_pipeline "
            "FROM org_pipeline_usage WHERE org_id = :org_id"
        ),
        {"org_id": ctx.org.id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    seats = seat_counts(db, ctx.org.id)
    return UsageOut(
        **row,
        member_limit=seats.member_limit,
        member_count=seats.member_count,
        pending_invites=seats.pending_invites,
    )
