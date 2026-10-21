"""Silver pipelines (integrated entities from a Bronze table), reference/crosswalk tables, and the counts
a Silver run reports.

The real engine code renders the notebook and the real deploy flow runs; only Databricks is faked.
That the notebook computes the right thing is checked by running it on real Spark:
tests/spark_e2e/run_e2e.py (scenario 5).
"""
import json
import uuid

import pytest
from sqlalchemy import func, select, text

from app.models import AssetLineage, DataAsset, Pipeline
from tests.conftest import cols
from tests.test_bronze import assets_in, bdeploy, bspec, finished_run, raw_asset, report
from tests.test_pipelines import acct, deploy, register, spec  # noqa: F401  (acct is a fixture)
from tests.test_runs import refresh

XWALK = "main.ref.xwalk"
XWALK_COLUMNS = [("src_sys", "string"), ("src_key", "string"), ("mk", "string"), ("etype", "string")]


def sspec(source_asset_id, entities=None, job_name="customers_silver", **over):
    return {
        "job_name": job_name,
        "catalog": "main",
        "target_schema": "silver",
        "source_asset_id": source_asset_id,
        "source_system": "CRM",
        "entities": entities if entities is not None else [customer()],
        "schedule": {"frequency": "daily", "time": "04:00", "timezone": "UTC"},
        **over,
    }


def attr(name, source=None, *steps, required=False):
    return {"name": name, "source_column": source or name, "steps": list(steps), "required": required}


def customer(master=None, **over):
    body = {
        "table_name": "customer",
        "business_key": ["id"],
        "attributes": [
            attr("id", required=True),
            attr("full_name", "name", {"kind": "trim"}, {"kind": "null_if", "values": [""]}, {"kind": "upper"}, required=True),
            attr("birth_date", "birth", {"kind": "null_if", "values": ["0"]}, {"kind": "parse_date", "format": "yyyyMMdd"}),
        ],
        **over,
    }
    if master:
        body["master_key"] = master
    return body


def master(crosswalk_id, **over):
    return {"column": "customer_mk", "crosswalk_asset_id": crosswalk_id,
            "system_column": "src_sys", "key_column": "src_key", "master_column": "mk", **over}


def spreview(client, acct, body):  # noqa: F811
    return client.post("/silver/preview", json=body, headers=acct.headers)


def sdeploy(client, acct, body, **extra):  # noqa: F811
    p = spreview(client, acct, body)
    assert p.status_code == 200, p.text
    return client.post("/silver", json={**body, "notebook_sha256": p.json()["notebook_sha256"], **extra}, headers=acct.headers)


@pytest.fixture()
def bronze(client, acct, workspace):  # noqa: F811
    """An org with a Raw and a Bronze pipeline; returns the Bronze asset main.bronze.customers."""
    sid = register(client, acct, columns=cols(("id", "BIGINT"), ("name", "STRING"), ("birth", "INT"), ("status", "STRING")))
    deploy(client, acct, spec([sid], target_schema="raw"))
    assert bdeploy(client, acct, bspec([raw_asset(client, acct)["id"]])).status_code == 201
    (b,) = assets_in(client, acct, "bronze")
    return b


@pytest.fixture()
def xwalk(client, acct, workspace):  # noqa: F811
    workspace.tables_by_name[XWALK] = XWALK_COLUMNS
    r = client.post("/assets/reference", json={"catalog": "main", "schema_name": "ref", "table_name": "xwalk"}, headers=acct.headers)
    assert r.status_code == 201, r.text
    return r.json()


# --- reference tables ----------------------------------------------------------------------

def test_a_reference_table_is_registered_with_columns_read_from_the_workspace(client, acct, workspace, xwalk):  # noqa: F811
    assert (xwalk["layer"], xwalk["full_name"], xwalk["produced_by"]) == ("reference", XWALK, None)
    assert xwalk["columns"] == [{"name": n, "sql_type": "STRING"} for n, _ in XWALK_COLUMNS]  # normalized from 'string'
    assert [a["full_name"] for a in assets_in(client, acct, "reference")] == [XWALK]


