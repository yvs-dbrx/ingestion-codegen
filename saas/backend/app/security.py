import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import get_settings

_hasher = PasswordHasher()

# Verified against when a login names an unknown email, so "no such user" and
# "wrong password" cost the same time and can't be told apart by timing.
DUMMY_HASH = _hasher.hash("timing-equalisation-only")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):  # VerifyMismatchError is a VerificationError
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def create_access_token(user_id: uuid.UUID, expires_delta: timedelta | None = None) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    exp = now + (expires_delta if expires_delta is not None else timedelta(minutes=settings.jwt_expire_minutes))
    return jwt.encode(
        {"sub": str(user_id), "iat": now, "exp": exp},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> uuid.UUID | None:
    """Returns the user id, or None for any invalid/expired/tampered token."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],  # pinned: never trust the token's own "alg"
            options={"require": ["exp", "sub"]},
        )
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        return None
