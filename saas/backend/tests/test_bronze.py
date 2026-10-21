"""Bronze pipelines (distinct records from Raw tables) and the counts runs report back.

The real engine code renders the notebook and the real deploy flow runs; only Databricks is faked.
Whether the generated notebook computes the right thing is checked separately, by executing it on a
real local Spark: tests/spark_e2e/run_e2e.py.
"""
import json
import uuid

import pytest
from sqlalchemy import func, select, text

from app.models import AssetLineage, DataAsset, Pipeline
from tests.conftest import cols
from tests.test_pipelines import FOLDER, acct, deploy, register, spec  # noqa: F401  (acct is a fixture)
from tests.test_runs import refresh, run_now

LINEAGE = ("_ingested_at", "_source_file", "_batch_id")


def assets_in(client, acct, layer):  # noqa: F811
    return client.get("/assets", params={"layer": layer}, headers=acct.headers).json()


def raw_asset(client, acct, name="customers"):  # noqa: F811
    return next(a for a in assets_in(client, acct, "raw") if a["table_name"] == name)


def bspec(asset_ids, names=None, job_name="customers_bronze", **over):
    names = names or [f"b{i}" if len(asset_ids) > 1 else "customers" for i in range(len(asset_ids))]
    return {
        "job_name": job_name,
        "catalog": "main",
        "target_schema": "bronze",
        "inputs": [{"asset_id": a, "table_name": n} for a, n in zip(asset_ids, names)],
        "schedule": {"frequency": "daily", "time": "03:00", "timezone": "UTC"},
        **over,
    }


def bpreview(client, acct, body):  # noqa: F811
    return client.post("/bronze/preview", json=body, headers=acct.headers)


def bdeploy(client, acct, body, **extra):  # noqa: F811
    p = bpreview(client, acct, body)
    assert p.status_code == 200, p.text
    return client.post("/bronze", json={**body, "notebook_sha256": p.json()["notebook_sha256"], **extra}, headers=acct.headers)


def deploy_raw(client, acct, path, table, job_name):  # noqa: F811
    """A deployed Raw pipeline writing main.raw.<table>."""
    sid = register(client, acct, path=path)
    deploy(client, acct, spec([sid], job_name=job_name, table_names=[table], target_schema="raw"))
    return raw_asset(client, acct, table)


@pytest.fixture()
def raw(client, acct, workspace):  # noqa: F811
    """An org with one deployed Raw pipeline; returns its Raw asset (main.raw.customers)."""
    sid = register(client, acct, columns=cols(("id", "BIGINT"), ("name", "STRING")))
    deploy(client, acct, spec([sid], target_schema="raw"))
    return raw_asset(client, acct)


# --- the Raw tables remember their columns ------------------------------------------------

def test_a_raw_table_records_its_columns(client, acct, raw):  # noqa: F811
    detail = client.get(f"/assets/{raw['id']}", headers=acct.headers).json()
    assert detail["columns"] == [{"name": "id", "sql_type": "BIGINT"}, {"name": "name", "sql_type": "STRING"}]
    assert raw["column_count"] == 2


# --- preview -----------------------------------------------------------------------------

def test_preview_generates_a_notebook_over_the_source_columns_only(client, acct, raw, workspace, session_factory):  # noqa: F811
    r = bpreview(client, acct, bspec([raw["id"]]))
    assert r.status_code == 200, r.text
    nb = r.json()["notebook"]
    assert '"raw": "main.raw.customers"' in nb and '"bronze": "main.bronze.customers"' in nb
    assert '["id", "BIGINT"]' in nb and '["name", "STRING"]' in nb
    compared = nb[nb.index("TABLES = ["):nb.index("LINEAGE =")]
    assert not any(l in compared for l in LINEAGE)  # lineage columns are never part of a record's identity
    assert len(r.json()["notebook_sha256"]) == 64 and r.json()["workspace_path"].startswith("/Workspace/Shared/ingestion/")
    assert len(workspace.notebooks) == 1  # still only the Raw pipeline's own notebook
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Pipeline)) == 1  # nothing created by previewing


