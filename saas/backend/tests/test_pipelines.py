"""Building, previewing, deploying and deleting pipelines against a fake Databricks workspace.

The real engine code (codegen.py / databricks_deploy.py) runs; only the network is faked.
"""
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select, text

from app.models import Pipeline, PipelineRun, PipelineSource
from tests.conftest import cols, source_body
from tests.test_connection import connect

FOLDER = "/Volumes/main/landing"
CSV = f"{FOLDER}/customers.csv"


def register(client, acct, path=CSV, columns=None, source_type="csv", config=None, **extra) -> str:
    body = source_body(path, columns, source_type=source_type, discovery_config=config or {"delimiter": ",", "has_header": True}, **extra)
    r = client.post("/sources", json=body, headers=acct.headers)
    assert r.status_code in (200, 201), r.text
    return r.json()["source"]["id"]


def spec(source_ids, job_name="customers_daily", table_names=None, mode="single", **over):
    names = table_names or [f"t{i}" if len(source_ids) > 1 else "customers" for i in range(len(source_ids))]
    return {
        "job_name": job_name,
        "mode": mode,
        "catalog": "main",
        "target_schema": "bronze",
        "tables": [{"source_id": s, "table_name": n} for s, n in zip(source_ids, names)],
        "schedule": {"frequency": "daily", "time": "02:30", "timezone": "America/Chicago"},
        **over,
    }


def preview(client, acct, body):
    return client.post("/pipelines/preview", json=body, headers=acct.headers)


def deploy(client, acct, body, **extra):
    """Preview, then deploy with the hash of what was previewed — the intended flow."""
    p = preview(client, acct, body)
    assert p.status_code == 200, p.text
    return client.post("/pipelines", json={**body, "notebook_sha256": p.json()["notebook_sha256"], **extra}, headers=acct.headers)


@pytest.fixture()
def acct(client, make_account, workspace):
    a = make_account("admin@a.com")
    assert connect(client, a).status_code == 200
    return a


def pipeline_count(session_factory) -> int:
    with session_factory() as s:
        return s.scalar(select(func.count()).select_from(Pipeline))


# --- preview -----------------------------------------------------------------------------

def test_preview_generates_the_notebook_and_changes_nothing(client, acct, workspace, session_factory):
    sid = register(client, acct)
    r = preview(client, acct, spec([sid]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "customers" in body["notebook"] and "main" in body["notebook"] and "bronze" in body["notebook"]
    assert "id" in body["notebook"] and "BIGINT" in body["notebook"]
    assert len(body["notebook_sha256"]) == 64
    assert body["cron"] == "0 30 2 * * ?"
    assert body["workspace_path"].startswith("/Workspace/Shared/ingestion/")
    assert workspace.notebooks == {} and workspace.jobs_by_id == {}
    assert pipeline_count(session_factory) == 0


def test_preview_is_deterministic(client, acct):
    sid = register(client, acct)
    a = preview(client, acct, spec([sid])).json()
    b = preview(client, acct, spec([sid])).json()
    assert a["notebook_sha256"] == b["notebook_sha256"]


def test_a_change_to_the_request_changes_the_hash(client, acct):
    sid = register(client, acct)
    a = preview(client, acct, spec([sid])).json()["notebook_sha256"]
    b = preview(client, acct, {**spec([sid]), "target_schema": "silver"}).json()["notebook_sha256"]
    assert a != b


HOSTILE_NAME = 'x"); import os; os.system("id")#'


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(catalog='main"; DROP'),
        lambda b: b.update(target_schema="a b"),
        lambda b: b.update(job_name="../../etc"),
        lambda b: b["tables"][0].update(table_name=HOSTILE_NAME),
        lambda b: b["tables"][0].update(table_name="1abc"),
        lambda b: b["schedule"].update(timezone="Not/AZone"),
        lambda b: b["schedule"].update(time="25:00"),
        lambda b: b["schedule"].update(day_of_week="Someday"),
        lambda b: b.update(cluster_id="bad cluster id!"),
    ],
)
def test_preview_rejects_unsafe_request_values(client, acct, mutate):
    sid = register(client, acct)
    body = spec([sid])
    mutate(body)
    r = preview(client, acct, body)
    assert r.status_code == 422, r.text
    assert r.json()["problems"]