def test_registering_again_refreshes_the_columns_instead_of_duplicating(client, acct, workspace, xwalk):  # noqa: F811
    workspace.tables_by_name[XWALK] = XWALK_COLUMNS + [("extra", "decimal(10, 2)")]
    r = client.post("/assets/reference", json={"catalog": "main", "schema_name": "ref", "table_name": "xwalk"}, headers=acct.headers)
    assert r.status_code == 201 and r.json()["id"] == xwalk["id"]
    assert {"name": "extra", "sql_type": "DECIMAL(10,2)"} in r.json()["columns"]
    assert len(assets_in(client, acct, "reference")) == 1


def test_a_table_that_does_not_exist_in_the_workspace_is_a_404(client, acct, workspace):  # noqa: F811
    r = client.post("/assets/reference", json={"catalog": "main", "schema_name": "ref", "table_name": "nope"}, headers=acct.headers)
    assert r.status_code == 404 and "doesn't exist" in r.json()["detail"]


def test_a_table_the_platform_produces_cannot_be_registered_as_reference(client, acct, bronze, workspace):  # noqa: F811
    workspace.tables_by_name[bronze["full_name"]] = [("id", "bigint")]
    ref = {"catalog": "main", "schema_name": "bronze", "table_name": "customers"}
    r = client.post("/assets/reference", json=ref, headers=acct.headers)
    assert r.status_code == 409 and "already registered as a bronze table" in r.json()["detail"]


@pytest.mark.parametrize("bad", ['x"; DROP', "a b", "a.b", "1x", "x" * 129])
def test_unsafe_reference_names_are_refused_before_anything_is_asked_of_databricks(client, acct, workspace, bad):  # noqa: F811
    r = client.post("/assets/reference", json={"catalog": "main", "schema_name": "ref", "table_name": bad}, headers=acct.headers)
    assert r.status_code == 422


def test_reference_registration_needs_a_connected_workspace(client, make_account, workspace):  # noqa: F811
    nobody = make_account("c@c.com", "C")
    r = client.post("/assets/reference", json={"catalog": "main", "schema_name": "ref", "table_name": "xwalk"}, headers=nobody.headers)
    assert r.status_code == 409 and "Connection" in r.json()["detail"]


# --- preview -------------------------------------------------------------------------------

def test_preview_generates_a_notebook_whose_design_is_data_and_changes_nothing(client, acct, bronze, workspace, session_factory):  # noqa: F811
    r = spreview(client, acct, sspec(bronze["id"]))
    assert r.status_code == 200, r.text
    nb = r.json()["notebook"]
    assert "main.bronze.customers_sightings" in nb and "main.silver.customer_quarantine" in nb
    assert "yyyyMMdd" in nb and len(r.json()["notebook_sha256"]) == 64
    assert len(workspace.notebooks) == 2  # the Raw and Bronze notebooks only
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Pipeline)) == 2
        assert s.scalar(select(func.count()).select_from(DataAsset).where(DataAsset.layer == "silver")) == 0


def test_preview_is_deterministic_and_sensitive_to_the_design(client, acct, bronze):  # noqa: F811
    a = spreview(client, acct, sspec(bronze["id"])).json()["notebook_sha256"]
    assert a == spreview(client, acct, sspec(bronze["id"])).json()["notebook_sha256"]
    other = customer()
    other["attributes"][1]["steps"].append({"kind": "lower"})
    assert a != spreview(client, acct, sspec(bronze["id"], [other])).json()["notebook_sha256"]


