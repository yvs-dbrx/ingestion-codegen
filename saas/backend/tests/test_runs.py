"""Run history: starting runs, syncing them from Databricks, and who can see what."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from databricks.sdk.service.jobs import RunLifeCycleState as Life
from databricks.sdk.service.jobs import RunResultState as Result
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import PipelineRun
from app.services import engine
from tests.test_connection import connect
from tests.test_pipelines import acct, deploy, register, spec  # noqa: F401  (acct is a fixture)


@pytest.fixture()
def pipeline(client, acct, workspace):  # noqa: F811
    """An org with one deployed pipeline; returns (pipeline id, Databricks job id)."""
    sid = register(client, acct)
    out = deploy(client, acct, spec([sid])).json()
    return out["id"], out["databricks_job_id"]


def runs(client, acct, pid, **params):
    return client.get(f"/pipelines/{pid}/runs", params=params, headers=acct.headers)


def run_now(client, acct, pid):
    return client.post(f"/pipelines/{pid}/run", headers=acct.headers)


def refresh(client, acct, pid):
    return client.post(f"/pipelines/{pid}/runs/refresh", headers=acct.headers)


def run_count(session_factory) -> int:
    with session_factory() as s:
        return s.scalar(select(func.count()).select_from(PipelineRun))


# --- Databricks state -> our status ------------------------------------------------------

@pytest.mark.parametrize(
    "life, result, expected",
    [
        (Life.PENDING, None, "pending"),
        (Life.QUEUED, None, "pending"),
        (Life.BLOCKED, None, "pending"),
        (Life.WAITING_FOR_RETRY, None, "pending"),
        (Life.RUNNING, None, "running"),
        (Life.TERMINATING, None, "running"),
        (Life.TERMINATED, Result.SUCCESS, "success"),
        (Life.TERMINATED, Result.FAILED, "failed"),
        (Life.TERMINATED, Result.TIMEDOUT, "failed"),
        (Life.TERMINATED, Result.SUCCESS_WITH_FAILURES, "failed"),  # never reported as a clean success
        (Life.TERMINATED, Result.UPSTREAM_FAILED, "failed"),
        (Life.TERMINATED, Result.CANCELED, "cancelled"),
        (Life.TERMINATED, Result.EXCLUDED, "cancelled"),
        (Life.TERMINATED, None, "failed"),  # finished without saying it succeeded
        (Life.SKIPPED, None, "cancelled"),
        (Life.INTERNAL_ERROR, None, "failed"),
        ("SOMETHING_NEW", None, "running"),  # unknown states never claim success
        ("TERMINATED", "SUCCESS", "success"),  # plain strings work too
    ],
)
def test_status_mapping(life, result, expected):
    assert engine.map_status(life, result) == expected


# --- starting a run ----------------------------------------------------------------------

def test_run_now_starts_the_job_and_records_it(client, acct, workspace, pipeline, session_factory):  # noqa: F811
    pid, job_id = pipeline
    r = run_now(client, acct, pid)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["triggered_by"] == "manual" and body["status"] == "pending" and body["pipeline_id"] == pid
    assert body["databricks_run_id"] == workspace.runs[-1]
    assert workspace.remote_runs[body["databricks_run_id"]]["job_id"] == job_id
    assert [x["id"] for x in runs(client, acct, pid).json()] == [body["id"]]


def test_a_second_run_is_refused_while_one_is_in_progress(client, acct, workspace, pipeline):  # noqa: F811
    pid, _ = pipeline
    first = run_now(client, acct, pid).json()
    r = run_now(client, acct, pid)
    assert r.status_code == 409 and "in progress" in r.json()["detail"]
    assert len(workspace.runs) == 1

    workspace.finish_run(first["databricks_run_id"])  # ...and once it's finished, a new one is fine
    assert run_now(client, acct, pid).status_code == 201
    assert len(workspace.runs) == 2
    statuses = {x["databricks_run_id"]: x["status"] for x in runs(client, acct, pid).json()}
    assert statuses[first["databricks_run_id"]] == "success"


def test_concurrent_run_requests_start_only_one_run(client, acct, workspace, pipeline):  # noqa: F811
    pid, _ = pipeline
    workspace.run_delay = 0.5  # both requests in flight together; only the pipeline lock separates them

    with ThreadPoolExecutor(2) as pool:
        codes = sorted(pool.map(lambda _: run_now(client, acct, pid).status_code, range(2)))
    assert codes == [201, 409]
    assert len(workspace.runs) == 1


def test_a_databricks_failure_starting_a_run_records_nothing(client, acct, workspace, pipeline, session_factory):  # noqa: F811
    pid, _ = pipeline
    workspace.fail_run_now = True
    assert run_now(client, acct, pid).status_code == 502
    assert run_count(session_factory) == 0
    workspace.fail_run_now = False
    assert run_now(client, acct, pid).status_code == 201


def test_a_member_can_run_a_pipeline(client, acct, join_org, workspace, pipeline):  # noqa: F811
    member = join_org(acct, "m@a.com")
    assert run_now(client, member, pipeline[0]).status_code == 201


# --- syncing from Databricks -------------------------------------------------------------

def test_refresh_picks_up_scheduled_runs_and_their_outcomes(client, acct, workspace, pipeline):  # noqa: F811
    pid, job_id = pipeline
    ok = workspace.add_scheduled_run(job_id)
    bad = workspace.add_scheduled_run(job_id, result=Result.FAILED, message="Task ingest failed: table not found")
    going = workspace.add_scheduled_run(job_id, life=Life.RUNNING, result=None)

    r = refresh(client, acct, pid)
    assert r.status_code == 200, r.text
    by_id = {x["databricks_run_id"]: x for x in r.json()}
    assert by_id[ok]["status"] == "success" and by_id[ok]["triggered_by"] == "schedule"
    assert by_id[ok]["started_at"] and by_id[ok]["finished_at"]
    assert by_id[bad]["status"] == "failed" and by_id[bad]["error_message"] == "Task ingest failed: table not found"
    assert by_id[going]["status"] == "running" and by_id[going]["finished_at"] is None
    assert by_id[ok]["databricks_run_url"].endswith(f"/jobs/{job_id}/runs/{ok}")
    assert by_id[ok]["error_message"] is None and by_id[ok]["rows_by_table"] is None


def test_refresh_updates_a_run_we_already_know(client, acct, workspace, pipeline, session_factory):  # noqa: F811
    pid, _ = pipeline
    run = run_now(client, acct, pid).json()
    workspace.finish_run(run["databricks_run_id"], Result.FAILED, "boom")
    (row,) = refresh(client, acct, pid).json()
    assert row["id"] == run["id"] and row["status"] == "failed" and row["triggered_by"] == "manual"  # same row, updated
    assert run_count(session_factory) == 1


def test_refreshing_repeatedly_never_duplicates_runs(client, acct, workspace, pipeline, session_factory):  # noqa: F811
    pid, job_id = pipeline
    for _ in range(3):
        workspace.add_scheduled_run(job_id)
    for _ in range(3):
        assert len(refresh(client, acct, pid).json()) == 3
    assert run_count(session_factory) == 3


def test_concurrent_refreshes_never_duplicate_runs(client, acct, workspace, pipeline, session_factory):  # noqa: F811
    pid, job_id = pipeline
    for _ in range(4):
        workspace.add_scheduled_run(job_id)
    with ThreadPoolExecutor(4) as pool:
        codes = list(pool.map(lambda _: refresh(client, acct, pid).status_code, range(4)))
    assert codes == [200] * 4 and run_count(session_factory) == 4


def test_the_run_started_by_deploy_is_matched_not_duplicated(client, acct, workspace, session_factory):  # noqa: F811
    sid = register(client, acct)
    out = deploy(client, acct, spec([sid]), run_now=True).json()
    assert out["last_run"]["triggered_by"] == "deploy" and out["last_run"]["status"] == "pending"
    (row,) = refresh(client, acct, out["id"]).json()
    assert row["triggered_by"] == "deploy" and row["status"] == "running"  # Databricks says it's running now
    assert run_count(session_factory) == 1


def test_a_long_error_message_is_capped(client, acct, workspace, pipeline):  # noqa: F811
    pid, job_id = pipeline
    workspace.add_scheduled_run(job_id, result=Result.FAILED, message="x" * 5000)
    (row,) = refresh(client, acct, pid).json()
    assert len(row["error_message"]) == 2000


def test_a_databricks_failure_during_refresh_is_a_502_and_keeps_what_we_had(client, acct, workspace, pipeline, session_factory):  # noqa: F811
    pid, _ = pipeline
    run_now(client, acct, pid)
    workspace.fail_list_runs = True
    assert refresh(client, acct, pid).status_code == 502
    assert run_count(session_factory) == 1
    assert len(runs(client, acct, pid).json()) == 1


def test_a_job_deleted_in_databricks_is_reported_clearly(client, acct, workspace, pipeline):  # noqa: F811
    pid, _ = pipeline
    workspace.jobs_by_id.clear()
    r = refresh(client, acct, pid)
    assert r.status_code == 502 and "does not exist" in r.json()["detail"]


# --- reading -----------------------------------------------------------------------------

def test_runs_are_listed_newest_first_with_paging(client, acct, workspace, pipeline):  # noqa: F811
    pid, job_id = pipeline
    ids = [workspace.add_scheduled_run(job_id) for _ in range(3)]
    refresh(client, acct, pid)
    page1 = runs(client, acct, pid, limit=2).json()
    page2 = runs(client, acct, pid, limit=2, offset=2).json()
    assert [x["databricks_run_id"] for x in page1 + page2] == sorted(ids, reverse=True)  # newest first
    assert {x["databricks_run_id"] for x in page1 + page2} == set(ids)
    assert runs(client, acct, pid, limit=0).status_code == 422
    assert runs(client, acct, pid, limit=201).status_code == 422


def test_org_wide_runs_can_be_filtered_by_status(client, acct, workspace, pipeline):  # noqa: F811
    pid, job_id = pipeline
    workspace.add_scheduled_run(job_id)
    workspace.add_scheduled_run(job_id, result=Result.FAILED, message="bad")
    refresh(client, acct, pid)
    everything = client.get("/runs", headers=acct.headers).json()
    failed = client.get("/runs", params={"status": "failed"}, headers=acct.headers).json()
    assert len(everything) == 2 and [r["status"] for r in failed] == ["failed"]
    assert failed[0]["job_name"] == "customers_daily"
    assert client.get("/runs", params={"status": "exploded"}, headers=acct.headers).status_code == 422


def test_the_pipeline_shows_its_latest_run(client, acct, workspace, pipeline):  # noqa: F811
    pid, job_id = pipeline
    assert client.get(f"/pipelines/{pid}", headers=acct.headers).json()["last_run"] is None
    workspace.add_scheduled_run(job_id)
    newest = workspace.add_scheduled_run(job_id, result=Result.FAILED, message="bad")
    refresh(client, acct, pid)
    got = client.get(f"/pipelines/{pid}", headers=acct.headers).json()
    assert got["last_run"]["databricks_run_id"] == newest and got["last_run"]["status"] == "failed"
    listed = client.get("/pipelines", headers=acct.headers).json()
    assert listed[0]["last_run"]["id"] == got["last_run"]["id"]


# --- isolation and safety ----------------------------------------------------------------

def test_another_org_cannot_see_or_touch_the_runs(client, acct, make_account, workspace, pipeline):  # noqa: F811
    pid, job_id = pipeline
    workspace.add_scheduled_run(job_id)
    refresh(client, acct, pid)
    other = make_account("b@b.com", "B")
    connect(client, other)
    for r in (run_now(client, other, pid), refresh(client, other, pid), runs(client, other, pid)):
        assert r.status_code == 404
    assert client.get("/runs", headers=other.headers).json() == []
    assert len(workspace.runs) == 0


def test_runs_need_a_connected_workspace(client, acct, workspace, pipeline):  # noqa: F811
    pid, _ = pipeline
    client.delete("/org/connection", headers=acct.headers)
    r = run_now(client, acct, pid)
    assert r.status_code == 409 and "Connect your Databricks workspace" in r.json()["detail"]
    assert refresh(client, acct, pid).status_code == 409
    assert runs(client, acct, pid).status_code == 200  # reading our own record needs no connection


def test_a_job_id_is_never_run_in_a_different_workspace(client, acct, workspace, pipeline):  # noqa: F811
    pid, _ = pipeline
    assert connect(client, acct, host="fake-2.cloud.databricks.com", token="second-token-value-99").status_code == 200
    for r in (run_now(client, acct, pid), refresh(client, acct, pid)):
        assert r.status_code == 409 and "fake-1" in r.json()["detail"]
    assert workspace.runs == []


def test_deleting_a_pipeline_deletes_its_runs(client, acct, workspace, pipeline, session_factory):  # noqa: F811
    pid, job_id = pipeline
    run_now(client, acct, pid)
    workspace.add_scheduled_run(job_id)
    refresh(client, acct, pid)
    assert run_count(session_factory) == 2
    assert client.delete(f"/pipelines/{pid}", headers=acct.headers).status_code == 204
    assert run_count(session_factory) == 0
    assert client.get("/runs", headers=acct.headers).json() == []


# --- the schema change -------------------------------------------------------------------

def test_migration_004_upgrades_an_existing_database_and_stops_duplicate_runs(scratch_database):
    from tests.conftest import schema_files

    files = schema_files()
    assert files[3].name == "004_pipeline_run_tracking.sql"

    with scratch_database.begin() as conn:  # the database as it was before 1d, with a run in it
        for f in files[:3]:
            conn.exec_driver_sql(f.read_text(encoding="utf-8"))
        conn.execute(text("INSERT INTO orgs (name) VALUES ('Old Co')"))
        conn.execute(text("INSERT INTO pipelines (org_id, job_name, catalog, target_schema) SELECT id, 'j', 'main', 'b' FROM orgs"))
        conn.execute(text("INSERT INTO pipeline_runs (pipeline_id, databricks_run_id) SELECT id, 7 FROM pipelines"))

    for _ in range(2):  # apply, then apply AGAIN — must be harmless
        with scratch_database.begin() as conn:
            conn.exec_driver_sql(files[3].read_text(encoding="utf-8"))

    with scratch_database.connect() as conn:
        row = conn.execute(text("SELECT databricks_run_id, databricks_run_url, created_at IS NOT NULL FROM pipeline_runs")).one()
        assert tuple(row) == (7, None, True)  # existing run survived
    with pytest.raises(IntegrityError):  # the same Databricks run can't be recorded twice
        with scratch_database.begin() as conn:
            conn.execute(text("INSERT INTO pipeline_runs (pipeline_id, databricks_run_id) SELECT id, 7 FROM pipelines"))
    with scratch_database.begin() as conn:  # runs without a Databricks id are not constrained
        conn.execute(text("INSERT INTO pipeline_runs (pipeline_id) SELECT id FROM pipelines"))
        conn.execute(text("INSERT INTO pipeline_runs (pipeline_id) SELECT id FROM pipelines"))
