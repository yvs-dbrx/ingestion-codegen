"""Connecting an organization's Databricks workspace: who may, what's stored, what's never shown."""
import pytest
from sqlalchemy import select, text

from app.models import OrgConnection
from app.services import secrets

HOST = "fake-1.cloud.databricks.com"
TOKEN = "dapi-super-secret-token-123456"


def connect(client, acct, host=HOST, token=TOKEN):
    return client.put("/org/connection", json={"host": host, "token": token}, headers=acct.headers)


def test_admin_connects_and_the_token_is_stored_encrypted(client, make_account, workspace, session_factory):
    acct = make_account("admin@a.com")
    r = connect(client, acct)
    assert r.status_code == 200, r.text
    assert r.json()["host"] == f"https://{HOST}"
    assert TOKEN not in r.text

    with session_factory() as s:
        row = s.scalar(select(OrgConnection).where(OrgConnection.org_id == acct.org_id))
        assert row.token_ciphertext != TOKEN and TOKEN not in row.token_ciphertext
        assert secrets.decrypt(row.token_ciphertext) == TOKEN
    assert workspace.builds == [(f"https://{HOST}", TOKEN)]


def test_the_token_is_never_returned_by_any_endpoint(client, make_account, workspace):
    acct = make_account("admin@a.com")
    connect(client, acct)
    seen = client.get("/org/connection", headers=acct.headers).text + client.get("/org/usage", headers=acct.headers).text
    assert TOKEN not in seen and "token" not in client.get("/org/connection", headers=acct.headers).json()


def test_a_member_can_see_the_connection_but_not_change_it(client, make_account, join_org, workspace):
    admin = make_account("admin@a.com")
    connect(client, admin)
    member = join_org(admin, "m@a.com")
    assert client.get("/org/connection", headers=member.headers).status_code == 200
    assert connect(client, member, token="another-token-xyz-123").status_code == 403
    assert client.delete("/org/connection", headers=member.headers).status_code == 403
    assert workspace.builds == [(f"https://{HOST}", TOKEN)]  # the member's attempt never reached Databricks


@pytest.mark.parametrize(
    "host",
    [
        "evil.com",
        "http://fake-1.cloud.databricks.com",
        "https://databricks.com.evil.com",
        "169.254.169.254",
        "localhost",
        "https://fake-1.cloud.databricks.com/api",
        "https://user:pw@fake-1.cloud.databricks.com",
    ],
)
def test_a_host_that_isnt_databricks_is_rejected_before_any_connection(client, make_account, workspace, host):
    acct = make_account("admin@a.com")
    assert connect(client, acct, host=host).status_code == 422
    assert workspace.builds == []  # the server never dialled it
    assert client.get("/org/connection", headers=acct.headers).status_code == 404


def test_a_token_databricks_rejects_is_not_stored(client, make_account, workspace):
    acct = make_account("admin@a.com")
    workspace.rejected_tokens.add("bad-token-value-1")
    r = connect(client, acct, token="bad-token-value-1")
    assert r.status_code == 422
    assert "bad-token-value-1" not in r.text
    assert client.get("/org/connection", headers=acct.headers).status_code == 404


def test_reconnecting_replaces_the_stored_connection(client, make_account, workspace, session_factory):
    acct = make_account("admin@a.com")
    connect(client, acct)
    assert connect(client, acct, host="fake-2.cloud.databricks.com", token="second-token-value-99").status_code == 200
    body = client.get("/org/connection", headers=acct.headers).json()
    assert body["host"] == "https://fake-2.cloud.databricks.com"
    with session_factory() as s:
        rows = s.scalars(select(OrgConnection)).all()
        assert len(rows) == 1 and secrets.decrypt(rows[0].token_ciphertext) == "second-token-value-99"


def test_disconnect(client, make_account, workspace):
    acct = make_account("admin@a.com")
    connect(client, acct)
    assert client.delete("/org/connection", headers=acct.headers).status_code == 204
    assert client.get("/org/connection", headers=acct.headers).status_code == 404
    assert client.delete("/org/connection", headers=acct.headers).status_code == 204  # idempotent


def test_connections_are_per_organization(client, make_account, workspace):
    a = make_account("admin@a.com", "A")
    b = make_account("admin@b.com", "B")
    connect(client, a)
    assert client.get("/org/connection", headers=b.headers).status_code == 404
    assert client.get("/org/connection", headers=b.in_org(a.org_id)).status_code == 404  # not B's org


def test_deleting_the_org_removes_the_stored_token(client, make_account, workspace, session_factory):
    from sqlalchemy import text

    acct = make_account("admin@a.com")
    connect(client, acct)
    with session_factory() as s:
        s.execute(text("DELETE FROM orgs"))
        s.commit()
        assert s.scalar(select(OrgConnection)) is None


def test_the_stored_token_is_unreadable_with_a_different_key(client, make_account, workspace, monkeypatch):
    from cryptography.fernet import Fernet

    from app.config import get_settings

    acct = make_account("admin@a.com")
    connect(client, acct)
    monkeypatch.setattr(get_settings(), "credential_encryption_key", Fernet.generate_key().decode())

    r = client.post(
        "/discover",
        json={"source_type": "csv", "source_path": "/Volumes/main/landing/a.csv"},
        headers=acct.headers,
    )
    assert r.status_code == 409 and "reconnect" in r.json()["detail"].lower()
    assert workspace.builds == [(f"https://{HOST}", TOKEN)]  # only the original connect; no client with a garbage token


def test_migration_003_upgrades_an_existing_database_in_place_and_can_be_rerun(scratch_database):
    from tests.conftest import schema_files

    files = schema_files()
    assert files[2].name == "003_connections_and_pipeline_details.sql"

    with scratch_database.begin() as conn:  # the database as it was before 1c, with a pipeline already in it
        for f in files[:2]:
            conn.exec_driver_sql(f.read_text(encoding="utf-8"))
        conn.execute(text("INSERT INTO orgs (name) VALUES ('Old Co')"))
        conn.execute(text("INSERT INTO pipelines (org_id, job_name, catalog, target_schema) SELECT id, 'old_job', 'main', 'bronze' FROM orgs"))

    for _ in range(2):  # apply, then apply AGAIN — must be harmless
        with scratch_database.begin() as conn:
            conn.exec_driver_sql(files[2].read_text(encoding="utf-8"))

    with scratch_database.connect() as conn:
        row = conn.execute(text("SELECT job_name, job_parameters, cluster_id, notebook_sha256 FROM pipelines")).one()
        assert tuple(row) == ("old_job", {}, None, None)  # existing pipeline survived, new columns defaulted
        assert conn.execute(text("SELECT to_regclass('org_connections')")).scalar_one() is not None
        conn.execute(text("DELETE FROM orgs"))  # cascade reaches the new table too
