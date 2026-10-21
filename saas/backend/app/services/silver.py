"""Silver: clean, typed, integrated entities built from one Bronze table.

What the user chooses is a *configuration*, never code: entities, their business keys, and for each
attribute a short list of steps from a closed vocabulary (trim, upper, lower, null_if, cast,
parse_date, parse_timestamp, default). All of it is validated here against allowlists and handed to
the notebook as data; the notebook is a fixed interpreter. That is what lets customers design their
own 3NF tables without any of their input ever becoming code.

Master keys come from a crosswalk table that identity resolution produced elsewhere. A source key
with no mapping receives a fixed "unknown" master key and is flagged, so nothing is dropped and the
gap stays visible (and heals itself when the crosswalk gains the mapping).
"""
import re
import tempfile
import uuid
from datetime import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataAsset, Pipeline
from app.schemas import (
    CastStep,
    DefaultStep,
    NullIfStep,
    ParseDateStep,
    ParseTimestampStep,
    SilverDeployIn,
    SilverEntityIn,
    SilverPipelineIn,
)
from app.services import assets as assets_svc
from app.services import codegen_safety as safety
from app.services import engine
from app.services.databricks_client import ClientBuilder
from app.services.errors import Conflict, Unprocessable
from app.services.pipelines import Plan, deploy_plan, workspace_path_for

_PROBLEMS_TITLE = "This Silver pipeline can't be built"
_KEY_TYPES_REFUSED = {"DOUBLE", "FLOAT", "BINARY", "BOOLEAN"}  # approximate or unorderable values make poor business keys

