from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Org, OrgMember, User
from app.schemas import AcceptInviteNewAccountIn, OrgOut, RegisterIn, RegisterOut, TokenOut
from app.services import membership
from app.security import (
    DUMMY_HASH,
    create_access_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=RegisterOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    """Creates the account and its own organization (on the Free plan), with the user as admin,
    and returns an access token — signing up logs you in."""
    email = str(body.email).strip()
    if db.scalar(select(User.id).where(func.lower(User.email) == email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email already exists")

    user = User(email=email, password_hash=hash_password(body.password))
    org = Org(name=body.org_name)
    db.add_all([user, org])
    try:
        db.flush()
        db.add(OrgMember(org_id=org.id, user_id=user.id, role="admin"))
        db.commit()
    except IntegrityError:  # lost a race with a concurrent signup for the same email
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email already exists")

    return RegisterOut(
        user_id=user.id,
        email=user.email,
        org=OrgOut(id=org.id, name=org.name, tier=org.tier),
        access_token=create_access_token(user.id),
    )


@router.post("/accept-invite", response_model=RegisterOut, status_code=status.HTTP_201_CREATED)
def accept_invite_new_account(body: AcceptInviteNewAccountIn, db: Session = Depends(get_db)):
    """Create an account by accepting an invitation. The email is the one the invitation was sent
    to (it can't be chosen here), and the account joins that organization instead of getting its own."""
    # Hash first: it's deliberately slow, and shouldn't happen while the org row is locked.
    user, org = membership.accept_invite_new_account(db, token=body.token, password_hash=hash_password(body.password))
    db.commit()
    return RegisterOut(
        user_id=user.id,
        email=user.email,
        org=OrgOut(id=org.id, name=org.name, tier=org.tier),
        access_token=create_access_token(user.id),
    )


@router.post("/login", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """OAuth2 password flow: `username` is the email. Same error for unknown email and wrong password."""
    user = db.scalar(select(User).where(func.lower(User.email) == form.username.strip().lower()))
    if user is None:
        verify_password(DUMMY_HASH, form.password)  # burn the same time as a real check
        ok = False
    else:
        ok = verify_password(user.password_hash, form.password)

    if not ok:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(form.password)
        db.commit()

    return TokenOut(access_token=create_access_token(user.id))
