"""The table registry: every table the platform produces, its layer and its lineage.

It is metadata about tables that live in the customer's own Databricks workspace — never their
contents. Registration happens inside the same transaction that creates the producing pipeline,
so a deploy that fails and rolls back leaves no phantom entries behind.
"""
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AssetLineage, DataAsset, Pipeline, SourceRegistry
from app.services import codegen_safety as safety
from app.services import engine
from app.services.codegen_safety import SafeColumn
from app.services.errors import Conflict, NotFound, Unprocessable
from app.services.tenancy import lock_org


def full_name(a: DataAsset) -> str:
    return f"{a.catalog}.{a.schema_name}.{a.table_name}"


def column_list(columns: list[SafeColumn]) -> list[dict]:
    """How a table's columns are remembered: validated names and normalized SQL types, in order."""
    return [{"name": c.name, "sql_type": c.sql_type} for c in columns]


def register_raw_assets(
    db: Session, *, pipeline: Pipeline, links: list[tuple[SourceRegistry, str]], tables: list
) -> list[DataAsset]:
    """One Raw asset per target table of an ingestion pipeline (every load appended, duplicates kept). Flushes; the caller owns the transaction.

    Writing to a table that is already registered (the pipeline that made it was deleted, or the
    table was created by an earlier version) adopts it rather than failing: the latest pipeline
    becomes its producer.
    """
    out = []
    for (source, table_name), table in zip(links, tables):
        asset = db.scalar(
            select(DataAsset).where(
                DataAsset.org_id == pipeline.org_id,
                DataAsset.catalog == pipeline.catalog,
                DataAsset.schema_name == pipeline.target_schema,
                DataAsset.table_name == table_name,
            )
        )
        if asset is None:
            asset = DataAsset(
                org_id=pipeline.org_id,
                catalog=pipeline.catalog,
                schema_name=pipeline.target_schema,
                table_name=table_name,
                layer="raw",
            )
            db.add(asset)
        asset.layer = "raw"
        asset.origin = source.source_path
        asset.columns = column_list(table.columns)
        asset.produced_by = pipeline.id
        asset.updated_at = func.now()
        out.append(asset)
    db.flush()
    return out


def register_bronze_assets(
    db: Session, *, pipeline: Pipeline, inputs: list[tuple[DataAsset, str, list[SafeColumn]]]
) -> list[DataAsset]:
    """One Bronze asset per (Raw table -> Bronze table) of a pipeline, linked to its Raw source.
    `inputs` is [(raw asset, bronze table name, columns)]. Flushes; the caller owns the transaction.

    A table that already exists as Bronze (its pipeline was deleted) is adopted. One that exists
    as anything else is refused: this pipeline would be writing into some other layer's table.
    """
    out = []
    for raw, table_name, columns in inputs:
        asset = db.scalar(
            select(DataAsset).where(
                DataAsset.org_id == pipeline.org_id,
                DataAsset.catalog == pipeline.catalog,
                DataAsset.schema_name == pipeline.target_schema,
                DataAsset.table_name == table_name,
            )
        )
        if asset is not None and asset.layer != "bronze":
            raise Conflict(f"{full_name(asset)} is already registered as a {asset.layer} table, not a Bronze one")
        if asset is None:
            asset = DataAsset(
                org_id=pipeline.org_id,
                catalog=pipeline.catalog,
                schema_name=pipeline.target_schema,
                table_name=table_name,
                layer="bronze",
            )
            db.add(asset)
        asset.origin = None
        asset.columns = column_list(columns)
        asset.produced_by = pipeline.id
        asset.updated_at = func.now()
        db.flush()
        linked = db.scalar(
            select(AssetLineage).where(AssetLineage.downstream_id == asset.id, AssetLineage.upstream_id == raw.id)
        )
        if linked is None:
            db.add(AssetLineage(org_id=pipeline.org_id, downstream_id=asset.id, upstream_id=raw.id))
        out.append(asset)
    db.flush()
    return out


