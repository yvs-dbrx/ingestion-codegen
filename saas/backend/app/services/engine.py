"""The one place the backend touches the codegen engine (codegen.py, databricks_deploy.py and
friends — mounted at /engine, the same code the CLI and Streamlit app use).

Guarantees this adapter provides on top of the engine:
  * Discovery is ALWAYS remote (location='databricks_workspace'). The engine also has a local
    mode that opens files on the machine it runs on; a hosted service must never offer that —
    a tenant could otherwise point it at the server's own files. There is no way to select it.
  * Every Databricks call uses the client handed in (the org's own credentials), never the
    server's environment.
  * Engine failures are sorted into "your input is wrong" (422) and "Databricks/the probe
    failed" (502), with message length capped.
"""
import hashlib
import itertools
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import xmlschema

import codegen
import databricks_deploy
from schema_types import ColumnSchema, TableSpec

from app.services.codegen_safety import SafeColumn


class EngineInputError(Exception):
    """Discovery/generation failed because of what was asked for (bad path, wrong row tag, ...)."""


class EngineRemoteError(Exception):
    """Databricks (or the probe job running there) failed."""


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (ValueError, xmlschema.XMLSchemaException) as exc:
        raise EngineInputError(str(exc)[:500]) from exc
    except Exception as exc:
        raise EngineRemoteError(f"{type(exc).__name__}: {str(exc)[:500]}") from exc


# --- discovery ---------------------------------------------------------------------------

@dataclass
class DiscoveredTable:
    table_name: str  # suggested; the user can change it when building a pipeline
    file_name: str
    source_path: str
    columns: list[dict]


def _column_dicts(columns: list[ColumnSchema]) -> list[dict]:
    return [
        {
            "name": c.name,
            "sql_type": c.sql_type,
            "spark_type": c.spark_type,
            "pandas_dtype": c.pandas_dtype,
            "nullable": c.nullable,
            "struct_path": c.struct_path,
        }
        for c in columns
    ]


def _default_table_name(path: str) -> str:
    return codegen.sanitize_table_name(Path(path.rsplit("/", 1)[-1]).stem)


def discover(
    client,
    *,
    source_type: str,
    mode: str,
    source_path: str,
    schema_definition_path: str | None,
    delimiter: str,
    has_header: bool,
    multiline: bool,
    row_tag: str,
    is_record_sequence: bool,
    is_text: bool,
    cluster_id: str | None,
) -> list[DiscoveredTable]:
    if source_type in ("csv", "json"):
        config = {
            "file_path": source_path,
            "location": "databricks_workspace",
            "delimiter": delimiter,
            "has_header": has_header,
            "multiline": multiline,
            "workspace_client": client,
        }
        if cluster_id:
            config["existing_cluster_id"] = cluster_id
        if mode == "single":
            columns = _run(codegen.infer_schema, source_type, config)
            return [
                DiscoveredTable(_default_table_name(source_path), source_path.rsplit("/", 1)[-1], source_path, _column_dicts(columns))
            ]
        tables = _run(codegen.infer_multi_schema_remote, source_type, config)
        return [DiscoveredTable(t.table, t.file_name, t.source_path, _column_dicts(t.columns)) for t in tables]

    if mode != "single":
        raise EngineInputError(f"Multi-file ingestion isn't supported for {source_type} sources")

    if source_type == "xml":
        if not schema_definition_path:
            raise EngineInputError(
                "XML discovery here works from an XSD — provide schema_definition_path. "
                "(Sampling an XML data file remotely isn't supported yet.)"
            )
        columns = _run(
            codegen.infer_schema_from_xsd,
            schema_definition_path,
            row_tag,
            location="databricks_workspace",
            workspace_client=client,
        )
        return [DiscoveredTable(_default_table_name(source_path), source_path.rsplit("/", 1)[-1], source_path, _column_dicts(columns))]

    if source_type == "cobol":
        if not schema_definition_path:
            raise EngineInputError("COBOL discovery needs the copybook — provide schema_definition_path")
        if not cluster_id:
            raise EngineInputError(
                "COBOL discovery needs cluster_id: an existing cluster with the Cobrix library attached"
            )
        columns = _run(
            codegen.infer_cobol_schema,
            schema_definition_path,
            source_path,
            cluster_id,
            is_record_sequence,
            is_text,
            workspace_client=client,
        )
        return [DiscoveredTable(_default_table_name(source_path), source_path.rsplit("/", 1)[-1], source_path, _column_dicts(columns))]

    raise EngineInputError(f"Unsupported source type: {source_type}")