def test_preview_is_deterministic_and_sensitive_to_the_request(client, acct, raw):  # noqa: F811
    a = bpreview(client, acct, bspec([raw["id"]])).json()["notebook_sha256"]
    assert a == bpreview(client, acct, bspec([raw["id"]])).json()["notebook_sha256"]
    assert a != bpreview(client, acct, bspec([raw["id"]], names=["other"])).json()["notebook_sha256"]


# --- validation --------------------------------------------------------------------------

def test_unknown_and_foreign_tables_are_not_found(client, acct, raw, make_account, workspace):  # noqa: F811
    assert bpreview(client, acct, bspec([str(uuid.uuid4())])).status_code == 404
    other = make_account("b@b.com", "B")
    assert bpreview(client, other, bspec([raw["id"]])).status_code == 404  # indistinguishable from a missing one


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(catalog='main"; DROP'),
        lambda b: b.update(target_schema="a b"),
        lambda b: b.update(job_name="../../etc"),
        lambda b: b["inputs"][0].update(table_name="x'); import os #"),
        lambda b: b["inputs"][0].update(table_name="1abc"),
        lambda b: b["schedule"].update(timezone="Not/AZone"),
        lambda b: b.update(cluster_id="bad cluster!"),
    ],
)
def test_unsafe_values_are_refused(client, acct, raw, mutate):  # noqa: F811
    body = bspec([raw["id"]])
    mutate(body)
    r = bpreview(client, acct, body)
    assert r.status_code == 422 and r.json()["problems"], r.text


def test_shape_rules(client, acct, raw):  # noqa: F811
    assert bpreview(client, acct, bspec([raw["id"], raw["id"]], names=["a", "b"])).status_code == 422  # same table twice
    assert bpreview(client, acct, {**bspec([raw["id"]]), "inputs": []}).status_code == 422  # nothing to process


def test_two_inputs_cannot_share_a_target_name(client, acct, raw, workspace):  # noqa: F811
    orders = deploy_raw(client, acct, f"{FOLDER}/orders.csv", "orders", "orders_raw")
    assert bpreview(client, acct, bspec([raw["id"], orders["id"]], names=["same", "SAME"])).status_code == 422


def test_only_raw_tables_can_become_bronze(client, acct, raw, workspace):  # noqa: F811
    bdeploy(client, acct, bspec([raw["id"]]))
    (bronze,) = assets_in(client, acct, "bronze")
    r = bpreview(client, acct, bspec([bronze["id"]], names=["again"], job_name="again"))
    assert r.status_code == 422 and any("only Raw tables" in p for p in r.json()["problems"])


def test_the_target_cannot_be_the_raw_table_itself(client, acct, raw):  # noqa: F811
    body = bspec([raw["id"]], catalog=raw["catalog"], target_schema=raw["schema_name"], names=[raw["table_name"]])
    r = bpreview(client, acct, body)
    assert r.status_code == 422 and any("can't be the Raw table itself" in p for p in r.json()["problems"])


def test_a_target_already_registered_as_another_layer_is_refused(client, acct, raw, test_engine):  # noqa: F811
    with test_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO data_assets (org_id, catalog, schema_name, table_name, layer) VALUES (:o, 'main', 'bronze', 'customers', 'silver')"),
            {"o": acct.org_id},
        )
    r = bpreview(client, acct, bspec([raw["id"]]))
    assert r.status_code == 409 and "already registered as a silver table" in r.json()["detail"]


def test_a_table_without_a_column_list_is_refused(client, acct, raw, test_engine):  # noqa: F811
    with test_engine.begin() as conn:
        conn.execute(text("UPDATE data_assets SET columns = '[]'::jsonb WHERE id = :i"), {"i": raw["id"]})
    r = bpreview(client, acct, bspec([raw["id"]]))
    assert r.status_code == 422 and any("no column list" in p for p in r.json()["problems"])


