import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.models import Pipeline, PipelineSource, SourceRegistry
from app.schemas import ColumnIn
from app.services.source_registry import compute_schema_hash, register_source_version
from tests.conftest import cols, source_body


def C(name, sql_type, **kw) -> ColumnIn:
    return ColumnIn(name=name, sql_type=sql_type, **kw)


# --- what counts as schema drift (pure logic, no database) -------------------------

BASE = [C("id", "BIGINT"), C("amount", "DECIMAL(15,2)"), C("name", "STRING")]


def test_hash_ignores_nullability_and_display_labels():
    noisy = [
        C("id", "BIGINT", nullable=False, spark_type="LongType", pandas_dtype="int64"),
        C("amount", "DECIMAL(15,2)", nullable=True, pandas_dtype="float64"),
        C("name", "STRING", nullable=False, pandas_dtype="str"),
    ]
    assert compute_schema_hash(noisy) == compute_schema_hash(BASE)


def test_hash_ignores_type_case_and_whitespace():
    assert compute_schema_hash([C("amount", "decimal(15, 2)")]) == compute_schema_hash([C("amount", "DECIMAL(15,2)")])


@pytest.mark.parametrize(
    "changed",
    [
        [C("id", "INT"), C("amount", "DECIMAL(15,2)"), C("name", "STRING")],  # retyped
        [C("id", "BIGINT"), C("amount", "DECIMAL(18,2)"), C("name", "STRING")],  # precision changed
        [C("id", "BIGINT"), C("amount", "DECIMAL(15,2)"), C("full_name", "STRING")],  # renamed
        [C("id", "BIGINT"), C("name", "STRING"), C("amount", "DECIMAL(15,2)")],  # reordered
        BASE + [C("extra", "STRING")],  # column added
        BASE[:2],  # column removed
        [C("id", "BIGINT"), C("amount", "DECIMAL(15,2)"), C("name", "STRING", struct_path=["A", "NAME"])],
    ],
)
def test_hash_changes_when_the_shape_changes(changed):
    assert compute_schema_hash(changed) != compute_schema_hash(BASE)


# --- versioning through the API ----------------------------------------------------

def post(client, acct, **kw):
    return client.post("/sources", json=source_body(**kw), headers=acct.headers)


def history(client, acct, source_id):
    return client.get(f"/sources/{source_id}/history", headers=acct.headers).json()


def test_first_registration_creates_version_1(client, make_account):
    acct = make_account("alice@acme-corp.io")
    r = post(client, acct)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "created" and body["previous_version"] is None
    assert body["source"]["version"] == 1 and body["source"]["record_is_active"] is True
    assert body["source"]["record_end_ts"] is None


def test_same_schema_again_is_unchanged_and_only_reverifies(client, make_account):
    acct = make_account("alice@acme-corp.io")
    first = post(client, acct).json()["source"]
    r = post(client, acct)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unchanged" and body["source"]["id"] == first["id"]
    assert body["source"]["last_verified_at"] > first["last_verified_at"]
    assert len(history(client, acct, first["id"])) == 1


def test_a_nullability_only_difference_is_not_drift(client, make_account):
    acct = make_account("alice@acme-corp.io")
    post(client, acct, columns=cols(("id", "BIGINT"), ("name", "STRING"), nullable=False))
    r = post(client, acct, columns=cols(("id", "BIGINT"), ("name", "STRING"), nullable=True))
    assert r.json()["status"] == "unchanged"


