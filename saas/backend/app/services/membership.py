"""Team membership: invitations, the per-org member cap, and removing members.

The cap is `tiers.member_limit` (5 today). A seat is taken by a member OR by a pending
invitation, so an admin can't hand out more invitations than there are seats. Every
decision that reads the seat count happens while holding a lock on the org's row
(_lock_org) — without it, two people accepting at the same instant would both see "4 of
5" and both get in. Lock order is always org first, then invitation, so two requests can't
deadlock each other.
"""
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Org, OrgInvite, OrgMember, Tier, User
from app.services.errors import ServiceError
from app.services.tenancy import lock_org as _lock_org

INVITE_TTL = timedelta(days=7)


class MembershipError(ServiceError):
    """A membership rule was violated; carries the HTTP status the API should answer with."""


class MemberLimitReached(MembershipError):
    status_code = 409


class AlreadyMember(MembershipError):
    status_code = 409


class AccountExists(MembershipError):
    status_code = 409


class LastAdmin(MembershipError):
    status_code = 409


class NotPermitted(MembershipError):
    status_code = 403


class NotFound(MembershipError):
    status_code = 404


class InvalidInvite(MembershipError):
    """Unknown, expired, revoked, already used, or addressed to someone else — all deliberately
    the same answer, so a token holder can't probe which of those it was."""

    def __init__(self):
        super().__init__("This invitation is invalid or has expired")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class SeatCounts:
    member_count: int
    pending_invites: int
    member_limit: int


def _open_invites(org_id: uuid.UUID):
    return (
        OrgInvite.org_id == org_id,
        OrgInvite.accepted_at.is_(None),
        OrgInvite.revoked_at.is_(None),
        OrgInvite.expires_at > func.now(),
    )


def seat_counts(db: Session, org_id: uuid.UUID) -> SeatCounts:
    members = db.scalar(select(func.count()).select_from(OrgMember).where(OrgMember.org_id == org_id))
    pending = db.scalar(select(func.count()).select_from(OrgInvite).where(*_open_invites(org_id)))
    limit = db.scalar(select(Tier.member_limit).join(Org, Org.tier == Tier.name).where(Org.id == org_id))
    return SeatCounts(members or 0, pending or 0, limit)


def _full_message(limit: int) -> str:
    return f"This organization has reached its limit of {limit} members (invitations waiting to be accepted count toward it)"


# --- inviting -----------------------------------------------------------------------

def create_invite(
    db: Session, *, org_id: uuid.UUID, invited_by: uuid.UUID, email: str, role: str
) -> tuple[OrgInvite, str]:
    """Returns the invitation and its RAW token — the only time the token exists in the clear."""
    email = email.strip().lower()
    _lock_org(db, org_id)

    if db.scalar(
        select(OrgMember.user_id)
        .join(User, User.id == OrgMember.user_id)
        .where(OrgMember.org_id == org_id, func.lower(User.email) == email)
    ):
        raise AlreadyMember("That person is already a member of this organization")

    # Re-inviting an address replaces the earlier invitation (e.g. a lost link). Done inside a
    # savepoint so that if the cap then rejects the new one, the old one is left untouched.
    with db.begin_nested():
        for old in db.scalars(
            select(OrgInvite)
            .where(
                OrgInvite.org_id == org_id,
                OrgInvite.email == email,
                OrgInvite.accepted_at.is_(None),
                OrgInvite.revoked_at.is_(None),
            )
            .with_for_update()
        ):
            old.revoked_at = func.now()
        db.flush()

        counts = seat_counts(db, org_id)
        if counts.member_count + counts.pending_invites >= counts.member_limit:
            raise MemberLimitReached(_full_message(counts.member_limit))

        token = secrets.token_urlsafe(32)
        invite = OrgInvite(
            org_id=org_id,
            email=email,
            role=role,
            token_hash=hash_token(token),
            invited_by=invited_by,
            expires_at=func.now() + INVITE_TTL,
        )
        db.add(invite)
        db.flush()
    db.refresh(invite)
    return invite, token


def list_open_invites(db: Session, org_id: uuid.UUID) -> list[OrgInvite]:
    return list(db.scalars(select(OrgInvite).where(*_open_invites(org_id)).order_by(OrgInvite.created_at)))