def test_hostile_column_names_in_the_registry_never_reach_the_notebook(client, acct, raw, test_engine):  # noqa: F811
    hostile = [{"name": 'x"); import os; os.system("id")#', "sql_type": "STRING"}]
    with test_engine.begin() as conn:
        conn.execute(text("UPDATE data_assets SET columns = CAST(:c AS jsonb) WHERE id = :i"), {"c": json.dumps(hostile), "i": raw["id"]})
    r = bpreview(client, acct, bspec([raw["id"]]))
    assert r.status_code == 422 and "notebook" not in r.json()


def test_lineage_column_names_cannot_be_source_columns(client, acct, raw, test_engine):  # noqa: F811
    bad = [{"name": "id", "sql_type": "BIGINT"}, {"name": "_batch_id", "sql_type": "STRING"}]
    with test_engine.begin() as conn:
        conn.execute(text("UPDATE data_assets SET columns = CAST(:c AS jsonb) WHERE id = :i"), {"c": json.dumps(bad), "i": raw["id"]})
    assert bpreview(client, acct, bspec([raw["id"]])).status_code == 422


# --- deploy ------------------------------------------------------------------------------

def test_deploy_registers_the_bronze_table_and_links_it_to_its_raw_source(client, acct, raw, workspace):  # noqa: F811
    r = bdeploy(client, acct, bspec([raw["id"]]))
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["layer"] == "bronze" and out["mode"] == "single" and out["tables"] == []
    assert out["spec"]["inputs"] == [{"asset_id": raw["id"], "raw": raw["full_name"], "bronze": "main.bronze.customers", "table_name": "customers"}]
    assert out["job_parameters"] == {}  # it reads tables, not a file: no source path

    (bronze,) = assets_in(client, acct, "bronze")
    assert (bronze["full_name"], bronze["produced_by"]["job_name"], bronze["upstream_count"], bronze["column_count"]) == (
        "main.bronze.customers", "customers_bronze", 1, 2)
    detail = client.get(f"/assets/{bronze['id']}", headers=acct.headers).json()
    assert [u["full_name"] for u in detail["upstream"]] == [raw["full_name"]]
    assert [d["full_name"] for d in client.get(f"/assets/{raw['id']}", headers=acct.headers).json()["downstream"]] == ["main.bronze.customers"]

    job = list(workspace.jobs_by_id.values())[-1]
    assert job["name"] == "customers_bronze" and "source_path" not in job["tasks"][0].notebook_task.base_parameters
    assert workspace.notebooks[out["workspace_path"]].decode() == out["notebook_source"]


def test_a_multi_table_deploy(client, acct, raw, workspace):  # noqa: F811
    orders = deploy_raw(client, acct, f"{FOLDER}/orders.csv", "orders", "orders_raw")
    out = bdeploy(client, acct, bspec([raw["id"], orders["id"]], names=["customers", "orders"])).json()
    assert out["mode"] == "multi" and len(out["spec"]["inputs"]) == 2
    assert sorted(b["table_name"] for b in assets_in(client, acct, "bronze")) == ["customers", "orders"]


def test_the_notebook_hash_gate_applies(client, acct, raw, workspace):  # noqa: F811
    r = client.post("/bronze", json={**bspec([raw["id"]]), "notebook_sha256": "0" * 64}, headers=acct.headers)
    assert r.status_code == 409 and "preview again" in r.json()["detail"]


def test_a_failed_deploy_leaves_no_asset_and_no_lineage(client, acct, raw, workspace, session_factory):  # noqa: F811
    workspace.fail_create = True
    assert bdeploy(client, acct, bspec([raw["id"]])).status_code == 502
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(AssetLineage)) == 0
        assert s.scalar(select(func.count()).select_from(DataAsset).where(DataAsset.layer == "bronze")) == 0
        assert s.scalar(select(func.count()).select_from(Pipeline).where(Pipeline.layer == "bronze")) == 0


