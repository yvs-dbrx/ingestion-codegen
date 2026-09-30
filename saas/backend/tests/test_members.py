"""Team membership: invitations, the 5-member cap, removing members, and the 002 migration."""
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from app.security import hash_password
from app.services import membership
from tests.conftest import PASSWORD, schema_files


def invite(client, admin, email, role="member"):
    return client.post("/org/invites", json={"email": email, "role": role}, headers=admin.headers)


def accept_new(client, token, password=PASSWORD, **extra):
    return client.post("/auth/accept-invite", json={"token": token, "password": password, **extra})


def usage(client, acct):
    return client.get("/org/usage", headers=acct.headers).json()


@pytest.fixture()
def set_member_limit(test_engine):
    """Change the cap for one test, then put it back (tiers is shared reference data)."""

    def _set(n: int) -> None:
        with test_engine.begin() as conn:
            conn.execute(text("UPDATE tiers SET member_limit = :n"), {"n": n})

    yield _set
    _set(5)


# --- inviting and joining ------------------------------------------------------------

def test_token_is_shown_once_and_only_its_hash_is_stored(client, make_account, session_factory):
    admin = make_account("admin@acme-corp.io")
    r = invite(client, admin, "New.Person@Acme-Corp.io")
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "new.person@acme-corp.io" and body["role"] == "member" and len(body["token"]) >= 40

    listed = client.get("/org/invites", headers=admin.headers).json()
    assert [i["email"] for i in listed] == ["new.person@acme-corp.io"]
    assert "token" not in listed[0]

    with session_factory() as s:
        stored = s.execute(text("SELECT token_hash FROM org_invites")).scalar_one()
    assert len(stored) == 64 and stored != body["token"] and body["token"] not in stored


def test_only_admins_manage_invitations_but_any_member_can_see_the_team(client, make_account, join_org):
    admin = make_account("admin@acme-corp.io")
    member = join_org(admin, "m@acme-corp.io")
    assert invite(client, member, "x@acme-corp.io").status_code == 403
    assert client.get("/org/invites", headers=member.headers).status_code == 403
    pending = invite(client, admin, "y@acme-corp.io").json()
    assert client.delete(f"/org/invites/{pending['id']}", headers=member.headers).status_code == 403

    team = client.get("/org/members", headers=member.headers).json()
    assert sorted(m["email"] for m in team) == ["admin@acme-corp.io", "m@acme-corp.io"]


def test_accepting_joins_the_inviting_org_with_the_invited_role_and_makes_no_new_org(client, make_account, join_org):
    admin = make_account("admin@acme-corp.io", "Acme")
    colleague = join_org(admin, "colleague@acme-corp.io", role="admin")

    me = client.get("/me", headers=colleague.headers).json()
    assert [(m["org"]["id"], m["role"]) for m in me["memberships"]] == [(admin.org_id, "admin")]
    team = {m["email"]: m["role"] for m in client.get("/org/members", headers=admin.headers).json()}
    assert team == {"admin@acme-corp.io": "admin", "colleague@acme-corp.io": "admin"}
    assert (usage(client, admin)["member_count"], usage(client, admin)["pending_invites"]) == (2, 0)


def test_the_email_comes_from_the_invitation_not_the_request(client, make_account):
    admin = make_account("admin@acme-corp.io")
    token = invite(client, admin, "real@acme-corp.io").json()["token"]
    r = accept_new(client, token, email="attacker@evil.io")
    assert r.status_code == 201 and r.json()["email"] == "real@acme-corp.io"


def test_a_weak_password_is_rejected_without_using_up_the_invitation(client, make_account):
    admin = make_account("admin@acme-corp.io")
    token = invite(client, admin, "x@acme-corp.io").json()["token"]
    assert accept_new(client, token, password="short").status_code == 422
    assert accept_new(client, token).status_code == 201


def test_unknown_expired_revoked_and_used_tokens_all_get_the_same_answer(client, make_account, session_factory):
    admin = make_account("admin@acme-corp.io")

    def fresh(email):
        return invite(client, admin, email).json()

    expired = fresh("expired@acme-corp.io")
    with session_factory() as s:
        s.execute(text("UPDATE org_invites SET expires_at = now() - interval '1 minute'"))
        s.commit()
    revoked = fresh("revoked@acme-corp.io")
    assert client.delete(f"/org/invites/{revoked['id']}", headers=admin.headers).status_code == 204
    used = fresh("used@acme-corp.io")
    assert accept_new(client, used["token"]).status_code == 201

    answers = [
        accept_new(client, t)
        for t in ("x" * 43, expired["token"], revoked["token"], used["token"])  # unknown, expired, revoked, used
    ]
    assert {a.status_code for a in answers} == {400}
    assert len({a.text for a in answers}) == 1


# --- the 5-member cap ----------------------------------------------------------------

def test_an_org_is_limited_to_five_members(client, make_account, join_org):
    admin = make_account("admin@acme-corp.io")
    for i in range(4):
        join_org(admin, f"m{i}@acme-corp.io")
    u = usage(client, admin)
    assert (u["member_count"], u["member_limit"]) == (5, 5)

    r = invite(client, admin, "sixth@acme-corp.io")
    assert r.status_code == 409 and "limit of 5" in r.json()["detail"]