def test_hostile_column_names_stored_in_the_registry_never_reach_the_notebook(client, acct):
    """POST /sources accepts whatever a client sends, so the pipeline builder must re-check it."""
    sid = register(client, acct, columns=cols(("id", "BIGINT"), (HOSTILE_NAME, "STRING")))
    r = preview(client, acct, spec([sid]))
    assert r.status_code == 422
    assert any("name must start" in p for p in r.json()["problems"])
    assert "notebook" not in r.json()  # nothing was generated


@pytest.mark.parametrize(
    "columns",
    [
        cols(("id", "VARCHAR(10); DROP TABLE x")),
        cols(("id", "BIGINT"), ("ID", "STRING")),
        cols(("_ingested_at", "STRING")),
        cols(("id", "DECIMAL(10,2)")),  # csv can't carry decimals
    ],
)
def test_unsafe_registered_columns_are_refused(client, acct, columns):
    sid = register(client, acct, columns=columns)
    assert preview(client, acct, spec([sid])).status_code == 422


@pytest.mark.parametrize(
    "path, config",
    [
        ('/Volumes/main/landing/a".csv', None),
        (CSV, {"delimiter": '"'}),
        (CSV, {"delimiter": "{"}),
        (CSV, {"has_header": "yes"}),
        (CSV, {"delimiter": 5}),
    ],
)
def test_unsafe_paths_and_settings_stored_in_the_registry_are_refused(client, acct, path, config):
    sid = register(client, acct, path=path, config=config)
    assert preview(client, acct, spec([sid])).status_code == 422


def test_another_orgs_source_is_not_found(client, acct, make_account, workspace):
    other = make_account("b@b.com", "B")
    foreign = register(client, other)
    r = preview(client, acct, spec([foreign]))
    assert r.status_code == 404
    assert client.post("/pipelines/preview", json=spec([foreign]), headers=acct.headers).status_code == 404


def test_a_superseded_source_version_cannot_be_used(client, acct):
    old = register(client, acct)
    register(client, acct, columns=cols(("id", "BIGINT"), ("name", "STRING"), ("email", "STRING")))
    r = preview(client, acct, spec([old]))
    assert r.status_code == 409 and "superseded" in r.json()["detail"]


def test_shape_rules(client, acct):
    a = register(client, acct, path=f"{FOLDER}/a.csv")
    b = register(client, acct, path=f"{FOLDER}/b.csv")
    assert preview(client, acct, spec([a, a], table_names=["x", "y"], mode="multi")).status_code == 422  # same source twice
    assert preview(client, acct, spec([a, b], table_names=["x", "X"], mode="multi")).status_code == 422  # duplicate names
    assert preview(client, acct, spec([a, b], table_names=["x", "y"], mode="single")).status_code == 422  # two in single
    assert preview(client, acct, {**spec([a]), "tables": []}).status_code == 422


# --- multi-file / xml / cobol --------------------------------------------------------------

def test_multi_file_pipeline_previews_every_table(client, acct):
    a = register(client, acct, path=f"{FOLDER}/a.csv")
    b = register(client, acct, path=f"{FOLDER}/b.csv", columns=cols(("k", "STRING")))
    r = preview(client, acct, spec([a, b], table_names=["alpha", "beta"], mode="multi"))
    assert r.status_code == 200, r.text
    nb = r.json()["notebook"]
    assert "alpha" in nb and "beta" in nb and "a.csv" in nb and "b.csv" in nb