def test_bronze_pipelines_count_toward_the_plan_limit(client, acct, raw, workspace):  # noqa: F811
    assert bdeploy(client, acct, bspec([raw["id"]], job_name="b1")).status_code == 201
    deploy_raw(client, acct, f"{FOLDER}/f2.csv", "t2", "raw2")
    assert len(client.get("/pipelines", headers=acct.headers).json()) == 3  # Raw + Bronze + Raw: the free plan's limit
    r = bdeploy(client, acct, bspec([raw["id"]], job_name="b2", names=["again"]))
    assert r.status_code == 409 and "free plan allows 3" in r.json()["detail"]


def test_deleting_a_bronze_pipeline_keeps_its_table_and_lineage_and_redeploy_adopts_it(client, acct, raw, workspace):  # noqa: F811
    first = bdeploy(client, acct, bspec([raw["id"]])).json()
    assert client.delete(f"/pipelines/{first['id']}", headers=acct.headers).status_code == 204
    (kept,) = assets_in(client, acct, "bronze")
    assert kept["produced_by"] is None and kept["upstream_count"] == 1  # the table and where it came from remain
    second = bdeploy(client, acct, bspec([raw["id"]])).json()
    (again,) = assets_in(client, acct, "bronze")
    assert again["id"] == kept["id"] and again["produced_by"]["pipeline_id"] == second["id"] and again["upstream_count"] == 1


def test_bronze_pipelines_show_in_the_pipeline_list_with_their_layer(client, acct, raw, workspace):  # noqa: F811
    bdeploy(client, acct, bspec([raw["id"]]))
    layers = {p["job_name"]: p["layer"] for p in client.get("/pipelines", headers=acct.headers).json()}
    assert layers == {"customers_daily": "raw", "customers_bronze": "bronze"}


# --- the counts a run reports ------------------------------------------------------------

def report(layer, target, **n):
    return json.dumps({"v": 1, "layer": layer, "mode": "full", "reconciled": True,
                       "tables": {target.split(".")[-1]: {"source": "x", "target": target, **n}}})


def finished_run(client, acct, workspace, pipeline_id, output):  # noqa: F811
    """Start a run, let it finish, and make its notebook report `output` (None = it reported nothing)."""
    run_id = run_now(client, acct, pipeline_id).json()["databricks_run_id"]
    workspace.finish_run(run_id)
    if output is not None:
        workspace.run_outputs[run_id] = output
    return run_id


def test_a_finished_runs_counts_are_stored_and_update_the_registry(client, acct, raw, workspace):  # noqa: F811
    bronze = bdeploy(client, acct, bspec([raw["id"]])).json()
    finished_run(client, acct, workspace, bronze["id"], report(
        "bronze", "main.bronze.customers", rows_read=300, duplicates_removed=200, distinct_records=100,
        rows_inserted=100, already_in_target=0, rows_in_target=100))
    (r,) = refresh(client, acct, bronze["id"]).json()
    assert r["status"] == "success" and r["metrics"]["tables"]["customers"]["rows_read"] == 300
    assert r["metrics"]["reconciled"] is True and r["metrics"]["layer"] == "bronze"
    (asset,) = assets_in(client, acct, "bronze")
    assert asset["row_count"] == 100 and asset["last_run_at"] is not None
    assert len(workspace.output_reads) == 1


def test_raw_runs_report_too(client, acct, raw, workspace):  # noqa: F811
    pid = client.get("/pipelines", headers=acct.headers).json()[0]["id"]
    finished_run(client, acct, workspace, pid, report("raw", "main.raw.customers", rows_read=7, rows_written=7, rows_in_target=21))
    (r,) = refresh(client, acct, pid).json()
    assert r["metrics"]["tables"]["customers"]["rows_written"] == 7
    assert raw_asset(client, acct)["row_count"] == 21