def test_the_type_of_an_attribute_follows_its_steps(client, acct, bronze, workspace):  # noqa: F811
    entity = customer()
    entity["attributes"] += [attr("status_code", "status", {"kind": "cast", "type": "int"}), attr("raw_status", "status")]
    out = sdeploy(client, acct, sspec(bronze["id"], [entity])).json()
    assert {a["name"]: a["type"] for a in out["spec"]["entities"][0]["attributes"]} == {
        "id": "BIGINT", "full_name": "STRING", "birth_date": "DATE", "status_code": "INT", "raw_status": "STRING"}
    steps = {a["name"]: a["steps"] for a in out["spec"]["entities"][0]["attributes"]}
    assert steps["status_code"] == [{"kind": "cast", "type": "INT"}]  # normalized: what the user typed is not what is stored


# --- validation ----------------------------------------------------------------------------

def test_unknown_and_foreign_tables_are_not_found(client, acct, bronze, xwalk, make_account, workspace):  # noqa: F811
    assert spreview(client, acct, sspec(str(uuid.uuid4()))).status_code == 404
    other = make_account("b@b.com", "B")
    assert spreview(client, other, sspec(bronze["id"])).status_code == 404  # someone else's Bronze table
    assert spreview(client, acct, sspec(bronze["id"], [customer(master(str(uuid.uuid4())))])).status_code == 404
    mine = sspec(bronze["id"], [customer(master(xwalk["id"]))])
    assert spreview(client, other, {**mine}).status_code == 404  # nor can they use my crosswalk


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(catalog='main"; DROP'),
        lambda b: b.update(target_schema="a b"),
        lambda b: b.update(job_name="../../etc"),
        lambda b: b.update(source_system="CRM' OR '1'='1"),
        lambda b: b.update(unknown_master_key="x'); import os #"),
        lambda b: b["entities"][0].update(table_name="x'); import os #"),
        lambda b: b["entities"][0]["attributes"][0].update(name='a"; DROP'),
        lambda b: b["entities"][0]["attributes"][0].update(source_column="id`; DROP"),
        lambda b: b["entities"][0]["attributes"][1]["steps"][1].update(values=["x' OR '1'='1"]),
        lambda b: b["entities"][0]["attributes"][1]["steps"][1].update(values=["back\\slash"]),
        lambda b: b["entities"][0]["attributes"][1]["steps"][1].update(values=["new\nline"]),
        lambda b: b["entities"][0]["attributes"][2]["steps"][1].update(format="yyyy'); DROP"),
        lambda b: b["entities"][0]["attributes"][0]["steps"].append({"kind": "cast", "type": "INT); DROP"}),
        lambda b: b["entities"][0]["attributes"][0]["steps"].append({"kind": "sql", "expr": "1"}),
        lambda b: b["entities"][0]["attributes"][0]["steps"].append({"kind": "trim", "extra": 1}),
        lambda b: b["entities"][0]["attributes"][0]["steps"].append({"kind": "cast"}),
        lambda b: b["entities"][0].update(master_key=master(b["xwalk"], entity_type_column="etype", entity_type="x' OR '1'='1")),
        lambda b: b["entities"][0].update(master_key=master(b["xwalk"], entity_type_column="etype", entity_type="")),
        lambda b: b["entities"][0].update(master_key=master(b["xwalk"], entity_type_column="etype`; DROP", entity_type="customer")),
    ],
)
def test_unsafe_values_are_refused(client, acct, bronze, xwalk, mutate):  # noqa: F811
    body = sspec(bronze["id"])
    body["xwalk"] = xwalk["id"]  # lets a case build a master key; removed before the request is sent
    mutate(body)
    body.pop("xwalk")
    r = spreview(client, acct, body)
    assert r.status_code == 422 and "notebook" not in r.json()


def problems(client, acct, body):  # noqa: F811
    r = spreview(client, acct, body)
    assert r.status_code in (409, 422), r.text
    return r.json().get("problems", []) + [r.json().get("detail") if isinstance(r.json().get("detail"), str) else ""]


