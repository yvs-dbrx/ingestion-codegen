"""A stand-in for a customer's Databricks workspace.

It implements only the handful of SDK calls the engine makes, and records what happened so tests
can assert on what was (and wasn't) created. The REAL engine code (databricks_deploy.py) runs
against it — only the network is faked.
"""
import itertools
import time
from types import SimpleNamespace

from databricks.sdk.errors import NotFound
from databricks.sdk.service.jobs import RunLifeCycleState, RunResultState, TriggerType


class FakeWorkspace:
    def __init__(self, host: str = "https://fake-1.cloud.databricks.com"):
        self.config = SimpleNamespace(host=host)
        self.notebooks: dict[str, bytes] = {}
        self.jobs_by_id: dict[int, dict] = {}
        self.resets: list[int] = []  # job ids the engine RESET (overwrote) — should stay empty
        self.runs: list[int] = []  # run ids started through run_now
        self.remote_runs: dict[int, dict] = {}  # everything Databricks "knows" about, by run id
        self.fail_run_now = False
        self.fail_list_runs = False
        self.run_delay = 0.0  # seconds run_now takes — widens race windows
        self.rejected_tokens: set[str] = set()
        self.builds: list[tuple[str, str]] = []  # (host, token) each time the backend built a client
        self.fail_create = False
        self.fail_delete_job = False
        self.delay = 0.0  # seconds each job creation takes — widens race windows in concurrency tests
        self._ids = itertools.count(100)
        self.current_user = SimpleNamespace(me=self._me)
        self.workspace = SimpleNamespace(
            mkdirs=lambda path: None,
            upload=self._upload,
            get_status=self._get_status,
            delete=self._delete_notebook,
        )
        self.jobs = SimpleNamespace(
            list=self._list_jobs,
            create=self._create_job,
            reset=self._reset_job,
            run_now=self._run_now,
            list_runs=self._list_runs,
            delete=self._delete_job,
        )
        self._token = ""

    # The backend's client builder calls this: (host, token) -> client
    def build(self, host: str, token: str) -> "FakeWorkspace":
        self.builds.append((host, token))
        self.config.host = host
        self._token = token
        return self

    def _me(self):
        if self._token in self.rejected_tokens:
            raise PermissionError("401 invalid token")
        return SimpleNamespace(user_name="someone")

    def _upload(self, path, content, format=None, language=None, overwrite=False):
        if path in self.notebooks and not overwrite:
            raise RuntimeError("already exists")
        self.notebooks[path] = content

    def _get_status(self, path):
        if path not in self.notebooks:
            raise NotFound(f"{path} does not exist")
        return SimpleNamespace(path=path)

    def _delete_notebook(self, path, recursive=False):
        self.notebooks.pop(path, None)

    def _list_jobs(self, name=None):
        return iter(
            [SimpleNamespace(job_id=i, name=j["name"]) for i, j in self.jobs_by_id.items() if name is None or j["name"] == name]
        )

    def _create_job(self, name, tasks, schedule):
        time.sleep(self.delay)
        if self.fail_create:
            raise RuntimeError("Databricks is having a bad day")
        job_id = next(self._ids)
        self.jobs_by_id[job_id] = {"name": name, "tasks": tasks, "schedule": schedule}
        return SimpleNamespace(job_id=job_id)

    def _reset_job(self, job_id, new_settings):
        self.resets.append(job_id)
        self.jobs_by_id[job_id] = {"name": new_settings.name, "tasks": new_settings.tasks, "schedule": new_settings.schedule}

    def _run_now(self, job_id):
        time.sleep(self.run_delay)
        if self.fail_run_now:
            raise RuntimeError("Databricks is having a bad day")
        run_id = next(self._ids)
        self.runs.append(run_id)
        self._add_run(run_id, job_id, TriggerType.ONE_TIME, RunLifeCycleState.RUNNING, None, "")
        return SimpleNamespace(run_id=run_id)

    def _add_run(self, run_id, job_id, trigger, life, result, message):
        self.remote_runs[run_id] = {
            "job_id": job_id, "trigger": trigger, "life": life, "result": result, "message": message,
            "start": 1_790_000_000_000 + run_id * 1000, "end": None,
        }

    def _list_runs(self, job_id=None, limit=None, expand_tasks=None):
        if self.fail_list_runs:
            raise RuntimeError("Databricks is having a bad day")
        if job_id not in self.jobs_by_id:
            raise NotFound(f"job {job_id} does not exist")
        rows = sorted((i for i, r in self.remote_runs.items() if r["job_id"] == job_id), reverse=True)  # newest first
        return iter(
            SimpleNamespace(
                run_id=i,
                state=SimpleNamespace(
                    life_cycle_state=self.remote_runs[i]["life"],
                    result_state=self.remote_runs[i]["result"],
                    state_message=self.remote_runs[i]["message"],
                ),
                start_time=self.remote_runs[i]["start"],
                end_time=self.remote_runs[i]["end"],
                trigger=self.remote_runs[i]["trigger"],
                run_page_url=f"{self.config.host}/jobs/{job_id}/runs/{i}",
            )
            for i in rows
        )

    def _delete_job(self, job_id):
        if self.fail_delete_job:
            raise RuntimeError("Databricks is having a bad day")
        if job_id not in self.jobs_by_id:
            raise NotFound(f"job {job_id} does not exist")
        del self.jobs_by_id[job_id]

    # --- helpers for tests --------------------------------------------------------------
    def finish_run(self, run_id: int, result=RunResultState.SUCCESS, message: str = "") -> None:
        r = self.remote_runs[run_id]
        r.update(life=RunLifeCycleState.TERMINATED, result=result, message=message, end=r["start"] + 60_000)

    def add_scheduled_run(self, job_id: int, life=RunLifeCycleState.TERMINATED, result=RunResultState.SUCCESS, message: str = "") -> int:
        run_id = next(self._ids)
        self._add_run(run_id, job_id, TriggerType.PERIODIC, life, result, message)
        if life == RunLifeCycleState.TERMINATED:
            self.remote_runs[run_id]["end"] = self.remote_runs[run_id]["start"] + 60_000
        return run_id

    def add_foreign_job(self, name: str) -> int:
        """A job that exists in the workspace but wasn't created by us."""
        job_id = next(self._ids)
        self.jobs_by_id[job_id] = {"name": name, "tasks": ["theirs"], "schedule": None}
        return job_id