def test_counts_are_fetched_once_not_on_every_refresh(client, acct, raw, workspace):  # noqa: F811
    bronze = bdeploy(client, acct, bspec([raw["id"]])).json()
    finished_run(client, acct, workspace, bronze["id"], report("bronze", "main.bronze.customers", rows_in_target=5))
    for _ in range(3):
        refresh(client, acct, bronze["id"])
    assert len(workspace.output_reads) == 1


def test_a_run_that_reported_nothing_is_marked_and_never_asked_again(client, acct, raw, workspace):  # noqa: F811
    bronze = bdeploy(client, acct, bspec([raw["id"]])).json()
    finished_run(client, acct, workspace, bronze["id"], None)  # e.g. a notebook generated before counts existed
    for _ in range(3):
        (r,) = refresh(client, acct, bronze["id"]).json()
    assert r["metrics"] == {"unavailable": True} and len(workspace.output_reads) == 1


def test_if_databricks_cannot_be_asked_the_run_is_tried_again_later(client, acct, raw, workspace):  # noqa: F811
    bronze = bdeploy(client, acct, bspec([raw["id"]])).json()
    finished_run(client, acct, workspace, bronze["id"], report("bronze", "main.bronze.customers", rows_in_target=9))
    workspace.fail_run_output = True
    r = refresh(client, acct, bronze["id"])
    assert r.status_code == 200 and r.json()[0]["metrics"] is None  # the refresh itself still works
    workspace.fail_run_output = False
    (r,) = refresh(client, acct, bronze["id"]).json()
    assert r["metrics"]["tables"]["customers"]["rows_in_target"] == 9


def test_reported_numbers_are_treated_as_untrusted(client, acct, raw, workspace):  # noqa: F811
    bronze = bdeploy(client, acct, bspec([raw["id"]])).json()
    hostile = json.dumps({
        "v": 1, "layer": "bronze", "mode": "x" * 500, "reconciled": "yes", "injected": {"a": 1},
        "tables": {"customers": {
            "target": "main.bronze.customers", "source": "s" * 5000,
            "rows_read": -5, "rows_inserted": 1.5, "rows_written": True, "rows_in_target": 2**80, "distinct_records": 12,
            "sightings_inserted": 7, "sightings_in_target": -1,
            "extra_source_columns": ["ok", 7, "y" * 500], "reconciled": "nope", "evil": "<script>",
        }},
    })
    finished_run(client, acct, workspace, bronze["id"], hostile)
    (r,) = refresh(client, acct, bronze["id"]).json()
    t = r["metrics"]["tables"]["customers"]
    assert t["distinct_records"] == 12 and t["sightings_inserted"] == 7 and "sightings_in_target" not in t
    assert not {"rows_read", "rows_inserted", "rows_written", "rows_in_target", "evil", "reconciled"} & set(t)
    assert len(t["source"]) == 300 and t["extra_source_columns"] == ["ok", "y" * 128]
    assert r["metrics"]["reconciled"] is False and len(r["metrics"]["mode"]) == 20 and "injected" not in r["metrics"]
    assert assets_in(client, acct, "bronze")[0]["row_count"] is None  # the impossible count never reached the registry


def test_a_report_cannot_touch_tables_the_pipeline_does_not_produce(client, acct, raw, workspace):  # noqa: F811
    bronze = bdeploy(client, acct, bspec([raw["id"]])).json()
    forged = json.dumps({"v": 1, "layer": "bronze", "reconciled": True, "tables": {
        "a": {"target": raw["full_name"], "rows_in_target": 999999},  # claims to have written the RAW table
        "b": {"target": "main.nowhere.nothing", "rows_in_target": 1}}})  # and one that doesn't exist
    finished_run(client, acct, workspace, bronze["id"], forged)
    refresh(client, acct, bronze["id"])
    assert raw_asset(client, acct)["row_count"] is None


