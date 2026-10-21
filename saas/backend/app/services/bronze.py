"""Bronze: one copy of each distinct record, built automatically from Raw tables.

Nothing here is configured by the user beyond which Raw tables to process and where to put the
results — what counts as "the same record" is fixed by the table's own columns (every source column,
never the lineage columns), so there is no business key to choose and nothing to get wrong.

A Bronze notebook is generated from the Raw table's recorded column list, validated here exactly like
every other value that ends up in generated code.
"""
import re
import tempfile
import uuid
from datetime import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataAsset, Pipeline
from app.schemas import BronzeDeployIn, BronzePipelineIn
from app.services import assets as assets_svc
from app.services import codegen_safety as safety
from app.services import engine
from app.services.databricks_client import ClientBuilder
from app.services.errors import Conflict, Unprocessable
from app.services.pipelines import Plan, deploy_plan, workspace_path_for

_PROBLEMS_TITLE = "This Bronze pipeline can't be built"


def build_bronze_plan(db: Session, org_id: uuid.UUID, body: BronzePipelineIn, *, lock: bool = False) -> Plan:
    problems: list[str] = []

    ids = [i.asset_id for i in body.inputs]
    names = [i.table_name.lower() for i in body.inputs]
    if len(set(ids)) != len(ids):
        problems.append("the same Raw table can't appear twice in one pipeline")
    if len(set(names)) != len(names):
        problems.append("Bronze table names must be unique within a pipeline (case-insensitive)")
    if problems:
        raise Unprocessable(_PROBLEMS_TITLE, problems=problems)

    assets = assets_svc.load_assets(db, org_id, ids, lock)
    raws = [assets[i] for i in ids]

    safety.collect(problems, safety.check_identifier, body.catalog, "catalog")
    safety.collect(problems, safety.check_identifier, body.target_schema, "target schema")
    safety.collect(problems, safety.check_job_name, body.job_name)
    safety.collect(problems, safety.check_schedule, body.schedule.frequency, body.schedule.time, body.schedule.day_of_week, body.schedule.timezone)
    if body.cluster_id:
        safety.collect(problems, safety.check_cluster_id, body.cluster_id)

    entries: list[tuple[DataAsset, str, list[safety.SafeColumn]]] = []
    for raw, requested in zip(raws, body.inputs):
        name = requested.table_name
        safety.collect(problems, safety.check_identifier, name, "table name")
        label = f"{raw.catalog}.{raw.schema_name}.{raw.table_name}"
        if raw.layer != "raw":
            problems.append(f"{label} is a {raw.layer} table; only Raw tables can be turned into Bronze")
            continue
        if (raw.catalog, raw.schema_name, raw.table_name) == (body.catalog, body.target_schema, name):
            problems.append(f"{label}: the Bronze table can't be the Raw table itself — choose another schema or name")
        if not raw.columns:
            problems.append(f"{label} has no column list on record — redeploy the pipeline that loads it, then try again")
            continue
        # Re-validate what was stored: the registry is not trusted just because we wrote it.
        # "cobol" is only the most permissive rule set (it allows DECIMAL); a Raw table can have those.
        columns = safety.collect(problems, safety.check_columns, list(raw.columns), source_type="cobol")
        if columns:
            entries.append((raw, name, columns))

    workspace_path = workspace_path_for(org_id, body.job_name)
    safety.collect(problems, safety.check_workspace_path, workspace_path)
    if problems:
        raise Unprocessable(_PROBLEMS_TITLE, problems=problems)

    for _, name, _ in entries:
        existing = db.scalar(
            select(DataAsset).where(
                DataAsset.org_id == org_id,
                DataAsset.catalog == body.catalog,
                DataAsset.schema_name == body.target_schema,
                DataAsset.table_name == name,
            )
        )
        if existing is not None and existing.layer != "bronze":
            raise Conflict(
                f"{body.catalog}.{body.target_schema}.{name} is already registered as a {existing.layer} table — "
                "choose another name"
            )

    try:
        hour, minute = (int(p) for p in body.schedule.time.split(":"))
        cron = engine.build_cron(body.schedule.frequency, body.schedule.time, body.schedule.day_of_week)
        render = [
            {
                "raw": f"{raw.catalog}.{raw.schema_name}.{raw.table_name}",
                "bronze": f"{body.catalog}.{body.target_schema}.{name}",
                "name": name,
                "columns": [(c.name, c.sql_type) for c in columns],
            }
            for raw, name, columns in entries
        ]
        with tempfile.TemporaryDirectory() as tmp:
            _, notebook = engine.generate_bronze_notebook(tables=render, out_dir=Path(tmp))
    except (engine.EngineInputError, engine.EngineRemoteError) as exc:
        raise Unprocessable(f"The notebook couldn't be generated: {exc}")

    def register(db: Session, pipeline: Pipeline) -> None:
        assets_svc.register_bronze_assets(db, pipeline=pipeline, inputs=entries)

    return Plan(
        source_type="table",
        mode="multi" if len(entries) > 1 else "single",
        links=[],
        notebook=notebook,
        notebook_sha256=engine.notebook_sha256(notebook),
        workspace_path=workspace_path,
        cron=cron,
        source_path_param="",  # reads tables, not a file: no source_path job parameter
        extra_parameters={},
        schedule_time=time(hour, minute),
        layer="bronze",
        spec={"inputs": [{"asset_id": str(raw.id), "raw": r["raw"], "bronze": r["bronze"], "table_name": name}
                         for (raw, name, _), r in zip(entries, render)]},
        register=register,
    )


def deploy_bronze(
    db: Session, *, org_id: uuid.UUID, user_id: uuid.UUID, body: BronzeDeployIn, client_builder: ClientBuilder
) -> Pipeline:
    return deploy_plan(
        db,
        org_id=org_id,
        user_id=user_id,
        body=body,
        client_builder=client_builder,
        build=lambda lock: build_bronze_plan(db, org_id, body, lock=lock),
    )
