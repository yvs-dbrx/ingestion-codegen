"""
databricks_deploy.py

Takes an already-generated, already-reviewed local notebook file and:
  1. Uploads it into the target Databricks workspace.
  2. Creates (or updates, if a job with this name already exists) a Job
     that runs it on a schedule.

Deliberately does NOT regenerate or modify the notebook — deployment is a
separate step from codegen, matching the "generate once, redeploy only on
config/schema change" principle from PRD Section 7. Running this twice
against the same job_name updates the existing job rather than creating a
duplicate.

Auth: uses databricks-sdk's default auth resolution — it automatically
picks up DATABRICKS_HOST / DATABRICKS_TOKEN environment variables, or a
profile in ~/.databrickscfg, so credentials don't need to be passed as
CLI arguments (which would otherwise land in shell history). See the
README for setup.

Compute: defaults to serverless (no cluster config needed at all — that's
Databricks' own recommended default for notebook tasks as of 2026). Pass
an existing_cluster_id if you specifically want classic compute instead.
"""

from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import jobs as jobs_api
from databricks.sdk.service.workspace import ImportFormat, Language


def build_cron_expression(frequency: str, time_str: str, day_of_week: str = "MON") -> str:
    """
    Converts a simple frequency choice into a Databricks Quartz cron
    expression, so callers don't need to hand-write cron syntax — mirrors
    the "frequency picker" UI concept from the PRD rather than exposing
    raw cron.
    """
    hour, minute = (int(p) for p in time_str.split(":"))

    if frequency == "hourly":
        return f"0 {minute} * * * ?"
    elif frequency == "daily":
        return f"0 {minute} {hour} * * ?"
    elif frequency == "weekly":
        return f"0 {minute} {hour} ? * {day_of_week}"
    else:
        raise ValueError(f"Unsupported frequency '{frequency}' (use hourly/daily/weekly)")


def deploy_and_schedule(
    local_notebook_path: str,
    workspace_path: str,
    job_name: str,
    source_path_param: str,
    cron_expression: str,
    timezone: str = "UTC",
    existing_cluster_id: str | None = None,
    run_now: bool = False,
    extra_parameters: dict | None = None,
) -> dict:
    w = WorkspaceClient()  # auth resolved from env vars / ~/.databrickscfg

    # --- 1. Upload the notebook ---
    parent_dir = workspace_path.rsplit("/", 1)[0]
    w.workspace.mkdirs(parent_dir)

    content = Path(local_notebook_path).read_bytes()
    w.workspace.upload(
        path=workspace_path,
        content=content,
        format=ImportFormat.SOURCE,
        language=Language.PYTHON,
        overwrite=True,
    )
    print(f"Uploaded notebook to workspace path: {workspace_path}")

    # --- 2. Build the task ---
    base_parameters = {"source_path": source_path_param, **(extra_parameters or {})}
    task = jobs_api.Task(
        task_key="ingest",
        notebook_task=jobs_api.NotebookTask(
            notebook_path=workspace_path,
            base_parameters=base_parameters,
        ),
        timeout_seconds=3600,
    )
    if existing_cluster_id:
        task.existing_cluster_id = existing_cluster_id
    # else: no cluster config at all = serverless, Databricks' recommended default

    schedule = jobs_api.CronSchedule(
        quartz_cron_expression=cron_expression,
        timezone_id=timezone,
        pause_status=jobs_api.PauseStatus.UNPAUSED,
    )

    # --- 3. Create or update the job (idempotent by name) ---
    existing = list(w.jobs.list(name=job_name))
    if existing:
        job_id = existing[0].job_id
        w.jobs.reset(
            job_id=job_id,
            new_settings=jobs_api.JobSettings(name=job_name, tasks=[task], schedule=schedule),
        )
        print(f"Updated existing job '{job_name}' (job_id={job_id})")
    else:
        created = w.jobs.create(name=job_name, tasks=[task], schedule=schedule)
        job_id = created.job_id
        print(f"Created new job '{job_name}' (job_id={job_id})")

    result = {"job_id": job_id, "workspace_path": workspace_path}

    # --- 4. Optionally trigger an immediate run, useful for validating deployment ---
    if run_now:
        run = w.jobs.run_now(job_id=job_id)
        print(f"Triggered run_id={run.run_id} — check status in the Databricks Jobs UI")
        result["run_id"] = run.run_id

    host = w.config.host
    result["job_url"] = f"{host}/jobs/{job_id}"
    print(f"View the job here: {result['job_url']}")

    return result