def test_an_older_run_read_late_does_not_overwrite_a_newer_runs_count(client, acct, raw, workspace):  # noqa: F811
    pid = bdeploy(client, acct, bspec([raw["id"]])).json()["id"]
    first = finished_run(client, acct, workspace, pid, report("bronze", "main.bronze.customers", rows_in_target=10))
    workspace.fail_run_output = {first}  # run 1's counts can't be read yet
    refresh(client, acct, pid)
    finished_run(client, acct, workspace, pid, report("bronze", "main.bronze.customers", rows_in_target=20))
    refresh(client, acct, pid)  # run 2 is read; run 1 still isn't
    assert assets_in(client, acct, "bronze")[0]["row_count"] == 20
    workspace.fail_run_output = False
    refresh(client, acct, pid)  # run 1 is read now, but it is the OLDER run
    assert assets_in(client, acct, "bronze")[0]["row_count"] == 20
    assert {r["metrics"]["tables"]["customers"]["rows_in_target"] for r in client.get(f"/pipelines/{pid}/runs", headers=acct.headers).json()} == {10, 20}


def test_other_orgs_cannot_see_the_counts(client, acct, raw, workspace, make_account):  # noqa: F811
    bronze = bdeploy(client, acct, bspec([raw["id"]])).json()
    finished_run(client, acct, workspace, bronze["id"], report("bronze", "main.bronze.customers", rows_in_target=4))
    refresh(client, acct, bronze["id"])
    other = make_account("b@b.com", "B")
    assert client.get("/runs", headers=other.headers).json() == []


# --- the schema change -------------------------------------------------------------------

def test_migration_007_backfills_columns_for_existing_tables_and_can_be_rerun(scratch_database):
    from tests.conftest import schema_files

    files = schema_files()
    assert files[6].name == "007_asset_columns.sql"
    schema = json.dumps([{"name": "id", "sql_type": "BIGINT", "nullable": True}, {"name": "amt", "sql_type": "DECIMAL(9,2)"}])
    with scratch_database.begin() as conn:
        for f in files[:6]:
            conn.exec_driver_sql(f.read_text(encoding="utf-8"))
        conn.execute(text("INSERT INTO orgs (name) VALUES ('Old Co')"))
        conn.execute(
            text("INSERT INTO source_registry (org_id, source_type, source_path, schema_json, schema_hash) "
                 "SELECT id, 'csv', '/Volumes/m/l/old.csv', CAST(:s AS jsonb), 'h' FROM orgs"),
            {"s": schema},
        )
        conn.execute(text("INSERT INTO pipelines (org_id, job_name, catalog, target_schema) SELECT id, 'j', 'main', 'raw' FROM orgs"))
        conn.execute(text("INSERT INTO pipeline_sources (pipeline_id, source_registry_id, table_name) SELECT p.id, s.id, 'old' FROM pipelines p, source_registry s"))
        conn.execute(text("INSERT INTO data_assets (org_id, catalog, schema_name, table_name, layer, produced_by) SELECT p.org_id, 'main', 'raw', 'old', 'raw', p.id FROM pipelines p"))
    for _ in range(2):  # apply, then apply AGAIN
        with scratch_database.begin() as conn:
            conn.exec_driver_sql(files[6].read_text(encoding="utf-8"))
    with scratch_database.begin() as conn:
        assert conn.execute(text("SELECT columns FROM data_assets")).scalar_one() == [
            {"name": "id", "sql_type": "BIGINT"}, {"name": "amt", "sql_type": "DECIMAL(9,2)"}]
        conn.execute(text("UPDATE data_assets SET columns = CAST(:c AS jsonb)"), {"c": json.dumps([{"name": "kept", "sql_type": "STRING"}])})
    with scratch_database.begin() as conn:  # re-running must not clobber a column list that is already there
        conn.exec_driver_sql(files[6].read_text(encoding="utf-8"))
    with scratch_database.connect() as conn:
        assert conn.execute(text("SELECT columns FROM data_assets")).scalar_one() == [{"name": "kept", "sql_type": "STRING"}]