def test_design_rules_each_give_a_specific_message(client, acct, bronze, xwalk):  # noqa: F811
    def one(mutate, expect):
        body = sspec(bronze["id"])
        mutate(body)
        found = problems(client, acct, body)
        assert any(expect in p for p in found), (expect, found)

    one(lambda b: b["entities"][0]["attributes"][0].update(source_column="nope"), "no column 'nope'")
    one(lambda b: b["entities"][0].update(business_key=["ghost"]), "isn't one of its attributes")
    one(lambda b: b["entities"][0].update(business_key=["id", "ID"]), "twice")
    one(lambda b: b["entities"][0]["attributes"].append(attr("ID", "status")), "duplicate of 'id'")
    one(lambda b: b["entities"][0]["attributes"].append(attr("_hidden", "status")), "underscore")
    one(lambda b: b["entities"][0]["attributes"][2]["steps"][1].update(format="dd.MM.yyyy"), "isn't one of")
    one(lambda b: b["entities"][0]["attributes"][0]["steps"].append({"kind": "default", "value": "abc"}), "can't be read as BIGINT")
    one(lambda b: b["entities"][0]["attributes"][0]["steps"].append({"kind": "default", "value": ""}), "can't be empty")
    one(lambda b: b["entities"].append(customer()), "unique within a pipeline")
    one(lambda b: b["entities"].append({**customer(), "table_name": "customer_quarantine"}), "quarantine")
    one(lambda b: b["entities"][0]["attributes"].append(attr("score", "status", {"kind": "cast", "type": "double"})) or
        b["entities"][0].update(business_key=["score"]), "can't be a business key")
    one(lambda b: b["entities"][0].update(master_key=master(xwalk["id"], key_column="src_sys")), "must all be different")
    one(lambda b: b["entities"][0].update(master_key=master(xwalk["id"], key_column="missing")), "no key column named 'missing'")
    one(lambda b: b["entities"][0].update(master_key=master(xwalk["id"], entity_type_column="etype")), "both the entity type column and the entity type value")
    one(lambda b: b["entities"][0].update(master_key=master(xwalk["id"], entity_type="customer")), "both the entity type column and the entity type value")
    one(lambda b: b["entities"][0].update(master_key=master(xwalk["id"], entity_type_column="nope", entity_type="customer")), "no entity type column named 'nope'")
    one(lambda b: b["entities"][0].update(master_key=master(xwalk["id"], entity_type_column="src_key", entity_type="customer")), "must all be different")
    one(lambda b: b["entities"][0].update(master_key=master(xwalk["id"], column="id")), "already one of the attributes")
    one(lambda b: b["entities"][0].update(master_key=master(xwalk["id"], column="_mk")), "underscore")
    one(lambda b: b["entities"][0].update(business_key=["id", "full_name"], master_key=master(xwalk["id"])), "exactly one attribute")


def test_source_must_be_a_bronze_table(client, acct, bronze, workspace):  # noqa: F811
    raw = raw_asset(client, acct)
    found = problems(client, acct, sspec(raw["id"]))
    assert any("is a raw table; Silver is built from a Bronze table" in p for p in found)


def test_the_crosswalk_must_be_a_registered_reference_table(client, acct, bronze, workspace):  # noqa: F811
    found = problems(client, acct, sspec(bronze["id"], [customer(master(bronze["id"], system_column="id", key_column="name", master_column="birth"))]))
    assert any("register the crosswalk as a reference table first" in p for p in found)


def test_a_table_without_a_column_list_is_refused(client, acct, bronze, test_engine):  # noqa: F811
    with test_engine.begin() as conn:
        conn.execute(text("UPDATE data_assets SET columns = '[]'::jsonb WHERE id = :i"), {"i": bronze["id"]})
    assert any("no column list" in p for p in problems(client, acct, sspec(bronze["id"])))