def test_a_changed_schema_closes_the_old_version_and_opens_the_next(client, make_account):
    acct = make_account("alice@acme-corp.io")
    v1 = post(client, acct).json()["source"]
    r = post(client, acct, columns=cols(("id", "BIGINT"), ("name", "STRING"), ("email", "STRING")))
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "drifted" and body["previous_version"] == 1 and body["source"]["version"] == 2

    versions = history(client, acct, v1["id"])  # via the OLD version's id
    assert [v["version"] for v in versions] == [2, 1]  # newest first
    assert [v["record_is_active"] for v in versions] == [True, False]
    assert versions[1]["record_end_ts"] is not None and versions[0]["record_end_ts"] is None
    assert [v["schema_hash"] for v in versions][0] != [v["schema_hash"] for v in versions][1]

    current = client.get("/sources", headers=acct.headers).json()
    assert [(s["version"]) for s in current] == [2]  # the list shows only the active version


def test_later_registrations_compare_against_the_newest_version_and_a_revert_is_a_new_version(client, make_account):
    acct = make_account("alice@acme-corp.io")
    v1_cols = cols(("id", "BIGINT"), ("name", "STRING"))
    v2_cols = cols(("id", "BIGINT"), ("name", "STRING"), ("email", "STRING"))
    src = post(client, acct, columns=v1_cols).json()["source"]
    assert post(client, acct, columns=v2_cols).json()["status"] == "drifted"
    assert post(client, acct, columns=v2_cols).json()["status"] == "unchanged"  # vs v2, not v1
    r = post(client, acct, columns=v1_cols)  # back to the original shape
    assert r.json()["status"] == "drifted" and r.json()["source"]["version"] == 3
    assert [v["version"] for v in history(client, acct, src["id"])] == [3, 2, 1]


def test_copybook_or_xsd_path_is_part_of_a_sources_identity(client, make_account):
    acct = make_account("alice@acme-corp.io")
    data = "/Volumes/main/landing/CUSTOMER.DAT"
    a = post(client, acct, source_type="cobol", source_path=data, schema_definition_path="/schemas/CUSTOMER_V1.CPY")
    b = post(client, acct, source_type="cobol", source_path=data, schema_definition_path="/schemas/CUSTOMER_V2.CPY")
    assert a.json()["source"]["id"] != b.json()["source"]["id"]
    assert b.json()["status"] == "created"  # a different copybook is a different source, not drift
    assert len(client.get("/sources", headers=acct.headers).json()) == 2


def test_blank_and_missing_definition_path_are_the_same_source(client, make_account):
    acct = make_account("alice@acme-corp.io")
    a = post(client, acct)
    b = post(client, acct, schema_definition_path=None)
    assert a.json()["source"]["id"] == b.json()["source"]["id"] and b.json()["status"] == "unchanged"


def test_cobol_struct_paths_and_discovery_config_round_trip(client, make_account):
    acct = make_account("alice@acme-corp.io")
    columns = [
        {"name": "CUST_ID", "sql_type": "BIGINT", "nullable": False, "struct_path": ["CUST-ID"]},
        {"name": "CUST_NAME_FIRST_NAME", "sql_type": "STRING", "struct_path": ["CUST-NAME", "FIRST-NAME"]},
    ]
    r = post(
        client,
        acct,
        source_type="cobol",
        source_path="/x.dat",
        schema_definition_path="/x.cpy",
        columns=columns,
        discovery_config={"is_text": True, "is_record_sequence": False},
    )
    src = client.get(f"/sources/{r.json()['source']['id']}", headers=acct.headers).json()
    assert src["discovery_config"] == {"is_text": True, "is_record_sequence": False}
    assert src["columns"][1]["struct_path"] == ["CUST-NAME", "FIRST-NAME"]
    assert src["columns"][0]["nullable"] is False


def test_list_can_filter_by_source_type(client, make_account):
    acct = make_account("alice@acme-corp.io")
    post(client, acct, source_type="csv", source_path="/a.csv")
    post(client, acct, source_type="json", source_path="/b.json")
    assert [s["source_type"] for s in client.get("/sources?source_type=json", headers=acct.headers).json()] == ["json"]