# --- generation --------------------------------------------------------------------------

@dataclass
class GenTable:
    table_name: str
    file_name: str
    source_path: str
    columns: list[SafeColumn]


def _schema_columns(columns: list[SafeColumn]) -> list[ColumnSchema]:
    return [
        ColumnSchema(
            name=c.name,
            pandas_dtype="",
            spark_type=c.spark_type,
            sql_type=c.sql_type,
            nullable=c.nullable,
            struct_path=c.struct_path,
        )
        for c in columns
    ]


def generate_notebook(
    *,
    source_type: str,
    mode: str,
    tables: list[GenTable],
    catalog: str,
    target_schema: str,
    settings: dict,
    out_dir: Path,
) -> tuple[Path, str]:
    """Renders the notebook with the engine's own templates. Returns (file, text).

    `settings` holds only values already checked by codegen_safety: folder_path (multi),
    delimiter, has_header, multiline, row_tag, copybook_path, is_record_sequence, is_text.
    """
    out = out_dir / "notebook.py"
    source_config = {
        "location": "databricks_workspace",
        "delimiter": settings["delimiter"],
        "has_header": settings["has_header"],
        "multiline": settings["multiline"],
        "row_tag": settings["row_tag"],
    }

    if source_type == "cobol":
        t = tables[0]
        _run(
            codegen.render_cobol_notebook,
            copybook_path=settings["copybook_path"],
            source_path=t.source_path,
            catalog=catalog,
            target_schema=target_schema,
            table=t.table_name,
            columns=_schema_columns(t.columns),
            output_path=str(out),
            is_record_sequence=settings["is_record_sequence"],
            is_text=settings["is_text"],
        )
    elif mode == "multi":
        _run(
            codegen.render_multi_notebook,
            source_type,
            {**source_config, "file_path": settings["folder_path"]},
            catalog,
            target_schema,
            [TableSpec(table=t.table_name, file_name=t.file_name, source_path=t.source_path, columns=_schema_columns(t.columns)) for t in tables],
            str(out),
        )
    else:
        t = tables[0]
        _run(
            codegen.render_notebook,
            source_type,
            {**source_config, "file_path": t.source_path},
            catalog,
            target_schema,
            t.table_name,
            _schema_columns(t.columns),
            str(out),
        )

    text = out.read_text(encoding="utf-8")
    return out, text


def generate_bronze_notebook(*, tables: list[dict], out_dir: Path) -> tuple[Path, str]:
    """Renders the Bronze (distinct records) notebook. `tables` is
    [{"raw", "bronze", "name", "columns": [(name, sql_type), ...]}] — every value already validated."""
    out = out_dir / "notebook.py"
    _run(codegen.render_bronze_notebook, tables, str(out))
    return out, out.read_text(encoding="utf-8")


def generate_silver_notebook(*, spec: dict, out_dir: Path) -> tuple[Path, str]:
    """Renders the Silver notebook from a fully validated spec (see codegen.render_silver_notebook)."""
    out = out_dir / "notebook.py"
    _run(codegen.render_silver_notebook, spec, str(out))
    return out, out.read_text(encoding="utf-8")


def notebook_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- Databricks operations ---------------------------------------------------------------

def build_cron(frequency: str, time_of_day: str, day_of_week: str) -> str:
    return _run(databricks_deploy.build_cron_expression, frequency, time_of_day, day_of_week)


def deploy(client, **kwargs) -> dict:
    """Uploads the notebook and creates the scheduled job (engine's deploy_and_schedule)."""
    return _run(databricks_deploy.deploy_and_schedule, workspace_client=client, **kwargs)


def job_name_taken(client, job_name: str) -> bool:
    """True if the workspace already has a job with this name. deploy_and_schedule would silently
    RESET (overwrite) such a job — fine for the CLI where you own the workspace's names, but a
    hosted product must never overwrite a job it didn't create."""
    return bool(_run(lambda: list(client.jobs.list(name=job_name))))


def workspace_path_taken(client, path: str) -> bool:
    """True if something already exists at this workspace path. The engine uploads with
    overwrite=True, which would replace a notebook we didn't create."""
    from databricks.sdk.errors import NotFound

    try:
        client.workspace.get_status(path)
    except NotFound:
        return False
    except Exception as exc:
        raise EngineRemoteError(f"{type(exc).__name__}: {str(exc)[:500]}") from exc
    return True


def delete_notebook(client, path: str) -> None:
    """Best-effort cleanup of an uploaded notebook; failure here never blocks anything."""
    try:
        client.workspace.delete(path)
    except Exception:
        pass