def test_hostile_registry_contents_never_reach_the_notebook(client, acct, bronze, xwalk, test_engine):  # noqa: F811
    hostile = [{"name": 'x"); import os; os.system("id")#', "sql_type": "STRING"}]
    with test_engine.begin() as conn:
        conn.execute(text("UPDATE data_assets SET columns = CAST(:c AS jsonb) WHERE id = :i"), {"c": json.dumps(hostile), "i": bronze["id"]})
    r = spreview(client, acct, sspec(bronze["id"]))
    assert r.status_code == 422 and "notebook" not in r.json()

    with test_engine.begin() as conn:  # and a crosswalk whose stored column names were tampered with
        conn.execute(text("UPDATE data_assets SET columns = CAST(:c AS jsonb) WHERE id = :i"),
                     {"c": json.dumps([{"name": "src_sys", "sql_type": "STRING"}, {"name": "src_key", "sql_type": "STRING"},
                                       {"name": 'mk"; DROP', "sql_type": "STRING"}]), "i": xwalk["id"]})
    r = spreview(client, acct, sspec(bronze["id"], [customer(master(xwalk["id"], master_column='mk"; DROP'))]))
    assert r.status_code == 422 and "notebook" not in r.json()


def test_a_target_already_registered_as_another_layer_is_refused(client, acct, bronze, test_engine):  # noqa: F811
    with test_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO data_assets (id, org_id, catalog, schema_name, table_name, layer) "
                 "VALUES (gen_random_uuid(), :o, 'main', 'silver', 'customer', 'gold')"),
            {"o": acct.org_id},
        )
    r = spreview(client, acct, sspec(bronze["id"]))
    assert r.status_code == 409 and "already registered as a gold table" in r.json()["detail"]


def test_a_quarantine_name_already_in_use_is_refused(client, acct, bronze, test_engine):  # noqa: F811
    with test_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO data_assets (id, org_id, catalog, schema_name, table_name, layer) "
                 "VALUES (gen_random_uuid(), :o, 'main', 'silver', 'customer_quarantine', 'silver')"),
            {"o": acct.org_id},
        )
    r = spreview(client, acct, sspec(bronze["id"]))
    assert r.status_code == 409 and "customer_quarantine" in r.json()["detail"]


def test_the_silver_table_cannot_be_its_bronze_source(client, acct, bronze):  # noqa: F811
    found = problems(client, acct, sspec(bronze["id"], target_schema="bronze", entities=[{**customer(), "table_name": "customers"}]))
    assert any("can't be its Bronze source" in p for p in found)


# --- deploy --------------------------------------------------------------------------------