@pytest.mark.parametrize(
    "bad",
    [
        {"columns": []},
        {"source_type": "CSV"},
        {"source_type": "csv; DROP TABLE users"},
        {"source_path": "   "},
        {"columns": [{"name": "id"}]},
        {"columns": [{"name": "", "sql_type": "BIGINT"}]},
    ],
)
def test_invalid_registrations_are_rejected(client, make_account, bad):
    acct = make_account("alice@acme-corp.io")
    assert client.post("/sources", json={**source_body(), **bad}, headers=acct.headers).status_code == 422


def test_drift_flags_pipelines_built_from_a_superseded_version_until_they_are_relinked(
    client, make_account, session_factory
):
    acct = make_account("alice@acme-corp.io")
    v1 = post(client, acct).json()["source"]
    with session_factory() as s:
        pipeline = Pipeline(org_id=uuid.UUID(acct.org_id), job_name="customers-ingest", catalog="c", target_schema="s")
        s.add(pipeline)
        s.flush()
        s.add(PipelineSource(pipeline_id=pipeline.id, source_registry_id=uuid.UUID(v1["id"]), table_name="customers"))
        s.commit()
        pipeline_id = pipeline.id
    assert client.get("/drift", headers=acct.headers).json() == []

    v2 = post(client, acct, columns=cols(("id", "BIGINT"), ("email", "STRING"))).json()["source"]
    flagged = client.get("/drift", headers=acct.headers).json()
    assert len(flagged) == 1
    assert flagged[0]["job_name"] == "customers-ingest" and flagged[0]["table_name"] == "customers"
    assert flagged[0]["built_from_version"] == 1 and flagged[0]["current_version"] == 2
    assert flagged[0]["built_from_source_id"] == v1["id"] and flagged[0]["current_source_id"] == v2["id"]

    with session_factory() as s:  # regenerating the pipeline links it to the current version
        s.delete(s.get(PipelineSource, (pipeline_id, uuid.UUID(v1["id"]))))
        s.flush()  # SQLAlchemy runs INSERTs before DELETEs in one flush; (pipeline_id, table_name) is unique
        s.add(PipelineSource(pipeline_id=pipeline_id, source_registry_id=uuid.UUID(v2["id"]), table_name="customers"))
        s.commit()
    assert client.get("/drift", headers=acct.headers).json() == []


# --- concurrency -------------------------------------------------------------------

def _register_in_thread(session_factory, acct, columns):
    with session_factory() as s:
        result = register_source_version(
            s,
            org_id=uuid.UUID(acct.org_id),
            user_id=uuid.UUID(acct.user_id),
            source_type="csv",
            source_path="/race.csv",
            schema_definition_path=None,
            discovery_config={},
            columns=columns,
        )
        s.commit()
        return result.status


def _rows(session_factory):
    with session_factory() as s:
        return s.execute(
            select(SourceRegistry.version, SourceRegistry.record_is_active).order_by(SourceRegistry.version)
        ).all()


def test_simultaneous_first_registrations_create_exactly_one_version(client, make_account, session_factory):
    acct = make_account("alice@acme-corp.io")
    columns = [C("id", "BIGINT"), C("name", "STRING")]
    with ThreadPoolExecutor(8) as pool:
        statuses = list(pool.map(lambda _: _register_in_thread(session_factory, acct, columns), range(8)))
    assert statuses.count("created") == 1 and statuses.count("unchanged") == 7
    assert _rows(session_factory) == [(1, True)]


def test_simultaneous_drift_registrations_create_exactly_one_new_version(client, make_account, session_factory):
    acct = make_account("alice@acme-corp.io")
    _register_in_thread(session_factory, acct, [C("id", "BIGINT")])
    changed = [C("id", "BIGINT"), C("email", "STRING")]
    with ThreadPoolExecutor(6) as pool:
        statuses = list(pool.map(lambda _: _register_in_thread(session_factory, acct, changed), range(6)))
    assert statuses.count("drifted") == 1 and statuses.count("unchanged") == 5
    assert _rows(session_factory) == [(1, False), (2, True)]