def delete_job(client, job_id: int) -> None:
    """Deletes the Databricks job; a job that's already gone counts as deleted."""
    from databricks.sdk.errors import NotFound

    try:
        client.jobs.delete(job_id=job_id)
    except NotFound:
        return
    except Exception as exc:
        raise EngineRemoteError(f"{type(exc).__name__}: {str(exc)[:500]}") from exc


def table_columns(client, full_name: str) -> list[dict] | None:
    """The columns of a Unity Catalog table as [{"name", "type"}], or None if there is no such table."""
    from databricks.sdk.errors import NotFound

    try:
        info = client.tables.get(full_name=full_name)
    except NotFound:
        return None
    except Exception as exc:
        raise EngineRemoteError(f"{type(exc).__name__}: {str(exc)[:500]}") from exc
    return [{"name": str(c.name), "type": str(c.type_text or "")} for c in (info.columns or [])]


def verify_connection(client) -> None:
    """Cheapest authenticated call — proves the host + token work before we store them."""
    _run(lambda: client.current_user.me())


# --- runs --------------------------------------------------------------------------------

@dataclass
class RemoteRun:
    run_id: int
    status: str  # pending | running | success | failed | cancelled
    triggered_by: str  # schedule | manual
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    url: str | None


def _value(x):
    """SDK enums and plain strings both arrive here; either way we want the string."""
    return getattr(x, "value", x)


def map_status(life_cycle_state, result_state) -> str:
    """Databricks' two-part run state -> our five statuses. Anything we don't recognise stays
    'running' (never silently 'success'), and unfinished-looking states never claim to be done."""
    life = _value(life_cycle_state)
    result = _value(result_state)
    if life in ("PENDING", "QUEUED", "BLOCKED", "WAITING_FOR_RETRY"):
        return "pending"
    if life in ("RUNNING", "TERMINATING"):
        return "running"
    if life == "SKIPPED":
        return "cancelled"
    if life == "INTERNAL_ERROR":
        return "failed"
    if life == "TERMINATED":
        if result == "SUCCESS":
            return "success"
        if result in ("CANCELED", "EXCLUDED", "DISABLED"):
            return "cancelled"
        return "failed"  # FAILED, TIMEDOUT, SUCCESS_WITH_FAILURES, UPSTREAM_*, ...
    return "running"


def _ms_to_datetime(ms) -> datetime | None:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc) if ms else None


def run_now(client, job_id: int) -> int:
    """Starts the job once, right now, and returns the Databricks run id."""
    return _run(lambda: client.jobs.run_now(job_id=job_id).run_id)


def list_runs(client, job_id: int, limit: int = 25) -> list[RemoteRun]:
    """The job's most recent runs, newest first."""

    def fetch():
        out = []
        for r in itertools.islice(client.jobs.list_runs(job_id=job_id, limit=limit, expand_tasks=False), limit):
            state = r.state
            status = map_status(getattr(state, "life_cycle_state", None), getattr(state, "result_state", None))
            message = (getattr(state, "state_message", None) or "").strip()
            out.append(
                RemoteRun(
                    run_id=r.run_id,
                    status=status,
                    triggered_by="schedule" if _value(getattr(r, "trigger", None)) == "PERIODIC" else "manual",
                    started_at=_ms_to_datetime(getattr(r, "start_time", None)),
                    finished_at=_ms_to_datetime(getattr(r, "end_time", None)) if status in ("success", "failed", "cancelled") else None,
                    error_message=message[:2000] if status == "failed" and message else None,
                    url=getattr(r, "run_page_url", None),
                )
            )
        return out

    return _run(fetch)


MAX_OUTPUT_CHARS = 200_000


def run_output(client, run_id: int) -> dict | None:
    """What the run's notebook handed back through dbutils.notebook.exit(), parsed as JSON.

    Returns None when the run finished but reported nothing usable (an older notebook, or output that
    isn't JSON) — a definitive answer that won't change. Raises EngineRemoteError when Databricks
    couldn't be asked, which may well work next time.

    A job with one task is read through that task's own run id, so look it up first.
    """

    def fetch():
        run = client.jobs.get_run(run_id=run_id)
        tasks = list(getattr(run, "tasks", None) or [])
        out = client.jobs.get_run_output(run_id=tasks[0].run_id if tasks else run_id)
        notebook_output = getattr(out, "notebook_output", None)
        return getattr(notebook_output, "result", None)

    raw = _run(fetch)
    if not raw or len(raw) > MAX_OUTPUT_CHARS:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
