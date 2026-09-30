"""Test harness: every test runs against a REAL Postgres (a throwaway '<db>_test'
database built from ../db/init/001_schema.sql), never mocks — the constraints,
partial unique indexes and cascades are part of what's being tested, and they
don't exist in a mock. Refuses to touch anything not named *_test."""
import os
import pathlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db import get_db
from app.main import app

SCHEMA_DIR = pathlib.Path(os.environ.get("SCHEMA_SQL_DIR", "/schema"))
PASSWORD = "correct-horse-battery"


def schema_files() -> list[pathlib.Path]:
    """Every migration script, in the same name order Postgres runs them on a fresh database."""
    return sorted(SCHEMA_DIR.glob("*.sql"))


def _admin_exec(sql: str) -> None:
    admin = create_engine(make_url(get_settings().database_url), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(sql))
    admin.dispose()


def _create_scratch_database(suffix: str):
    base_url = make_url(get_settings().database_url)
    name = f"{base_url.database}_{suffix}"
    assert name.endswith("_test"), "refusing to drop a database that isn't a *_test database"
    _admin_exec(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    _admin_exec(f'CREATE DATABASE "{name}"')
    return name, create_engine(base_url.set(database=name), pool_pre_ping=True)


@pytest.fixture(scope="session")
def test_engine():
    name, engine = _create_scratch_database("test")
    with engine.begin() as conn:
        for f in schema_files():
            conn.exec_driver_sql(f.read_text(encoding="utf-8"))
    yield engine
    engine.dispose()
    _admin_exec(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture()
def scratch_database():
    """An EMPTY throwaway database, for tests that build the schema themselves (migrations)."""
    name, engine = _create_scratch_database("scratch_test")
    yield engine
    engine.dispose()
    _admin_exec(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture()
def session_factory(test_engine):
    with test_engine.begin() as conn:  # clean slate; tiers (reference data) survive
        conn.execute(text("TRUNCATE orgs, users CASCADE"))
    return sessionmaker(test_engine, expire_on_commit=False)


@pytest.fixture()
def client(session_factory):
    def override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def workspace(client):
    """A fake Databricks workspace wired in as the backend's client builder."""
    from app.services.databricks_client import get_client_builder
    from tests.fakes import FakeWorkspace

    fake = FakeWorkspace()
    app.dependency_overrides[get_client_builder] = lambda: fake.build
    return fake


class Account:
    """A registered + logged-in user with their own org."""

    def __init__(self, client: TestClient, email: str, org_name: str):
        r = client.post("/auth/register", json={"email": email, "password": PASSWORD, "org_name": org_name})
        assert r.status_code == 201, r.text
        body = r.json()
        self.email = email
        self.user_id: str = body["user_id"]
        self.org_id: str = body["org"]["id"]
        r = client.post("/auth/login", data={"username": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        self.token: str = r.json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def in_org(self, org_id: str) -> dict:
        return {**self.headers, "X-Org-Id": org_id}


@pytest.fixture()
def make_account(client):
    def _make(email: str, org_name: str = "Acme") -> Account:
        return Account(client, email, org_name)

    return _make


class Joined:
    """Someone who created an account by accepting an invitation into `admin`'s org."""

    def __init__(self, client: TestClient, admin: Account, email: str, role: str = "member"):
        r = client.post("/org/invites", json={"email": email, "role": role}, headers=admin.headers)
        assert r.status_code == 201, r.text
        r = client.post("/auth/accept-invite", json={"token": r.json()["token"], "password": PASSWORD})
        assert r.status_code == 201, r.text
        self.email = email
        self.user_id: str = r.json()["user_id"]
        self.org_id: str = r.json()["org"]["id"]
        assert self.org_id == admin.org_id
        r = client.post("/auth/login", data={"username": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        self.headers = {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def join_org(client):
    def _join(admin: Account, email: str, role: str = "member") -> Joined:
        return Joined(client, admin, email, role)

    return _join


def cols(*pairs, **extra) -> list[dict]:
    """cols(("id", "BIGINT"), ("name", "STRING")) -> request-body column list."""
    return [{"name": n, "sql_type": t, **extra} for n, t in pairs]


def source_body(source_path="/Volumes/main/landing/customers.csv", columns=None, **extra) -> dict:
    return {
        "source_type": "csv",
        "source_path": source_path,
        "columns": columns or cols(("id", "BIGINT"), ("name", "STRING")),
        **extra,
    }
