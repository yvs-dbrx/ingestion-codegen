"""One organization must never be able to read or affect another's data."""
import uuid

from app.models import OrgMember, Pipeline, PipelineSource
from tests.conftest import cols, source_body


def register_source(client, acct, **kw):
    r = client.post("/sources", json=source_body(**kw), headers=acct.headers)
    assert r.status_code == 201, r.text
    return r.json()["source"]


def test_an_org_cannot_see_or_fetch_another_orgs_sources(client, make_account):
    alice = make_account("alice@acme-corp.io", "Alice Co")
    bob = make_account("bob@other-corp.io", "Bob Co")
    src = register_source(client, alice)

    assert [s["id"] for s in client.get("/sources", headers=alice.headers).json()] == [src["id"]]
    assert client.get("/sources", headers=bob.headers).json() == []

    # Same 404 as a source that doesn't exist at all — no confirmation the id is real.
    for path in (f"/sources/{src['id']}", f"/sources/{src['id']}/history"):
        assert client.get(path, headers=bob.headers).status_code == 404
        assert client.get(path.replace(src["id"], str(uuid.uuid4())), headers=bob.headers).status_code == 404


def test_the_same_path_in_two_orgs_is_two_independent_sources(client, make_account):
    alice = make_account("alice@acme-corp.io", "Alice Co")
    bob = make_account("bob@other-corp.io", "Bob Co")
    a = register_source(client, alice)
    b = register_source(client, bob)
    assert a["id"] != b["id"] and a["version"] == b["version"] == 1

    # Bob's schema drifting must not touch Alice's history.
    r = client.post(
        "/sources", json=source_body(columns=cols(("id", "BIGINT"), ("email", "STRING"))), headers=bob.headers
    )
    assert r.json()["status"] == "drifted"
    assert client.get(f"/sources/{a['id']}", headers=alice.headers).json()["version"] == 1
    assert len(client.get(f"/sources/{a['id']}/history", headers=alice.headers).json()) == 1


def test_x_org_id_for_an_org_you_do_not_belong_to_is_404(client, make_account):
    alice = make_account("alice@acme-corp.io", "Alice Co")
    bob = make_account("bob@other-corp.io", "Bob Co")
    register_source(client, bob)

    for path in ("/sources", "/org/usage", "/drift"):
        stolen = client.get(path, headers=alice.in_org(bob.org_id))
        made_up = client.get(path, headers=alice.in_org(str(uuid.uuid4())))
        assert stolen.status_code == made_up.status_code == 404, path
        assert stolen.json() == made_up.json(), path


def test_an_org_id_in_the_request_body_cannot_redirect_a_write(client, make_account):
    alice = make_account("alice@acme-corp.io", "Alice Co")
    bob = make_account("bob@other-corp.io", "Bob Co")
    r = client.post("/sources", json={**source_body(), "org_id": bob.org_id}, headers=alice.headers)
    assert r.status_code == 201
    assert len(client.get("/sources", headers=alice.headers).json()) == 1
    assert client.get("/sources", headers=bob.headers).json() == []


def test_a_user_in_two_orgs_must_choose_and_only_sees_the_chosen_one(client, make_account, session_factory):
    alice = make_account("alice@acme-corp.io", "Alice Co")
    bob = make_account("bob@other-corp.io", "Bob Co")
    register_source(client, alice, source_path="/alice.csv")
    register_source(client, bob, source_path="/bob.csv")
    with session_factory() as s:  # invite Alice into Bob's org
        s.add(OrgMember(org_id=uuid.UUID(bob.org_id), user_id=uuid.UUID(alice.user_id), role="member"))
        s.commit()

    assert client.get("/sources", headers=alice.headers).status_code == 400  # ambiguous without X-Org-Id

    own = client.get("/sources", headers=alice.in_org(alice.org_id)).json()
    other = client.get("/sources", headers=alice.in_org(bob.org_id)).json()
    assert [s["source_path"] for s in own] == ["/alice.csv"]
    assert [s["source_path"] for s in other] == ["/bob.csv"]

    # Bob, meanwhile, is still not in Alice's org.
    assert client.get("/sources", headers=bob.in_org(alice.org_id)).status_code == 404


def test_usage_and_drift_are_scoped_to_the_org(client, make_account, session_factory):
    alice = make_account("alice@acme-corp.io", "Alice Co")
    bob = make_account("bob@other-corp.io", "Bob Co")
    src = register_source(client, alice)
    with session_factory() as s:
        p = Pipeline(org_id=uuid.UUID(alice.org_id), job_name="j", catalog="c", target_schema="s")
        s.add(p)
        s.flush()
        s.add(PipelineSource(pipeline_id=p.id, source_registry_id=uuid.UUID(src["id"]), table_name="t"))
        s.commit()
    client.post("/sources", json=source_body(columns=cols(("id", "BIGINT"), ("x", "STRING"))), headers=alice.headers)

    assert client.get("/org/usage", headers=alice.headers).json()["pipeline_count"] == 1
    assert client.get("/org/usage", headers=bob.headers).json()["pipeline_count"] == 0
    assert len(client.get("/drift", headers=alice.headers).json()) == 1
    assert client.get("/drift", headers=bob.headers).json() == []