def load_assets(db: Session, org_id: uuid.UUID, ids: list[uuid.UUID], lock: bool) -> dict[uuid.UUID, DataAsset]:
    """The organization's own assets with these ids; any that isn't theirs (or doesn't exist) is a 404.
    With `lock`, rows are locked in id order so two deploys touching the same tables can't deadlock."""
    stmt = select(DataAsset).where(DataAsset.id.in_(ids), DataAsset.org_id == org_id)  # tenant filter
    if lock:
        stmt = stmt.order_by(DataAsset.id).with_for_update()
    found = {a.id: a for a in db.scalars(stmt)}
    for asset_id in ids:
        if asset_id not in found:
            raise NotFound(f"Table {asset_id} not found")
    return found


def register_reference(db: Session, *, org_id: uuid.UUID, client, catalog: str, schema_name: str, table_name: str) -> DataAsset:
    """Records an existing table in the customer's workspace (e.g. a source-key -> master-key crosswalk
    that identity resolution produced elsewhere) so pipelines can look values up in it.

    The table's columns are read from the workspace itself, not taken on trust. Registering the same
    table again refreshes its column list. Nothing in the table is ever read.
    """
    problems: list[str] = []
    safety.collect(problems, safety.check_identifier, catalog, "catalog")
    safety.collect(problems, safety.check_identifier, schema_name, "schema")
    safety.collect(problems, safety.check_identifier, table_name, "table name")
    if problems:
        raise Unprocessable("This table can't be registered", problems=problems)

    found = engine.table_columns(client, f"{catalog}.{schema_name}.{table_name}")
    if found is None:
        raise NotFound(f"{catalog}.{schema_name}.{table_name} doesn't exist in your Databricks workspace")
    columns = [
        {"name": c["name"], "sql_type": safety.normalize_sql_type(c["type"]) or c["type"].upper()[:40]}
        for c in found
        if safety.IDENTIFIER.match(c["name"])
    ]
    if not columns:
        raise Unprocessable("This table has no columns we can use (column names must be plain identifiers)")

    lock_org(db, org_id)
    asset = db.scalar(
        select(DataAsset).where(
            DataAsset.org_id == org_id,
            DataAsset.catalog == catalog,
            DataAsset.schema_name == schema_name,
            DataAsset.table_name == table_name,
        )
    )
    if asset is not None and asset.layer != "reference":
        raise Conflict(f"{full_name(asset)} is already registered as a {asset.layer} table, which this platform produces")
    if asset is None:
        asset = DataAsset(org_id=org_id, catalog=catalog, schema_name=schema_name, table_name=table_name, layer="reference")
        db.add(asset)
    asset.columns = columns
    asset.updated_at = func.now()
    db.flush()
    return asset


def register_silver_assets(
    db: Session, *, pipeline: Pipeline, bronze: DataAsset, spec: dict, references: dict[str, DataAsset]
) -> list[DataAsset]:
    """One Silver asset per entity of a Silver pipeline, linked to the Bronze table it reads and to any
    crosswalk it looks master keys up in. `spec` is the validated pipeline spec; `references` maps a
    crosswalk asset id to the asset. Flushes; the caller owns the transaction.

    A table that already exists as Silver (its pipeline was deleted) is adopted; one that is anything
    else is refused.
    """
    out = []
    for entity in spec["entities"]:
        name = entity["name"]
        asset = db.scalar(
            select(DataAsset).where(
                DataAsset.org_id == pipeline.org_id,
                DataAsset.catalog == pipeline.catalog,
                DataAsset.schema_name == pipeline.target_schema,
                DataAsset.table_name == name,
            )
        )
        if asset is not None and asset.layer != "silver":
            raise Conflict(f"{full_name(asset)} is already registered as a {asset.layer} table, not a Silver one")
        if asset is None:
            asset = DataAsset(
                org_id=pipeline.org_id, catalog=pipeline.catalog, schema_name=pipeline.target_schema, table_name=name, layer="silver"
            )
            db.add(asset)
        columns = [{"name": a["name"], "sql_type": a["type"]} for a in entity["attributes"]]
        master = entity["master_key"]
        if master:
            columns += [{"name": master["column"], "sql_type": "STRING"}, {"name": "_master_key_resolved", "sql_type": "BOOLEAN"}]
        asset.columns = columns
        asset.source_system = spec["source"]["system"]
        asset.origin = None
        asset.produced_by = pipeline.id
        asset.updated_at = func.now()
        db.flush()
        upstream = [bronze] + ([references[master["crosswalk_asset_id"]]] if master else [])
        for up in upstream:
            linked = db.scalar(
                select(AssetLineage).where(AssetLineage.downstream_id == asset.id, AssetLineage.upstream_id == up.id)
            )
            if linked is None:
                db.add(AssetLineage(org_id=pipeline.org_id, downstream_id=asset.id, upstream_id=up.id))
        out.append(asset)
    db.flush()
    return out


