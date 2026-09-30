import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import OrgContext, get_current_user, get_org_context, require_admin
from app.models import User
from app.schemas import (
    AcceptInviteIn,
    InviteCreatedOut,
    InviteIn,
    InviteOut,
    MemberOut,
    MembershipOut,
    OrgOut,
)
from app.services import membership

router = APIRouter(tags=["team"])


def _invite_out(invite) -> dict:
    return {
        "id": invite.id,
        "email": invite.email,
        "role": invite.role,
        "created_at": invite.created_at,
        "expires_at": invite.expires_at,
    }


@router.get("/org/members", response_model=list[MemberOut])
def list_members(ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)):
    return [
        MemberOut(user_id=u.id, email=u.email, role=m.role, joined_at=m.joined_at)
        for u, m in membership.list_members(db, ctx.org.id)
    ]


@router.post("/org/invites", response_model=InviteCreatedOut, status_code=status.HTTP_201_CREATED)
def create_invite(body: InviteIn, ctx: OrgContext = Depends(require_admin), db: Session = Depends(get_db)):
    """Invite someone by email (admins only). Nothing is emailed yet: hand the returned token to
    the invitee — it is shown only in this response."""
    invite, token = membership.create_invite(
        db, org_id=ctx.org.id, invited_by=ctx.user.id, email=str(body.email), role=body.role
    )
    db.commit()
    return InviteCreatedOut(**_invite_out(invite), token=token)


@router.get("/org/invites", response_model=list[InviteOut])
def list_invites(ctx: OrgContext = Depends(require_admin), db: Session = Depends(get_db)):
    """Invitations still waiting to be accepted (the tokens themselves are never shown again)."""
    return [_invite_out(i) for i in membership.list_open_invites(db, ctx.org.id)]


@router.delete("/org/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(invite_id: uuid.UUID, ctx: OrgContext = Depends(require_admin), db: Session = Depends(get_db)):
    membership.revoke_invite(db, ctx.org.id, invite_id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/org/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(user_id: uuid.UUID, ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)):
    """Frees a seat. Admins can remove anyone; anyone can remove themselves; never the last admin."""
    membership.remove_member(
        db, org_id=ctx.org.id, actor_id=ctx.user.id, actor_role=ctx.role, target_user_id=user_id
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invites/accept", response_model=MembershipOut)
def accept_invite(body: AcceptInviteIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """For someone who already has an account: accept an invitation addressed to their email."""
    org, role = membership.accept_invite_existing_account(db, token=body.token, user=user)
    db.commit()
    return MembershipOut(org=OrgOut(id=org.id, name=org.name, tier=org.tier), role=role)