def test_multi_file_rules(client, acct):
    a = register(client, acct, path=f"{FOLDER}/a.csv")
    other_folder = register(client, acct, path="/Volumes/main/other/b.csv")
    other_settings = register(client, acct, path=f"{FOLDER}/c.csv", config={"delimiter": "|"})
    hostile = register(client, acct, path=f"{FOLDER}/{{__import__('os')}}.csv")
    assert preview(client, acct, spec([a, other_folder], table_names=["x", "y"], mode="multi")).status_code == 422
    assert preview(client, acct, spec([a, other_settings], table_names=["x", "y"], mode="multi")).status_code == 422
    assert preview(client, acct, spec([a, hostile], table_names=["x", "y"], mode="multi")).status_code == 422


def test_xml_pipeline(client, acct):
    sid = register(client, acct, path=f"{FOLDER}/orders.xml", source_type="xml", config={"row_tag": "order"},
                   schema_definition_path=f"{FOLDER}/orders.xsd")
    r = preview(client, acct, spec([sid], table_names=["orders"]))
    assert r.status_code == 200, r.text
    assert "order" in r.json()["notebook"]
    bad = register(client, acct, path=f"{FOLDER}/o2.xml", source_type="xml", config={"row_tag": 'a"b'})
    assert preview(client, acct, spec([bad], table_names=["o2"])).status_code == 422


def cobol_columns():
    return [
        {"name": "cust_id", "sql_type": "STRING", "struct_path": ["CUSTOMER", "CUST-ID"]},
        {"name": "balance", "sql_type": "DECIMAL(15,2)", "struct_path": ["CUSTOMER", "BALANCE"]},
    ]


def test_cobol_pipeline_needs_a_cluster_and_passes_the_copybook(client, acct, workspace):
    sid = register(client, acct, path=f"{FOLDER}/cust.dat", source_type="cobol", columns=cobol_columns(),
                   config={"is_record_sequence": True}, schema_definition_path=f"{FOLDER}/cust.cpy")
    body = spec([sid], table_names=["cust"])
    r = preview(client, acct, body)
    assert r.status_code == 422 and any("cluster_id" in p for p in r.json()["problems"])

    body["cluster_id"] = "0101-000000-abc123"
    assert deploy(client, acct, body).status_code == 201
    (job,) = workspace.jobs_by_id.values()
    task = job["tasks"][0]
    assert task.existing_cluster_id == "0101-000000-abc123"
    assert task.notebook_task.base_parameters == {"source_path": f"{FOLDER}/cust.dat", "copybook_path": f"{FOLDER}/cust.cpy"}


def test_multi_file_is_refused_for_cobol(client, acct):
    sid = register(client, acct, path=f"{FOLDER}/cust.dat", source_type="cobol", columns=cobol_columns(),
                   schema_definition_path=f"{FOLDER}/cust.cpy")
    assert preview(client, acct, spec([sid], table_names=["c"], mode="multi", cluster_id="0101-000000-abc123")).status_code == 422


# --- deploy ------------------------------------------------------------------------------

def test_deploy_uploads_exactly_the_previewed_notebook_and_schedules_the_job(client, acct, workspace, session_factory):
    sid = register(client, acct)
    body = spec([sid])
    previewed = preview(client, acct, body).json()
    r = client.post("/pipelines", json={**body, "notebook_sha256": previewed["notebook_sha256"]}, headers=acct.headers)
    assert r.status_code == 201, r.text
    out = r.json()

    assert out["job_name"] == "customers_daily" and out["databricks_job_id"] in workspace.jobs_by_id
    assert out["databricks_job_url"] == f"https://fake-1.cloud.databricks.com/jobs/{out['databricks_job_id']}"
    assert out["workspace_path"] == previewed["workspace_path"]
    assert out["schedule_frequency"] == "daily" and out["schedule_time"] == "02:30" and out["schedule_timezone"] == "America/Chicago"
    assert out["tables"] == [{
        "table_name": "customers", "source_id": sid, "source_type": "csv", "source_path": CSV,
        "source_version": 1, "is_stale": False,
    }]

    uploaded = workspace.notebooks[previewed["workspace_path"]].decode("utf-8")
    assert uploaded == previewed["notebook"]  # byte-for-byte what was reviewed
    assert out["notebook_source"] == uploaded and out["notebook_sha256"] == previewed["notebook_sha256"]

    job = workspace.jobs_by_id[out["databricks_job_id"]]
    assert job["name"] == "customers_daily"
    assert job["schedule"].quartz_cron_expression == "0 30 2 * * ?" and job["schedule"].timezone_id == "America/Chicago"
    assert job["tasks"][0].notebook_task.base_parameters == {"source_path": CSV}
    assert workspace.resets == []