def test_deploy_registers_silver_tables_with_lineage_to_bronze_and_the_crosswalk(client, acct, bronze, xwalk, workspace):  # noqa: F811
    body = sspec(bronze["id"], [customer(master(xwalk["id"])), {
        "table_name": "status_code", "business_key": ["code"], "attributes": [attr("code", "status", {"kind": "trim"}, {"kind": "upper"})]}])
    r = sdeploy(client, acct, body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert (out["layer"], out["mode"], out["tables"], out["job_parameters"]) == ("silver", "multi", [], {})
    assert out["spec"]["source"]["bronze"] == "main.bronze.customers" and out["spec"]["unknown_master_key"] == "UNKNOWN"
    assert out["spec"]["entities"][0]["master_key"]["crosswalk"] == XWALK

    by_name = {a["table_name"]: a for a in assets_in(client, acct, "silver")}
    assert sorted(by_name) == ["customer", "status_code"]
    cust = client.get(f"/assets/{by_name['customer']['id']}", headers=acct.headers).json()
    assert [c["name"] for c in cust["columns"]] == ["id", "full_name", "birth_date", "customer_mk", "_master_key_resolved"]
    assert {u["full_name"] for u in cust["upstream"]} == {"main.bronze.customers", XWALK}
    assert cust["source_system"] == "CRM" and cust["produced_by"]["job_name"] == "customers_silver"
    status = client.get(f"/assets/{by_name['status_code']['id']}", headers=acct.headers).json()
    assert [u["full_name"] for u in status["upstream"]] == ["main.bronze.customers"]  # no crosswalk, no lineage to it

    job = list(workspace.jobs_by_id.values())[-1]
    assert job["name"] == "customers_silver" and "source_path" not in job["tasks"][0].notebook_task.base_parameters
    assert workspace.notebooks[out["workspace_path"]].decode() == out["notebook_source"]


def test_an_entity_type_narrows_the_crosswalk_lookup(client, acct, bronze, xwalk, workspace):  # noqa: F811
    typed = customer(master(xwalk["id"], entity_type_column="ETYPE", entity_type="customer"))
    out = sdeploy(client, acct, sspec(bronze["id"], [typed])).json()
    mk = out["spec"]["entities"][0]["master_key"]
    assert (mk["entity_type_column"], mk["entity_type"]) == ("etype", "customer")  # column name normalized to the crosswalk's own
    nb = out["notebook_source"]
    assert '\\"entity_type\\": \\"customer\\"' in nb and "(entity type `customer`)" in nb
    plain = sdeploy_spec_without_type(client, acct, bronze, xwalk)
    assert "entity_type" not in plain["spec"]["entities"][0]["master_key"]  # optional: absent when not asked for


def sdeploy_spec_without_type(client, acct, bronze, xwalk):  # noqa: F811
    first = client.get("/pipelines", headers=acct.headers).json()
    mine = next(p for p in first if p["layer"] == "silver")
    assert client.delete(f"/pipelines/{mine['id']}", headers=acct.headers).status_code == 204
    return sdeploy(client, acct, sspec(bronze["id"], [customer(master(xwalk["id"]))])).json()


def test_the_notebook_hash_gate_applies(client, acct, bronze):  # noqa: F811
    r = client.post("/silver", json={**sspec(bronze["id"]), "notebook_sha256": "0" * 64}, headers=acct.headers)
    assert r.status_code == 409 and "preview again" in r.json()["detail"]


def test_a_failed_deploy_leaves_no_asset_and_no_lineage(client, acct, bronze, xwalk, workspace, session_factory):  # noqa: F811
    with session_factory() as s:
        before = s.scalar(select(func.count()).select_from(AssetLineage))
    workspace.fail_create = True
    assert sdeploy(client, acct, sspec(bronze["id"], [customer(master(xwalk["id"]))])).status_code == 502
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(AssetLineage)) == before
        assert s.scalar(select(func.count()).select_from(DataAsset).where(DataAsset.layer == "silver")) == 0
        assert s.scalar(select(func.count()).select_from(Pipeline).where(Pipeline.layer == "silver")) == 0


def test_deleting_a_silver_pipeline_keeps_its_tables_and_lineage_and_redeploy_adopts_them(client, acct, bronze, xwalk, workspace):  # noqa: F811
    body = sspec(bronze["id"], [customer(master(xwalk["id"]))])
    first = sdeploy(client, acct, body).json()
    assert client.delete(f"/pipelines/{first['id']}", headers=acct.headers).status_code == 204
    (kept,) = assets_in(client, acct, "silver")
    assert kept["produced_by"] is None and kept["upstream_count"] == 2
    second = sdeploy(client, acct, body).json()
    (again,) = assets_in(client, acct, "silver")
    assert again["id"] == kept["id"] and again["produced_by"]["pipeline_id"] == second["id"] and again["upstream_count"] == 2


def test_silver_pipelines_show_in_the_list_and_count_toward_the_plan(client, acct, bronze, workspace):  # noqa: F811
    assert sdeploy(client, acct, sspec(bronze["id"])).status_code == 201
    layers = {p["job_name"]: p["layer"] for p in client.get("/pipelines", headers=acct.headers).json()}
    assert layers == {"customers_daily": "raw", "customers_bronze": "bronze", "customers_silver": "silver"}
    r = sdeploy(client, acct, sspec(bronze["id"], [{**customer(), "table_name": "other"}], job_name="more"))
    assert r.status_code == 409 and "free plan allows 3" in r.json()["detail"]


