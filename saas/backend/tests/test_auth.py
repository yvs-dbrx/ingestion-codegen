import time
import uuid
from datetime import timedelta

import jwt
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.config import DEV_JWT_SECRET, Settings
from app.security import create_access_token
from tests.conftest import PASSWORD


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_register_creates_the_user_and_their_own_org_on_the_free_plan(client, make_account):
    acct = make_account("alice@acme-corp.io", "Acme Corp")
    me = client.get("/me", headers=acct.headers).json()
    assert me["email"] == "alice@acme-corp.io"
    assert len(me["memberships"]) == 1
    membership = me["memberships"][0]
    assert membership["role"] == "admin"
    assert membership["org"] == {"id": acct.org_id, "name": "Acme Corp", "tier": "free"}


def test_password_is_stored_only_as_an_argon2_hash(client, make_account, session_factory):
    make_account("alice@acme-corp.io")
    with session_factory() as s:
        stored = s.execute(text("SELECT password_hash FROM users")).scalar_one()
    assert stored.startswith("$argon2")
    assert PASSWORD not in stored


def test_duplicate_email_is_rejected_case_insensitively(client, make_account):
    make_account("bob@acme-corp.io")
    r = client.post("/auth/register", json={"email": "BOB@Acme-Corp.io", "password": PASSWORD, "org_name": "Other"})
    assert r.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"email": "c@acme-corp.io", "password": "short", "org_name": "X"},
        {"email": "not-an-email", "password": PASSWORD, "org_name": "X"},
        {"email": "c@acme-corp.io", "password": PASSWORD, "org_name": "   "},
        {"email": "c@acme-corp.io", "password": "x" * 129, "org_name": "X"},
        {"email": "c@acme-corp.io", "password": PASSWORD},
    ],
)
def test_bad_registrations_are_rejected(client, body):
    assert client.post("/auth/register", json=body).status_code == 422


def test_wrong_password_and_unknown_email_are_indistinguishable(client, make_account):
    make_account("dave@acme-corp.io")
    wrong_pw = client.post("/auth/login", data={"username": "dave@acme-corp.io", "password": "wrong-password-123"})
    no_user = client.post("/auth/login", data={"username": "nobody@acme-corp.io", "password": "wrong-password-123"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()


def test_login_email_is_case_insensitive(client, make_account):
    make_account("erin@acme-corp.io")
    r = client.post("/auth/login", data={"username": "ERIN@Acme-Corp.io", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["token_type"] == "bearer"


def test_protected_routes_reject_missing_or_garbage_tokens(client):
    for path in ("/me", "/org/usage", "/sources", "/drift"):
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers=bearer("garbage")).status_code == 401, path


def test_expired_token_is_rejected(client, make_account):
    acct = make_account("frank@acme-corp.io")
    expired = create_access_token(uuid.UUID(acct.user_id), expires_delta=timedelta(seconds=-5))
    assert client.get("/me", headers=bearer(expired)).status_code == 401


def test_token_signed_with_another_secret_is_rejected(client, make_account):
    acct = make_account("gina@acme-corp.io")
    forged = jwt.encode(
        {"sub": acct.user_id, "exp": int(time.time()) + 3600},
        "an-attackers-own-secret-that-is-long-enough",
        algorithm="HS256",
    )
    assert client.get("/me", headers=bearer(forged)).status_code == 401


def test_unsigned_alg_none_token_is_rejected(client, make_account):
    acct = make_account("hank@acme-corp.io")
    unsigned = jwt.encode({"sub": acct.user_id, "exp": int(time.time()) + 3600}, key="", algorithm="none")
    assert client.get("/me", headers=bearer(unsigned)).status_code == 401


def test_valid_token_for_a_deleted_user_is_rejected(client, make_account, session_factory):
    acct = make_account("ivy@acme-corp.io")
    assert client.get("/me", headers=acct.headers).status_code == 200
    with session_factory() as s:
        s.execute(text("DELETE FROM users WHERE id = :id"), {"id": acct.user_id})
        s.commit()
    assert client.get("/me", headers=acct.headers).status_code == 401


def test_dev_jwt_secret_is_refused_outside_dev():
    with pytest.raises(RuntimeError):
        Settings(database_url="postgresql://x", environment="prod").validate_for_environment()
    with pytest.raises(RuntimeError):
        Settings(database_url="postgresql://x", environment="prod", jwt_secret="too-short").validate_for_environment()
    real_key = Fernet.generate_key().decode()
    Settings(
        database_url="postgresql://x", environment="prod", jwt_secret="x" * 40, credential_encryption_key=real_key
    ).validate_for_environment()
    Settings(database_url="postgresql://x", environment="dev", jwt_secret=DEV_JWT_SECRET).validate_for_environment()


def test_dev_credential_key_is_refused_outside_dev():
    with pytest.raises(RuntimeError, match="CREDENTIAL_ENCRYPTION_KEY"):
        Settings(database_url="postgresql://x", environment="prod", jwt_secret="x" * 40).validate_for_environment()


def test_register_returns_a_token_that_works_immediately(client):
    r = client.post("/auth/register", json={"email": "new@a.com", "password": PASSWORD, "org_name": "Acme"})
    assert r.status_code == 201
    body = r.json()
    assert body["token_type"] == "bearer"
    me = client.get("/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == "new@a.com"


def test_accepting_an_invitation_also_logs_the_new_member_in(client, make_account):
    admin = make_account("admin@a.com")
    token = client.post("/org/invites", json={"email": "joiner@a.com"}, headers=admin.headers).json()["token"]
    r = client.post("/auth/accept-invite", json={"token": token, "password": PASSWORD})
    assert r.status_code == 201
    me = client.get("/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["email"] == "joiner@a.com"
    assert me.json()["memberships"][0]["org"]["id"] == admin.org_id


def test_the_login_instructions_are_in_the_api_docs(client):
    spec = client.get("/openapi.json").json()
    assert "Authorize" in spec["info"]["description"]