@dataclass
class AssetView:
    asset: DataAsset
    producer: Pipeline | None
    upstream_count: int
    downstream_count: int


def _counts(db: Session, org_id: uuid.UUID, ids: list[uuid.UUID]) -> tuple[dict, dict]:
    if not ids:
        return {}, {}
    up = dict(
        db.execute(
            select(AssetLineage.downstream_id, func.count())
            .where(AssetLineage.org_id == org_id, AssetLineage.downstream_id.in_(ids))
            .group_by(AssetLineage.downstream_id)
        ).all()
    )
    down = dict(
        db.execute(
            select(AssetLineage.upstream_id, func.count())
            .where(AssetLineage.org_id == org_id, AssetLineage.upstream_id.in_(ids))
            .group_by(AssetLineage.upstream_id)
        ).all()
    )
    return up, down


def list_assets(db: Session, org_id: uuid.UUID, layer: str | None = None) -> list[AssetView]:
    stmt = select(DataAsset).where(DataAsset.org_id == org_id)  # tenant filter
    if layer:
        stmt = stmt.where(DataAsset.layer == layer)
    assets = list(db.scalars(stmt.order_by(DataAsset.layer, DataAsset.catalog, DataAsset.schema_name, DataAsset.table_name)))
    up, down = _counts(db, org_id, [a.id for a in assets])
    producers = _producers(db, org_id, assets)
    return [AssetView(a, producers.get(a.produced_by), up.get(a.id, 0), down.get(a.id, 0)) for a in assets]


def _producers(db: Session, org_id: uuid.UUID, assets: list[DataAsset]) -> dict[uuid.UUID, Pipeline]:
    ids = {a.produced_by for a in assets if a.produced_by}
    if not ids:
        return {}
    return {p.id: p for p in db.scalars(select(Pipeline).where(Pipeline.id.in_(ids), Pipeline.org_id == org_id))}


def get_asset(db: Session, org_id: uuid.UUID, asset_id: uuid.UUID) -> tuple[AssetView, list[DataAsset], list[DataAsset]]:
    asset = db.scalar(select(DataAsset).where(DataAsset.id == asset_id, DataAsset.org_id == org_id))  # tenant filter
    if asset is None:
        raise NotFound("Table not found")
    up_assets = list(
        db.scalars(
            select(DataAsset)
            .join(AssetLineage, AssetLineage.upstream_id == DataAsset.id)
            .where(AssetLineage.downstream_id == asset.id, AssetLineage.org_id == org_id)
            .order_by(DataAsset.catalog, DataAsset.schema_name, DataAsset.table_name)
        )
    )
    down_assets = list(
        db.scalars(
            select(DataAsset)
            .join(AssetLineage, AssetLineage.downstream_id == DataAsset.id)
            .where(AssetLineage.upstream_id == asset.id, AssetLineage.org_id == org_id)
            .order_by(DataAsset.catalog, DataAsset.schema_name, DataAsset.table_name)
        )
    )
    producers = _producers(db, org_id, [asset])
    view = AssetView(asset, producers.get(asset.produced_by), len(up_assets), len(down_assets))
    return view, up_assets, down_assets