def revoke_invite(db: Session, org_id: uuid.UUID, invite_id: uuid.UUID) -> None:
    invite = db.scalar(
        select(OrgInvite)
        .where(
            OrgInvite.id == invite_id,
            OrgInvite.org_id == org_id,  # tenant filter: another org's invitation is simply "not found"
            OrgInvite.accepted_at.is_(None),
            OrgInvite.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if invite is None:
        raise NotFound("Invitation not found")
    invite.revoked_at = func.now()
    db.flush()


# --- accepting ----------------------------------------------------------------------

def _consume_invite(db: Session, token: str) -> OrgInvite:
    """Finds a usable invitation for this token, locks it, checks the seat, and returns it."""
    found = db.scalar(select(OrgInvite).where(OrgInvite.token_hash == hash_token(token)))
    if found is None:
        raise InvalidInvite()

    _lock_org(db, found.org_id)  # org first, then the invitation — same order everywhere
    invite = db.scalar(
        select(OrgInvite)
        .where(OrgInvite.id == found.id)
        .with_for_update()
        .execution_options(populate_existing=True)  # re-read: it may have changed while we waited for the lock
    )
    if (
        invite.accepted_at is not None
        or invite.revoked_at is not None
        or invite.expires_at <= db.scalar(select(func.now()))
    ):
        raise InvalidInvite()

    counts = seat_counts(db, invite.org_id)
    if counts.member_count >= counts.member_limit:
        raise MemberLimitReached(_full_message(counts.member_limit))
    return invite


def _join(db: Session, invite: OrgInvite, user_id: uuid.UUID) -> Org:
    db.add(OrgMember(org_id=invite.org_id, user_id=user_id, role=invite.role))
    invite.accepted_at = func.now()
    db.flush()
    return db.get(Org, invite.org_id)


def accept_invite_new_account(db: Session, *, token: str, password_hash: str) -> tuple[User, Org]:
    """The invitee has no account yet. The email comes from the invitation, never from the request."""
    invite = _consume_invite(db, token)
    if db.scalar(select(User.id).where(func.lower(User.email) == invite.email)):
        raise AccountExists(
            "An account with this email already exists — log in with it and accept the invitation from there"
        )
    user = User(email=invite.email, password_hash=password_hash)
    db.add(user)
    try:
        db.flush()
    except IntegrityError:  # someone registered this address a moment ago
        raise AccountExists(
            "An account with this email already exists — log in with it and accept the invitation from there"
        )
    return user, _join(db, invite, user.id)


def accept_invite_existing_account(db: Session, *, token: str, user: User) -> tuple[Org, str]:
    invite = _consume_invite(db, token)
    if invite.email != user.email.strip().lower():
        raise InvalidInvite()  # addressed to someone else
    if db.get(OrgMember, (invite.org_id, user.id)) is not None:
        raise AlreadyMember("You are already a member of this organization")
    return _join(db, invite, user.id), invite.role


# --- members ------------------------------------------------------------------------

def list_members(db: Session, org_id: uuid.UUID) -> list[tuple[User, OrgMember]]:
    return list(
        db.execute(
            select(User, OrgMember)
            .join(OrgMember, OrgMember.user_id == User.id)
            .where(OrgMember.org_id == org_id)
            .order_by(OrgMember.joined_at, User.email)
        ).all()
    )


def remove_member(
    db: Session, *, org_id: uuid.UUID, actor_id: uuid.UUID, actor_role: str, target_user_id: uuid.UUID
) -> None:
    """Admins can remove anyone; anyone can remove themselves. Never the last admin."""
    _lock_org(db, org_id)
    target = db.get(OrgMember, (org_id, target_user_id))
    if target is None:
        raise NotFound("Member not found")
    if actor_role != "admin" and actor_id != target_user_id:
        raise NotPermitted("Only an admin can remove other members")
    if target.role == "admin":
        admins = db.scalar(
            select(func.count()).select_from(OrgMember).where(OrgMember.org_id == org_id, OrgMember.role == "admin")
        )
        if admins <= 1:
            raise LastAdmin("The last admin can't be removed — make someone else an admin first")
    db.delete(target)
    db.flush()
