import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Org, OrgMember, User
from app.security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    user_id = decode_access_token(token)
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


@dataclass
class OrgContext:
    org: Org
    role: str
    user: User


def get_org_context(
    x_org_id: uuid.UUID | None = Header(
        default=None,
        description="Organization to act on. Optional: only needed if your account belongs to more than one organization.",
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrgContext:
    """Which organization this request acts on behalf of — the tenant boundary.

    Every tenant-scoped query must filter by ctx.org.id. The org is only ever
    taken from the caller's own memberships, never trusted from the request:
    an org the caller doesn't belong to is indistinguishable from one that
    doesn't exist (404, not 403 — don't confirm which org ids are real).
    """
    memberships = db.execute(
        select(Org, OrgMember.role)
        .join(OrgMember, OrgMember.org_id == Org.id)
        .where(OrgMember.user_id == user.id)
        .order_by(Org.created_at)
    ).all()

    if x_org_id is not None:
        for org, role in memberships:
            if org.id == x_org_id:
                return OrgContext(org=org, role=role, user=user)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")

    if len(memberships) == 1:
        org, role = memberships[0]
        return OrgContext(org=org, role=role, user=user)
    if not memberships:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User does not belong to any organization")
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        "User belongs to multiple organizations — send an X-Org-Id header to choose one",
    )


def require_admin(ctx: OrgContext = Depends(get_org_context)) -> OrgContext:
    if ctx.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return ctx