def test_pending_invitations_hold_seats_until_revoked(client, make_account, join_org):
    admin = make_account("admin@acme-corp.io")
    join_org(admin, "m1@acme-corp.io")
    join_org(admin, "m2@acme-corp.io")  # 3 members
    first = invite(client, admin, "a1@acme-corp.io").json()
    assert invite(client, admin, "a2@acme-corp.io").status_code == 201  # 3 + 2 pending = 5 seats used
    u = usage(client, admin)
    assert (u["member_count"], u["pending_invites"]) == (3, 2)

    assert invite(client, admin, "a3@acme-corp.io").status_code == 409
    assert client.delete(f"/org/invites/{first['id']}", headers=admin.headers).status_code == 204
    assert invite(client, admin, "a3@acme-corp.io").status_code == 201


def test_reinviting_an_address_replaces_the_old_invitation_and_takes_no_extra_seat(
    client, make_account, set_member_limit
):
    set_member_limit(2)  # admin + one pending invitation = full
    admin = make_account("admin@acme-corp.io")
    first = invite(client, admin, "x@acme-corp.io").json()
    assert invite(client, admin, "someone-else@acme-corp.io").status_code == 409  # genuinely full

    second = invite(client, admin, "x@acme-corp.io")  # same address: re-issue, not a second seat
    assert second.status_code == 201
    assert usage(client, admin)["pending_invites"] == 1
    assert accept_new(client, first["token"]).status_code == 400  # the old link is dead
    assert accept_new(client, second.json()["token"]).status_code == 201


def test_inviting_someone_who_is_already_a_member_is_rejected(client, make_account, join_org):
    admin = make_account("admin@acme-corp.io")
    join_org(admin, "m@acme-corp.io")
    assert invite(client, admin, "M@Acme-Corp.io").status_code == 409


def test_the_cap_is_checked_again_at_acceptance_and_a_freed_seat_lets_a_waiting_invite_through(
    client, make_account, set_member_limit
):
    set_member_limit(4)
    admin = make_account("admin@acme-corp.io")
    a = invite(client, admin, "a@acme-corp.io").json()
    b = invite(client, admin, "b@acme-corp.io").json()
    set_member_limit(2)  # lowered after both were issued: room for exactly one more member

    joined = accept_new(client, a["token"])
    assert joined.status_code == 201
    full = accept_new(client, b["token"])
    assert full.status_code == 409 and "limit of 2" in full.json()["detail"]

    # An admin frees a seat; the same invitation now works — it was never consumed.
    assert client.delete(f"/org/members/{joined.json()['user_id']}", headers=admin.headers).status_code == 204
    assert accept_new(client, b["token"]).status_code == 201


def test_simultaneous_acceptances_cannot_exceed_the_limit(client, make_account, session_factory, set_member_limit):
    set_member_limit(6)
    admin = make_account("admin@acme-corp.io")
    tokens = [invite(client, admin, f"p{i}@acme-corp.io").json()["token"] for i in range(4)]
    set_member_limit(3)  # admin + room for exactly two more
    password_hash = hash_password(PASSWORD)

    def accept(token):
        with session_factory() as s:
            try:
                membership.accept_invite_new_account(s, token=token, password_hash=password_hash)
                s.commit()
                return "joined"
            except membership.MemberLimitReached:
                s.rollback()
                return "full"

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(accept, tokens))
    assert sorted(results) == ["full", "full", "joined", "joined"]
    with session_factory() as s:
        assert s.execute(text("SELECT count(*) FROM org_members")).scalar_one() == 3


# --- people who already have an account ----------------------------------------------

def test_an_existing_account_can_accept_an_invitation_into_a_second_org(client, make_account):
    admin = make_account("admin@acme-corp.io", "Acme")
    carol = make_account("carol@other-corp.io", "Carol Co")
    token = invite(client, admin, "Carol@Other-Corp.io").json()["token"]

    r = client.post("/invites/accept", json={"token": token}, headers=carol.headers)
    assert r.status_code == 200 and r.json()["org"]["id"] == admin.org_id and r.json()["role"] == "member"
    assert len(client.get("/me", headers=carol.headers).json()["memberships"]) == 2
    # Now in two orgs, she has to say which one she means.
    assert client.get("/sources", headers=carol.headers).status_code == 400
    assert client.get("/sources", headers={**carol.headers, "X-Org-Id": admin.org_id}).status_code == 200


def test_an_invitation_cannot_be_accepted_from_a_different_account(client, make_account):
    admin = make_account("admin@acme-corp.io")
    carol = make_account("carol@other-corp.io", "Carol Co")
    dave = make_account("dave@third-corp.io", "Dave Co")
    token = invite(client, admin, "carol@other-corp.io").json()["token"]

    assert client.post("/invites/accept", json={"token": token}, headers=dave.headers).status_code == 400
    assert client.post("/invites/accept", json={"token": token}, headers=carol.headers).status_code == 200


