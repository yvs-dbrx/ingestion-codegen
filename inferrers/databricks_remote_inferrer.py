"""
databricks_remote_inferrer.py

Runs the SAME pandas-based schema discovery as the local CSV/JSON
inferrers, but executes it on the client's own Databricks cluster instead
of this machine. This replaces an earlier SQL/read_files()-based
approach — same end result (a list of ColumnSchema), far less machinery:
no SQL warehouse to manage, no separate SQL-type-name translation table.
Nullability and dtype detection reuse map_pandas_dtype() from
schema_types.py, the exact same function the local inferrers use, so
behavior is consistent regardless of where the file lives.

How it works:
  1. Render a small "probe" notebook (templates/_schema_probe family) —
     just enough pandas code to read a sample and print its schema.
  2. Upload it to the workspace.
  3. Submit it as a ONE-TIME run (jobs.submit(), not jobs.create()) — this
     deliberately does NOT create a persistent job, so it never shows up
     in the client's Workflows list next to their real pipelines.
  4. Wait for it to finish and read the schema back via
     dbutils.notebook.exit() / get_run_output().
  5. Delete the probe notebook — it was scaffolding, not something that
     should linger in their workspace.

NOT YET LIVE-TESTED against a real workspace (no network access in the
environment this was built in). The jobs.submit()/get_run_output() call
shapes below are reviewed against current databricks-sdk docs but are
more likely than the earlier deploy_and_schedule() code to need a small
adjustment on first real run — treat this explicitly as the thing to
verify, not assume.
"""

import json
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from schema_types import ColumnSchema, map_pandas_dtype
from inferrers.base import SchemaInferrer

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

PROBE_TEMPLATES = {
    "csv": "csv_probe.py.jinja2",
    "json": "json_probe.py.jinja2",
}


class DatabricksRemoteInferrer(SchemaInferrer):
    source_type = "databricks_remote"

    def infer(self, config: dict) -> list[ColumnSchema]:
        # Imported here, not at module level — same reasoning as before:
        # local-only testing shouldn't require databricks-sdk installed.
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service import jobs as jobs_api
        from databricks.sdk.service.workspace import ImportFormat, Language

        file_format = config["format"]
        source_path = config["file_path"]
        sample_rows = config.get("sample_rows", 1000)
        existing_cluster_id = config.get("existing_cluster_id")  # None = serverless
        probe_path = config.get(
            "probe_workspace_path",
            f"/Workspace/Shared/ingestion_codegen_probes/probe_{int(time.time())}",
        )

        if file_format not in PROBE_TEMPLATES:
            raise ValueError(
                f"No schema probe template for format='{file_format}'. "
                f"Available: {list(PROBE_TEMPLATES.keys())}"
            )

        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        template = env.get_template(PROBE_TEMPLATES[file_format])
        rendered = template.render(
            source_path=source_path,
            sample_rows=sample_rows,
            delimiter=config.get("delimiter", ","),
            has_header=config.get("has_header", True),
            multiline=config.get("multiline", False),
        )

        w = WorkspaceClient()

        parent_dir = probe_path.rsplit("/", 1)[0]
        w.workspace.mkdirs(parent_dir)

        w.workspace.upload(
            path=probe_path,
            content=rendered.encode(),
            format=ImportFormat.SOURCE,
            language=Language.PYTHON,
            overwrite=True,
        )
        print(f"Uploaded schema probe to {probe_path}")

        task = jobs_api.SubmitTask(
            task_key="schema_probe",
            notebook_task=jobs_api.NotebookTask(notebook_path=probe_path),
            timeout_seconds=600,
        )
        if existing_cluster_id:
            task.existing_cluster_id = existing_cluster_id

        print("Submitting one-time probe run — this is NOT a persistent job.")
        waiter = w.jobs.submit(run_name="schema-discovery-probe", tasks=[task])
        run = waiter.result()  # blocks until the run reaches a terminal state
        print(f"Probe run finished with state: {run.state.result_state}")

        probe_run_id = run.tasks[0].run_id
        output = w.jobs.get_run_output(run_id=probe_run_id)
        if output.error:
            raise RuntimeError(f"Schema probe failed: {output.error}")

        raw_columns = json.loads(output.notebook_output.result)

        columns = []
        for col in raw_columns:
            spark_type, sql_type = map_pandas_dtype(col["pandas_dtype"])
            columns.append(
                ColumnSchema(
                    name=col["name"],
                    pandas_dtype=col["pandas_dtype"],
                    spark_type=spark_type,
                    sql_type=sql_type,
                    nullable=col["nullable"],
                )
            )

        try:
            w.workspace.delete(probe_path)
        except Exception:
            pass  # cleanup best-effort — a leftover probe file isn't worth failing over

        return columns
