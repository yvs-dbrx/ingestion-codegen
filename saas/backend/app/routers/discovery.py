from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import OrgContext, get_org_context
from app.routers.sources import _to_out
from app.schemas import ColumnIn, DiscoverIn, DiscoveredSourceOut, DiscoverOut
from app.services import codegen_safety as safety
from app.services import engine
from app.services.databricks_client import ClientBuilder, client_for_org, get_client_builder
from app.services.errors import BadGateway, Unprocessable
from app.services.source_registry import register_source_version

router = APIRouter(tags=["discovery"])

MAX_TABLES = 200


@router.post("/discover", response_model=DiscoverOut)
def discover(
    body: DiscoverIn,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
    builder: ClientBuilder = Depends(get_client_builder),
):
    """Reads the schema of a file (or every file in a folder) that lives in YOUR Databricks
    workspace — through a short probe job that runs there — and records it in the source registry.

    Synchronous for now: a COBOL/large-folder probe can take a minute or more.
    """
    problems: list[str] = []
    safety.collect(problems, safety.check_path, body.source_path, "source path")
    safety.collect(problems, safety.check_delimiter, body.delimiter)
    if body.schema_definition_path:
        safety.collect(problems, safety.check_path, body.schema_definition_path, "schema definition path")
    if body.source_type == "xml":
        safety.collect(problems, safety.check_row_tag, body.row_tag)
    if body.cluster_id:
        safety.collect(problems, safety.check_cluster_id, body.cluster_id)
    if problems:
        raise Unprocessable("Those discovery settings can't be used", problems=problems)

    org_id, user_id = ctx.org.id, ctx.user.id
    client = client_for_org(db, org_id, builder)
    db.rollback()  # don't sit on a transaction while the probe job runs

    try:
        tables = engine.discover(
            client,
            source_type=body.source_type,
            mode=body.mode,
            source_path=body.source_path,
            schema_definition_path=body.schema_definition_path,
            delimiter=body.delimiter,
            has_header=body.has_header,
            multiline=body.multiline,
            row_tag=body.row_tag,
            is_record_sequence=body.is_record_sequence,
            is_text=body.is_text,
            cluster_id=body.cluster_id,
        )
    except engine.EngineInputError as exc:
        raise Unprocessable(f"Couldn't read a schema from that source: {exc}")
    except engine.EngineRemoteError as exc:
        raise BadGateway(f"Databricks couldn't complete the discovery: {exc}")

    if not tables:
        raise Unprocessable("No files were found to discover")
    if len(tables) > MAX_TABLES:
        raise Unprocessable(f"That folder has {len(tables)} files; the limit is {MAX_TABLES} per discovery")

    # Nothing is recorded unless every table's columns are safe to put in generated code.
    for t in tables:
        found: list[str] = []
        safety.collect(found, safety.check_columns, t.columns, source_type=body.source_type)
        safety.collect(found, safety.check_file_name, t.file_name)
        problems += [f"{t.file_name}: {p}" for p in found]
    if problems:
        raise Unprocessable("The discovered schema can't be used as-is", problems=problems)

    discovery_config = {
        "mode": body.mode,
        "delimiter": body.delimiter,
        "has_header": body.has_header,
        "multiline": body.multiline,
        "row_tag": body.row_tag,
        "is_record_sequence": body.is_record_sequence,
        "is_text": body.is_text,
        "cluster_id": body.cluster_id,
    }
    out = []
    for t in tables:
        result = register_source_version(
            db,
            org_id=org_id,
            user_id=user_id,
            source_type=body.source_type,
            source_path=t.source_path,
            schema_definition_path=body.schema_definition_path,
            discovery_config=discovery_config,
            columns=[ColumnIn(**c) for c in t.columns],
        )
        out.append(
            DiscoveredSourceOut(
                suggested_table_name=t.table_name,
                status=result.status,
                previous_version=result.previous_version,
                source=_to_out(result.source),
            )
        )
    db.commit()
    return DiscoverOut(sources=out)