def test_deploy_without_the_matching_hash_is_refused(client, acct, workspace, session_factory):
    sid = register(client, acct)
    body = spec([sid])
    r = client.post("/pipelines", json={**body, "notebook_sha256": "0" * 64}, headers=acct.headers)
    assert r.status_code == 409 and "preview again" in r.json()["detail"]
    assert workspace.notebooks == {} and workspace.jobs_by_id == {} and pipeline_count(session_factory) == 0


def test_a_source_that_drifts_after_preview_blocks_the_deploy(client, acct, workspace):
    sid = register(client, acct)
    body = spec([sid])
    sha = preview(client, acct, body).json()["notebook_sha256"]
    register(client, acct, columns=cols(("id", "BIGINT"), ("name", "STRING"), ("extra", "STRING")))
    r = client.post("/pipelines", json={**body, "notebook_sha256": sha}, headers=acct.headers)
    assert r.status_code == 409
    assert workspace.jobs_by_id == {}


def test_deploy_needs_a_connected_workspace(client, make_account, workspace):
    a = make_account("x@x.com")
    sid = register(client, a)
    r = client.post("/pipelines", json={**spec([sid]), "notebook_sha256": "0" * 64}, headers=a.headers)
    assert r.status_code == 409 and "Connect your Databricks workspace" in r.json()["detail"]


def test_run_now_records_the_run(client, acct, workspace, session_factory):
    sid = register(client, acct)
    r = deploy(client, acct, spec([sid]), run_now=True)
    assert r.status_code == 201, r.text
    assert len(workspace.runs) == 1
    with session_factory() as s:
        (run,) = s.scalars(select(PipelineRun)).all()
        assert run.databricks_run_id == workspace.runs[0] and run.triggered_by == "deploy"


def test_a_member_can_deploy(client, acct, join_org, workspace):
    member = join_org(acct, "m@a.com")
    sid = register(client, acct)
    assert deploy(client, member, spec([sid])).status_code == 201


def test_the_free_plan_limit_is_enforced(client, acct, workspace, session_factory):
    ids = [register(client, acct, path=f"{FOLDER}/f{i}.csv") for i in range(4)]
    for i in range(3):
        assert deploy(client, acct, spec([ids[i]], job_name=f"job{i}", table_names=[f"t{i}"])).status_code == 201
    r = deploy(client, acct, spec([ids[3]], job_name="job3", table_names=["t3"]))
    assert r.status_code == 409 and "free plan allows 3" in r.json()["detail"]
    assert len(workspace.jobs_by_id) == 3 and pipeline_count(session_factory) == 3


def test_concurrent_deploys_cannot_exceed_the_plan_limit(client, acct, workspace, session_factory):
    ids = [register(client, acct, path=f"{FOLDER}/f{i}.csv") for i in range(4)]
    for i in range(2):
        assert deploy(client, acct, spec([ids[i]], job_name=f"job{i}", table_names=[f"t{i}"])).status_code == 201

    bodies = [spec([ids[i]], job_name=f"job{i}", table_names=[f"t{i}"]) for i in (2, 3)]
    shas = [preview(client, acct, b).json()["notebook_sha256"] for b in bodies]

    workspace.delay = 0.5  # both requests are then in flight together; only the org lock keeps them apart

    def go(i):
        return client.post("/pipelines", json={**bodies[i], "notebook_sha256": shas[i]}, headers=acct.headers).status_code

    with ThreadPoolExecutor(2) as pool:
        codes = sorted(pool.map(go, [0, 1]))
    assert codes == [201, 409]
    assert len(workspace.jobs_by_id) == 3 and pipeline_count(session_factory) == 3