def test_the_new_account_route_refuses_an_email_that_already_has_an_account_and_keeps_the_invite(client, make_account):
    admin = make_account("admin@acme-corp.io")
    carol = make_account("carol@other-corp.io", "Carol Co")
    token = invite(client, admin, "carol@other-corp.io").json()["token"]

    r = accept_new(client, token)
    assert r.status_code == 409 and "log in" in r.json()["detail"]
    assert client.post("/invites/accept", json={"token": token}, headers=carol.headers).status_code == 200


def test_accepting_as_an_existing_account_requires_login(client, make_account):
    admin = make_account("admin@acme-corp.io")
    token = invite(client, admin, "carol@other-corp.io").json()["token"]
    assert client.post("/invites/accept", json={"token": token}).status_code == 401


# --- removing members ----------------------------------------------------------------

def test_removing_a_member_frees_a_seat_and_ends_their_access(client, make_account, join_org):
    admin = make_account("admin@acme-corp.io")
    members = [join_org(admin, f"m{i}@acme-corp.io") for i in range(4)]
    assert invite(client, admin, "extra@acme-corp.io").status_code == 409  # full

    gone = members[0]
    assert client.delete(f"/org/members/{gone.user_id}", headers=admin.headers).status_code == 204
    assert usage(client, admin)["member_count"] == 4
    assert invite(client, admin, "extra@acme-corp.io").status_code == 201

    assert client.get("/me", headers=gone.headers).json()["memberships"] == []
    assert client.get("/sources", headers=gone.headers).status_code == 403  # belongs to no org any more


def test_a_member_can_leave_but_cannot_remove_others(client, make_account, join_org):
    admin = make_account("admin@acme-corp.io")
    m1 = join_org(admin, "m1@acme-corp.io")
    m2 = join_org(admin, "m2@acme-corp.io")
    assert client.delete(f"/org/members/{m2.user_id}", headers=m1.headers).status_code == 403
    assert client.delete(f"/org/members/{m1.user_id}", headers=m1.headers).status_code == 204


def test_the_last_admin_cannot_be_removed(client, make_account, join_org):
    admin = make_account("admin@acme-corp.io")
    join_org(admin, "m@acme-corp.io")  # a plain member doesn't count as a second admin
    r = client.delete(f"/org/members/{admin.user_id}", headers=admin.headers)
    assert r.status_code == 409 and "last admin" in r.json()["detail"]

    join_org(admin, "second-admin@acme-corp.io", role="admin")
    assert client.delete(f"/org/members/{admin.user_id}", headers=admin.headers).status_code == 204


def test_you_cannot_remove_someone_who_is_not_in_your_org(client, make_account):
    alice = make_account("alice@acme-corp.io", "Alice Co")
    bob = make_account("bob@other-corp.io", "Bob Co")
    assert client.delete(f"/org/members/{bob.user_id}", headers=alice.headers).status_code == 404
    assert client.get("/me", headers=bob.headers).json()["memberships"] != []


def test_invitations_are_private_to_the_org_that_sent_them(client, make_account):
    alice = make_account("alice@acme-corp.io", "Alice Co")
    bob = make_account("bob@other-corp.io", "Bob Co")
    theirs = invite(client, bob, "guest@other-corp.io").json()

    assert client.get("/org/invites", headers=alice.headers).json() == []
    assert client.delete(f"/org/invites/{theirs['id']}", headers=alice.headers).status_code == 404
    assert client.delete(f"/org/invites/{uuid.uuid4()}", headers=alice.headers).status_code == 404
    assert len(client.get("/org/invites", headers=bob.headers).json()) == 1  # untouched


# --- the schema change itself --------------------------------------------------------

def test_migration_002_upgrades_an_existing_database_in_place_and_can_be_rerun(scratch_database):
    first, second = schema_files()[:2]
    assert (first.name, second.name) == ("001_schema.sql", "002_org_members_and_invites.sql")

    with scratch_database.begin() as conn:  # a database exactly as it was BEFORE this change, with data in it
        conn.exec_driver_sql(first.read_text(encoding="utf-8"))
        conn.execute(text("INSERT INTO orgs (name, tier) VALUES ('Old Co', 'team')"))
        conn.execute(text("INSERT INTO users (email, password_hash) VALUES ('old@x.io', 'h')"))

    for _ in range(2):  # apply it, then apply it AGAIN — must be harmless
        with scratch_database.begin() as conn:
            conn.exec_driver_sql(second.read_text(encoding="utf-8"))

    with scratch_database.connect() as conn:
        assert dict(conn.execute(text("SELECT name, member_limit FROM tiers")).all()) == {"free": 5, "team": 5, "pro": 5}
        assert conn.execute(text("SELECT count(*) FROM orgs")).scalar_one() == 1  # existing data survived
        assert conn.execute(text("SELECT count(*) FROM users")).scalar_one() == 1
        assert conn.execute(text("SELECT to_regclass('org_invites')")).scalar_one() is not None