_INTEGER = re.compile(r"^-?\d{1,18}$")
_NUMBER = re.compile(r"^-?\d{1,20}(\.\d{1,10})?$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _default_fits(sql_type: str, value: str) -> bool:
    """Whether a default's text can be read as this type (otherwise it would quietly become NULL)."""
    if sql_type == "STRING":
        return True
    if sql_type in ("INT", "BIGINT", "SMALLINT", "TINYINT"):
        return bool(_INTEGER.match(value))
    if sql_type in ("DOUBLE", "FLOAT") or sql_type.startswith("DECIMAL("):
        return bool(_NUMBER.match(value))
    if sql_type == "BOOLEAN":
        return value.lower() in ("true", "false")
    if sql_type == "DATE":
        return bool(_ISO_DATE.match(value))
    return False  # timestamps, binary: no default


def _compile_steps(label: str, source_type: str, steps: list, problems: list[str]) -> tuple[list[dict], str]:
    """The validated steps as plain dicts, and the type the attribute ends up with."""
    compiled: list[dict] = []
    current = source_type
    for i, step in enumerate(steps, 1):
        where = f"{label}, step {i} ({step.kind})"
        if step.kind in ("trim", "upper", "lower"):
            compiled.append({"kind": step.kind})
            current = "STRING"
        elif isinstance(step, NullIfStep):
            values = [v for v in step.values if safety.collect(problems, safety.check_simple_text, v, f"{where}: value") is not None]
            compiled.append({"kind": "null_if", "values": values})
        elif isinstance(step, CastStep):
            target = safety.normalize_sql_type(step.type)
            if target is None:
                problems.append(f"{where}: {step.type!r} isn't a supported type")
                continue
            compiled.append({"kind": "cast", "type": target})
            current = target
        elif isinstance(step, ParseDateStep):
            if step.format not in safety.DATE_FORMATS:
                problems.append(f"{where}: format {step.format!r} isn't one of {', '.join(safety.DATE_FORMATS)}")
                continue
            compiled.append({"kind": "parse_date", "format": step.format})
            current = "DATE"
        elif isinstance(step, ParseTimestampStep):
            if step.format not in safety.TIMESTAMP_FORMATS:
                problems.append(f"{where}: format {step.format!r} isn't one of {', '.join(safety.TIMESTAMP_FORMATS)}")
                continue
            compiled.append({"kind": "parse_timestamp", "format": step.format})
            current = "TIMESTAMP"
        elif isinstance(step, DefaultStep):
            if safety.collect(problems, safety.check_simple_text, step.value, f"{where}: value", allow_empty=False) is None:
                continue
            if not _default_fits(current, step.value):
                problems.append(f"{where}: {step.value!r} can't be read as {current}")
                continue
            compiled.append({"kind": "default", "value": step.value, "type": current})
    return compiled, current


def _plain_type(sql_type: str) -> str | None:
    return safety.normalize_sql_type(sql_type or "")


def _entity_spec(
    entity: SilverEntityIn,
    *,
    catalog: str,
    schema: str,
    source_columns: dict[str, safety.SafeColumn],
    references: dict[uuid.UUID, DataAsset],
    problems: list[str],
) -> dict | None:
    name = entity.table_name
    label = f"entity {name!r}"
    before = len(problems)
    safety.collect(problems, safety.check_identifier, name, f"{label}: table name")
    if len(f"{name}_quarantine") > 128:
        problems.append(f"{label}: table name is too long (its quarantine table adds 11 characters)")

    attributes: list[dict] = []
    seen: dict[str, str] = {}
    for attr in entity.attributes:
        alabel = f"{label}, attribute {attr.name!r}"
        if safety.collect(problems, safety.check_identifier, attr.name, f"{label}: attribute name") is None:
            continue
        if attr.name.startswith("_"):
            problems.append(f"{alabel}: names starting with an underscore are reserved for platform columns")
            continue
        if attr.name.lower() in seen:
            problems.append(f"{alabel}: duplicate of {seen[attr.name.lower()]!r} (names are case-insensitive)")
            continue
        seen[attr.name.lower()] = attr.name
        source = source_columns.get(attr.source_column.lower())
        if source is None:
            problems.append(f"{alabel}: the source table has no column {attr.source_column!r}")
            continue
        steps, final_type = _compile_steps(alabel, source.sql_type, attr.steps, problems)
        attributes.append(
            {"name": attr.name, "from": source.name, "type": final_type, "required": attr.required, "steps": steps}
        )

    keys: list[str] = []
    for key in entity.business_key:
        match = seen.get(key.lower())
        if match is None:
            problems.append(f"{label}: business key {key!r} isn't one of its attributes")
        elif match in keys:
            problems.append(f"{label}: business key lists {match!r} twice")
        else:
            keys.append(match)
    for a in attributes:
        if a["name"] in keys:
            a["required"] = True  # a record without its key can't be placed at all
            if a["type"] in _KEY_TYPES_REFUSED:
                problems.append(f"{label}: {a['type']} column {a['name']!r} can't be a business key (approximate or unordered values)")

    master = None
    if entity.master_key is not None:
        mk = entity.master_key
        mlabel = f"{label}: master key"
        if len(keys) != 1:
            problems.append(f"{mlabel} needs a business key of exactly one attribute (the value looked up in the crosswalk)")
        safety.collect(problems, safety.check_identifier, mk.column, f"{mlabel} column")
        if mk.column.startswith("_"):
            problems.append(f"{mlabel} column {mk.column!r}: names starting with an underscore are reserved")
        if mk.column.lower() in seen:
            problems.append(f"{mlabel} column {mk.column!r} is already one of the attributes")
        crosswalk = references[mk.crosswalk_asset_id]
        if crosswalk.layer != "reference":
            problems.append(f"{mlabel}: {assets_svc.full_name(crosswalk)} is a {crosswalk.layer} table; register the crosswalk as a reference table first")
        else:
            stored = {c["name"].lower(): c for c in (crosswalk.columns or [])}
            roles = {"system column": mk.system_column, "key column": mk.key_column, "master key column": mk.master_column}
            if (mk.entity_type_column is None) != (mk.entity_type is None):
                problems.append(f"{mlabel}: give both the entity type column and the entity type value, or neither")
            elif mk.entity_type_column is not None:
                roles["entity type column"] = mk.entity_type_column
                safety.collect(problems, safety.check_simple_text, mk.entity_type, f"{mlabel}: entity type", allow_empty=False)
            ok = True
            for role, column in roles.items():
                found = stored.get(column.lower())
                if safety.collect(problems, safety.check_identifier, column, f"{mlabel}: {role}") is None:
                    ok = False
                elif found is None:
                    problems.append(f"{mlabel}: the crosswalk has no {role} named {column!r}")
                    ok = False
                elif _plain_type(found["sql_type"]) is None:
                    problems.append(f"{mlabel}: the crosswalk's {column!r} is a {found['sql_type']} column, which can't be matched")
                    ok = False
            if len({c.lower() for c in roles.values()}) != len(roles):
                problems.append(f"{mlabel}: the {', '.join(r.removesuffix(' column') for r in roles)} columns must all be different")
                ok = False
            if ok:
                master = {
                    "column": mk.column,
                    "crosswalk": assets_svc.full_name(crosswalk),
                    "crosswalk_asset_id": str(crosswalk.id),
                    "system_column": stored[mk.system_column.lower()]["name"],
                    "key_column": stored[mk.key_column.lower()]["name"],
                    "master_column": stored[mk.master_column.lower()]["name"],
                }
                if mk.entity_type_column is not None:
                    master["entity_type_column"] = stored[mk.entity_type_column.lower()]["name"]
                    master["entity_type"] = mk.entity_type

    if len(problems) > before or not attributes:
        return None
    return {
        "name": name,
        "target": f"{catalog}.{schema}.{name}",
        "quarantine": f"{catalog}.{schema}.{name}_quarantine",
        "business_key": keys,
        "attributes": attributes,
        "master_key": master,
    }


def build_silver_plan(db: Session, org_id: uuid.UUID, body: SilverPipelineIn, *, lock: bool = False) -> Plan:
    problems: list[str] = []

    reference_ids = sorted({e.master_key.crosswalk_asset_id for e in body.entities if e.master_key}, key=str)
    loaded = assets_svc.load_assets(db, org_id, [body.source_asset_id, *reference_ids], lock)
    bronze = loaded[body.source_asset_id]
    references = {i: loaded[i] for i in reference_ids}

    safety.collect(problems, safety.check_identifier, body.catalog, "catalog")
    safety.collect(problems, safety.check_identifier, body.target_schema, "target schema")
    safety.collect(problems, safety.check_job_name, body.job_name)
    safety.collect(problems, safety.check_schedule, body.schedule.frequency, body.schedule.time, body.schedule.day_of_week, body.schedule.timezone)
    safety.collect(problems, safety.check_identifier, body.source_system, "source system")
    safety.collect(problems, safety.check_simple_text, body.unknown_master_key, "unknown master key", allow_empty=False)
    if body.cluster_id:
        safety.collect(problems, safety.check_cluster_id, body.cluster_id)

    bronze_name = assets_svc.full_name(bronze)
    source_columns: dict[str, safety.SafeColumn] = {}
    if bronze.layer != "bronze":
        problems.append(f"{bronze_name} is a {bronze.layer} table; Silver is built from a Bronze table")
    elif not bronze.columns:
        problems.append(f"{bronze_name} has no column list on record — redeploy the pipeline that builds it, then try again")
    else:
        checked = safety.collect(problems, safety.check_columns, list(bronze.columns), source_type="cobol")
        source_columns = {c.name.lower(): c for c in checked or []}

    names = [e.table_name.lower() for e in body.entities]
    if len(set(names)) != len(names):
        problems.append("table names must be unique within a pipeline (case-insensitive)")
    quarantines = {f"{n}_quarantine" for n in names}
    clash = quarantines & set(names)
    if clash:
        problems.append(f"a table can't be named {sorted(clash)[0]!r}: that name is taken by another table's quarantine table")

    entities: list[dict] = []
    if source_columns:
        for entity in body.entities:
            spec = _entity_spec(
                entity, catalog=body.catalog, schema=body.target_schema, source_columns=source_columns,
                references=references, problems=problems,
            )
            if spec is not None:
                entities.append(spec)
                if spec["target"] == bronze_name:
                    problems.append(f"{spec['target']}: the Silver table can't be its Bronze source — choose another schema or name")

    workspace_path = workspace_path_for(org_id, body.job_name)
    safety.collect(problems, safety.check_workspace_path, workspace_path)
    if problems:
        raise Unprocessable(_PROBLEMS_TITLE, problems=problems)

    # Names this pipeline would write must not already belong to something else.
    for entity in entities:
        for table, may_be_silver in ((entity["name"], True), (f"{entity['name']}_quarantine", False)):
            existing = db.scalar(
                select(DataAsset).where(
                    DataAsset.org_id == org_id,
                    DataAsset.catalog == body.catalog,
                    DataAsset.schema_name == body.target_schema,
                    DataAsset.table_name == table,
                )
            )
            if existing is not None and not (may_be_silver and existing.layer == "silver"):
                raise Conflict(
                    f"{body.catalog}.{body.target_schema}.{table} is already registered as a {existing.layer} table — "
                    "choose another name"
                )

    spec = {
        "source": {"asset_id": str(bronze.id), "bronze": bronze_name, "system": body.source_system},
        "unknown_master_key": body.unknown_master_key,
        "entities": entities,
    }
    try:
        hour, minute = (int(p) for p in body.schedule.time.split(":"))
        cron = engine.build_cron(body.schedule.frequency, body.schedule.time, body.schedule.day_of_week)
        with tempfile.TemporaryDirectory() as tmp:
            _, notebook = engine.generate_silver_notebook(spec=spec, out_dir=Path(tmp))
    except (engine.EngineInputError, engine.EngineRemoteError) as exc:
        raise Unprocessable(f"The notebook couldn't be generated: {exc}")

    def register(db: Session, pipeline: Pipeline) -> None:
        assets_svc.register_silver_assets(
            db, pipeline=pipeline, bronze=bronze, spec=spec, references={str(k): v for k, v in references.items()}
        )

    return Plan(
        source_type="table",
        mode="multi" if len(entities) > 1 else "single",
        links=[],
        notebook=notebook,
        notebook_sha256=engine.notebook_sha256(notebook),
        workspace_path=workspace_path,
        cron=cron,
        source_path_param="",  # reads a table, not a file: no source_path job parameter
        extra_parameters={},
        schedule_time=time(hour, minute),
        layer="silver",
        spec=spec,
        register=register,
    )


def deploy_silver(
    db: Session, *, org_id: uuid.UUID, user_id: uuid.UUID, body: SilverDeployIn, client_builder: ClientBuilder
) -> Pipeline:
    return deploy_plan(
        db,
        org_id=org_id,
        user_id=user_id,
        body=body,
        client_builder=client_builder,
        build=lambda lock: build_silver_plan(db, org_id, body, lock=lock),
    )