def test_a_duplicate_pipeline_name_is_refused_without_touching_databricks(client, acct, workspace):
    a = register(client, acct, path=f"{FOLDER}/a.csv")
    b = register(client, acct, path=f"{FOLDER}/b.csv")
    assert deploy(client, acct, spec([a], job_name="same")).status_code == 201
    r = deploy(client, acct, spec([b], job_name="same"))
    assert r.status_code == 409
    assert len(workspace.jobs_by_id) == 1 and len(workspace.notebooks) == 1


def test_an_existing_databricks_job_with_that_name_is_never_overwritten(client, acct, workspace, session_factory):
    foreign = workspace.add_foreign_job("customers_daily")
    sid = register(client, acct)
    r = deploy(client, acct, spec([sid]))
    assert r.status_code == 409 and "already has a job" in r.json()["detail"]
    assert workspace.resets == [] and workspace.jobs_by_id[foreign]["tasks"] == ["theirs"]
    assert workspace.notebooks == {} and pipeline_count(session_factory) == 0


def test_an_existing_notebook_at_the_target_path_is_never_overwritten(client, acct, workspace, session_factory):
    sid = register(client, acct)
    path = preview(client, acct, spec([sid])).json()["workspace_path"]
    workspace.notebooks[path] = b"someone else's work"
    r = deploy(client, acct, spec([sid]))
    assert r.status_code == 409
    assert workspace.notebooks[path] == b"someone else's work"
    assert workspace.jobs_by_id == {} and pipeline_count(session_factory) == 0


def test_a_databricks_failure_leaves_nothing_behind_and_keeps_the_slot(client, acct, workspace, session_factory):
    ids = [register(client, acct, path=f"{FOLDER}/f{i}.csv") for i in range(4)]
    workspace.fail_create = True
    r = deploy(client, acct, spec([ids[0]], job_name="broken"))
    assert r.status_code == 502
    assert pipeline_count(session_factory) == 0
    assert workspace.notebooks == {}  # the uploaded notebook was cleaned up
    assert workspace.jobs_by_id == {}

    workspace.fail_create = False
    for i in range(3):  # all three free slots are still available
        assert deploy(client, acct, spec([ids[i]], job_name=f"job{i}", table_names=[f"t{i}"])).status_code == 201


def test_pipelines_are_visible_only_to_their_own_org(client, acct, make_account, workspace):
    sid = register(client, acct)
    pid = deploy(client, acct, spec([sid])).json()["id"]
    other = make_account("b@b.com", "B")
    assert client.get("/pipelines", headers=other.headers).json() == []
    assert client.get(f"/pipelines/{pid}", headers=other.headers).status_code == 404
    assert client.delete(f"/pipelines/{pid}", headers=other.headers).status_code == 404
    assert len(workspace.jobs_by_id) == 1


def test_list_and_get(client, acct, workspace):
    a = register(client, acct, path=f"{FOLDER}/a.csv")
    b = register(client, acct, path=f"{FOLDER}/b.csv")
    p1 = deploy(client, acct, spec([a], job_name="one")).json()
    p2 = deploy(client, acct, spec([a, b], job_name="two", table_names=["x", "y"], mode="multi")).json()
    listed = client.get("/pipelines", headers=acct.headers).json()
    assert [p["id"] for p in listed] == [p1["id"], p2["id"]]
    assert "notebook_source" not in listed[0]
    got = client.get(f"/pipelines/{p2['id']}", headers=acct.headers).json()
    assert [t["table_name"] for t in got["tables"]] == ["x", "y"]
    assert got["job_parameters"] == {"source_path": FOLDER}
    usage = client.get("/org/usage", headers=acct.headers).json()
    assert usage["pipeline_count"] == 2


