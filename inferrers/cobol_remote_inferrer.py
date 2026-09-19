"""
cobol_remote_inferrer.py

COBOL/mainframe schema discovery — structurally different from every
other inferrer in this project, for reasons worth being explicit about:

  - Cobrix (za.co.absa.cobrix:spark-cobol), the library that actually
    understands copybook syntax and EBCDIC/COMP-3 decoding, is a
    Java/Scala Spark data source, not a pip package. There is no local,
    pure-Python discovery path the way csv/json/xml/xsd have one — this
    is remote-only, always, regardless of a "location" choice.
  - Discovery needs an existing classic cluster with the Cobrix Maven
    library already attached, not the serverless default every other
    probe in this project uses — serverless compute isn't documented as
    supporting custom JAR attachment. existing_cluster_id is therefore
    required here, not optional.
  - Cobrix derives the schema from the copybook alone (no data rows are
    scanned for it), but Spark's DataFrameReader.load() still needs an
    actual data file path to resolve against — so discovery takes BOTH
    a copybook path and a sample data path, not just one.

Mechanically this still follows the same probe-job pattern as
databricks_remote_inferrer.py: render a small notebook, upload it,
submit as a one-time run, read the result back, delete the probe.

Live-tested against a real workspace (DBR 17.3 / Spark 4.0) — three real
issues turned up on that first run, all fixed here and in the templates:
  1. _metadata.file_path (used for the _source_file lineage column) is
     only populated for Spark's native file sources — Cobrix doesn't
     expose it. cobol_notebook.py.jinja2 now uses F.lit(source_path)
     instead.
  2. COBOL group items (level 05 with child level-10s) come back from
     Cobrix as Spark StructType columns, not flat scalars — writing that
     into a flat STRING/etc. Delta column fails with
     DELTA_FAILED_TO_MERGE_FIELDS. Both this probe and the deployed
     notebook now recursively flatten structs into their leaf fields
     (see flatten() in cobol_probe.py.jinja2 and
     codegen.build_cobol_select_columns()).
  3. Mainframe-to-Unix text transfers often add a newline after every
     fixed-length record — Cobrix's default binary mode expects exact
     byte multiples and fails with IllegalArgumentException on that
     trailing newline. There's now an is_text option (config["is_text"])
     that adds .option("is_text", "true") to the Cobrix reader for this
     case; off by default, matching Cobrix's own default.
Still not verified: OCCURS (array) fields — see flatten()'s docstring.
"""

import json
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from schema_types import ColumnSchema
from inferrers.base import SchemaInferrer

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

# The Scala suffix must match the cluster's Spark version — this bit a
# real user on first live test (DBR 17.3 / Spark 4.0, which dropped Scala
# 2.12 entirely): Spark 3.x / DBR <=16.x needs spark-cobol_2.12, Spark 4.0+
# / DBR 17.x+ needs spark-cobol_2.13. Get this wrong and the cluster still
# accepts the library but every run against it fails with a generic
# RunLifeCycleState.INTERNAL_ERROR, not a clear "wrong Scala version"
# message — worth checking first if COBOL discovery/deploy fails outright.
COBRIX_MAVEN_COORDINATES_SCALA_2_12 = "za.co.absa.cobrix:spark-cobol_2.12:2.11.0"  # Spark 3.x / DBR <=16.x
COBRIX_MAVEN_COORDINATES_SCALA_2_13 = "za.co.absa.cobrix:spark-cobol_2.13:2.11.0"  # Spark 4.0+ / DBR 17.x+


class CobolRemoteInferrer(SchemaInferrer):
    source_type = "cobol"

    def infer(self, config: dict) -> list[ColumnSchema]:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service import jobs as jobs_api
        from databricks.sdk.service.workspace import ImportFormat, Language

        copybook_path = config["copybook_path"]
        data_path = config["file_path"]
        existing_cluster_id = config.get("existing_cluster_id")
        if not existing_cluster_id:
            raise ValueError(
                "COBOL schema discovery needs existing_cluster_id — an existing classic cluster "
                f"with Cobrix already attached ({COBRIX_MAVEN_COORDINATES_SCALA_2_12} for Spark 3.x "
                f"/ DBR <=16.x, {COBRIX_MAVEN_COORDINATES_SCALA_2_13} for Spark 4.0+ / DBR 17.x+ — "
                "match your cluster's Spark version, a mismatch fails at run time with a generic "
                "error, not a clear one). Serverless compute isn't known to support attaching "
                "custom Spark data source JARs."
            )
        is_record_sequence = config.get("is_record_sequence", False)
        is_text = config.get("is_text", False)
        probe_path = config.get(
            "probe_workspace_path",
            f"/Workspace/Shared/ingestion_codegen_probes/probe_cobol_{int(time.time())}",
        )

        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        template = env.get_template("cobol_probe.py.jinja2")
        rendered = template.render(
            copybook_path=copybook_path,
            data_path=data_path,
            is_record_sequence=is_record_sequence,
            is_text=is_text,
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
        print(f"Uploaded COBOL schema probe to {probe_path}")

        task = jobs_api.SubmitTask(
            task_key="cobol_schema_probe",
            notebook_task=jobs_api.NotebookTask(notebook_path=probe_path),
            existing_cluster_id=existing_cluster_id,
            timeout_seconds=600,
        )

        print("Submitting one-time COBOL probe run — this is NOT a persistent job.")
        waiter = w.jobs.submit(run_name="schema-discovery-probe-cobol", tasks=[task])
        run = waiter.result()
        print(f"Probe run finished with state: {run.state.result_state}")

        probe_run_id = run.tasks[0].run_id
        output = w.jobs.get_run_output(run_id=probe_run_id)
        if output.error:
            raise RuntimeError(f"COBOL schema probe failed: {output.error}")

        raw_columns = json.loads(output.notebook_output.result)

        # The probe already computes both the Spark type label and a
        # DDL-ready sql_type (with DecimalType precision/scale preserved)
        # directly from the live schema Cobrix produced — nothing left to
        # re-derive here, unlike the pandas-dtype-based probes. It also
        # already flattens COBOL group items (Cobrix returns those as
        # StructType, not flat scalars) and reports each leaf's original
        # nested path in "path" — kept here as struct_path so
        # render_cobol_notebook() can rebuild the same flattening as real
        # Spark code, not just DDL.
        columns = [
            ColumnSchema(
                name=col["name"],
                pandas_dtype=f"cobol:{col['spark_type']}",
                spark_type=col["spark_type"],
                sql_type=col["sql_type"],
                nullable=col["nullable"],
                struct_path=col.get("path"),
            )
            for col in raw_columns
        ]

        try:
            w.workspace.delete(probe_path)
        except Exception:
            pass  # cleanup best-effort — a leftover probe file isn't worth failing over

        return columns