# --- the counts a Silver run reports ---------------------------------------------------------

def test_a_silver_runs_counts_are_stored_and_update_the_registry(client, acct, bronze, workspace):  # noqa: F811
    pipeline = sdeploy(client, acct, sspec(bronze["id"])).json()
    finished_run(client, acct, workspace, pipeline["id"], report(
        "silver", "main.silver.customer", rows_read=900, null_key_records=3, distinct_keys=120, conflicting_keys=2,
        quarantined_keys=5, rows_inserted=100, rows_updated=15, unchanged=0, rows_in_target=100, unmapped_keys=7,
        crosswalk_rows=500, crosswalk_version=12))
    (run,) = refresh(client, acct, pipeline["id"]).json()
    t = run["metrics"]["tables"]["customer"]
    assert run["metrics"]["layer"] == "silver"
    assert (t["null_key_records"], t["distinct_keys"], t["conflicting_keys"], t["quarantined_keys"], t["rows_updated"],
            t["unmapped_keys"], t["crosswalk_rows"], t["crosswalk_version"]) == (3, 120, 2, 5, 15, 7, 500, 12)
    (silver,) = assets_in(client, acct, "silver")
    assert silver["row_count"] == 100 and silver["last_run_at"] is not None


def test_silver_reports_are_sanitized_like_every_other(client, acct, bronze, workspace):  # noqa: F811
    pipeline = sdeploy(client, acct, sspec(bronze["id"])).json()
    finished_run(client, acct, workspace, pipeline["id"], report(
        "silver", "main.silver.customer", distinct_keys=-1, unmapped_keys=1.5, quarantined_keys=True, crosswalk_rows=2 ** 80, rows_updated=4))
    (run,) = refresh(client, acct, pipeline["id"]).json()
    t = run["metrics"]["tables"]["customer"]
    assert t["rows_updated"] == 4 and not {"distinct_keys", "unmapped_keys", "quarantined_keys", "crosswalk_rows"} & set(t)


def test_a_silver_report_cannot_touch_a_table_the_pipeline_does_not_produce(client, acct, bronze, workspace):  # noqa: F811
    pipeline = sdeploy(client, acct, sspec(bronze["id"])).json()
    finished_run(client, acct, workspace, pipeline["id"], report("silver", bronze["full_name"], rows_in_target=999999))
    refresh(client, acct, pipeline["id"])
    assert [a["row_count"] for a in assets_in(client, acct, "bronze")] != [999999]


def test_registration_itself_refuses_a_name_another_layer_took_meanwhile(client, acct, bronze, session_factory):  # noqa: F811
    """build_silver_plan checks this up front; registration re-checks because another request can claim
    the name between the two. Called directly here, as that race would."""
    from app.services import assets as assets_svc
    from app.services.errors import Conflict

    entity = {"name": "customer", "attributes": [{"name": "id", "type": "BIGINT"}], "master_key": None}
    with session_factory() as s:
        org_id = s.scalar(select(DataAsset.org_id).limit(1))
        pipeline = s.scalar(select(Pipeline).limit(1))
        source = s.scalar(select(DataAsset).where(DataAsset.layer == "bronze"))
        s.add(DataAsset(org_id=org_id, catalog=pipeline.catalog, schema_name=pipeline.target_schema, table_name="customer", layer="gold"))
        s.flush()
        with pytest.raises(Conflict, match="already registered as a gold table"):
            assets_svc.register_silver_assets(
                s, pipeline=pipeline, bronze=source, spec={"source": {"system": "CRM"}, "entities": [entity]}, references={}
            )