def test_schema_drift_after_deploy_marks_the_pipeline_stale(client, acct, workspace):
    sid = register(client, acct)
    pid = deploy(client, acct, spec([sid])).json()["id"]
    assert client.get("/drift", headers=acct.headers).json() == []
    register(client, acct, columns=cols(("id", "BIGINT"), ("name", "STRING"), ("extra", "STRING")))
    table = client.get(f"/pipelines/{pid}", headers=acct.headers).json()["tables"][0]
    assert table["is_stale"] is True and table["source_version"] == 1
    (drift,) = client.get("/drift", headers=acct.headers).json()
    assert drift["pipeline_id"] == pid


# --- delete ------------------------------------------------------------------------------

def test_delete_removes_the_job_notebook_and_record_and_frees_the_slot(client, acct, workspace, session_factory):
    sid = register(client, acct)
    out = deploy(client, acct, spec([sid])).json()
    assert client.delete(f"/pipelines/{out['id']}", headers=acct.headers).status_code == 204
    assert workspace.jobs_by_id == {} and workspace.notebooks == {}
    assert client.get(f"/pipelines/{out['id']}", headers=acct.headers).status_code == 404
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(PipelineSource)) == 0
    assert client.get("/org/usage", headers=acct.headers).json()["pipeline_count"] == 0
    assert deploy(client, acct, spec([sid])).status_code == 201  # the name and the slot are reusable


def test_delete_permissions(client, acct, join_org, workspace):
    member = join_org(acct, "m@a.com")
    other_member = join_org(acct, "n@a.com")
    a = register(client, acct, path=f"{FOLDER}/a.csv")
    b = register(client, acct, path=f"{FOLDER}/b.csv")
    admins = deploy(client, acct, spec([a], job_name="admins")).json()["id"]
    mine = deploy(client, member, spec([b], job_name="mine")).json()["id"]

    assert client.delete(f"/pipelines/{admins}", headers=member.headers).status_code == 403  # not theirs
    assert client.delete(f"/pipelines/{mine}", headers=other_member.headers).status_code == 403
    assert client.delete(f"/pipelines/{mine}", headers=member.headers).status_code == 204  # their own
    assert client.delete(f"/pipelines/{admins}", headers=acct.headers).status_code == 204  # admin
    assert workspace.jobs_by_id == {}


def test_delete_still_works_when_the_job_is_already_gone_from_databricks(client, acct, workspace):
    sid = register(client, acct)
    out = deploy(client, acct, spec([sid])).json()
    workspace.jobs_by_id.clear()
    assert client.delete(f"/pipelines/{out['id']}", headers=acct.headers).status_code == 204


def test_a_databricks_failure_on_delete_keeps_the_record(client, acct, workspace):
    sid = register(client, acct)
    out = deploy(client, acct, spec([sid])).json()
    workspace.fail_delete_job = True
    assert client.delete(f"/pipelines/{out['id']}", headers=acct.headers).status_code == 502
    assert client.get(f"/pipelines/{out['id']}", headers=acct.headers).status_code == 200
    workspace.fail_delete_job = False
    assert client.delete(f"/pipelines/{out['id']}", headers=acct.headers).status_code == 204


def test_a_job_id_is_never_used_against_a_different_workspace(client, acct, workspace):
    sid = register(client, acct)
    out = deploy(client, acct, spec([sid])).json()
    assert connect(client, acct, host="fake-2.cloud.databricks.com", token="second-token-value-99").status_code == 200
    r = client.delete(f"/pipelines/{out['id']}", headers=acct.headers)
    assert r.status_code == 409 and "fake-1" in r.json()["detail"]
    assert out["databricks_job_id"] in workspace.jobs_by_id  # the job was not touched
    assert connect(client, acct).status_code == 200  # reconnect the original workspace
    assert client.delete(f"/pipelines/{out['id']}", headers=acct.headers).status_code == 204


def test_deleting_the_source_version_of_a_live_pipeline_is_still_blocked(client, acct, workspace, session_factory):
    sid = register(client, acct)
    deploy(client, acct, spec([sid]))
    from sqlalchemy.exc import IntegrityError

    with session_factory() as s:
        with pytest.raises(IntegrityError):
            s.execute(text("DELETE FROM source_registry WHERE id = :i"), {"i": sid})
            s.commit()
