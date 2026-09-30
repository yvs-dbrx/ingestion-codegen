"""Versioned registry of every unique source file / copybook (SCD Type-2).

A source's identity is (org, source_type, source_path, schema_definition_path).
Registering a discovered schema either creates version 1, confirms the active
version is unchanged, or — if the shape differs — closes the active version and
opens the next one. Nothing is ever overwritten, so drift is detectable and
every shape a source has had is kept.
"""
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.models import Pipeline, PipelineSource, SourceRegistry
from app.schemas import ColumnIn


def normalize_for_hash(columns: list[ColumnIn]) -> list[dict]:
    """The parts of a column that count as schema drift.

    Included: name, SQL type (whitespace/case-insensitive: 'DECIMAL(15, 2)' ==
    'decimal(15,2)'), the COBOL struct path, and column ORDER (positional formats
    like headerless CSV and COBOL break if columns move).

    Deliberately excluded: `nullable` — for sample-based formats it only reflects
    whether the sample happened to contain a null, so including it would raise
    false drift alarms; and the display labels spark_type/pandas_dtype, which
    vary by library version (pandas 'str' vs 'object') without the data changing.
    """
    return [
        {
            "name": c.name,
            "sql_type": re.sub(r"\s+", "", c.sql_type).upper(),
            "struct_path": c.struct_path or None,
        }
        for c in columns
    ]


def compute_schema_hash(columns: list[ColumnIn]) -> str:
    canonical = json.dumps(normalize_for_hash(columns), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _identity(org_id: uuid.UUID, source_type: str, source_path: str, schema_definition_path: str | None):
    # The database's uniqueness treats NULL and '' as the same (COALESCE), so match that.
    return and_(
        SourceRegistry.org_id == org_id,
        SourceRegistry.source_type == source_type,
        SourceRegistry.source_path == source_path,
        func.coalesce(SourceRegistry.schema_definition_path, "") == (schema_definition_path or ""),
    )


@dataclass
class RegisterResult:
    status: Literal["created", "unchanged", "drifted"]
    source: SourceRegistry
    previous_version: int | None


def register_source_version(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    source_type: str,
    source_path: str,
    schema_definition_path: str | None,
    discovery_config: dict,
    columns: list[ColumnIn],
) -> RegisterResult:
    """Flushes but does not commit — the caller owns the transaction."""
    schema_definition_path = schema_definition_path or None
    new_hash = compute_schema_hash(columns)
    schema_json = [c.model_dump() for c in columns]
    ident = _identity(org_id, source_type, source_path, schema_definition_path)

    def new_row(version: int) -> SourceRegistry:
        return SourceRegistry(
            org_id=org_id,
            source_type=source_type,
            source_path=source_path,
            schema_definition_path=schema_definition_path,
            discovery_config=discovery_config,
            schema_json=schema_json,
            schema_hash=new_hash,
            version=version,
            discovered_by=user_id,
        )

    # Two requests can race to create the same brand-new source; the unique index
    # lets exactly one win. The loser retries and then sees the winner's row.
    for _ in range(3):
        active = db.scalar(select(SourceRegistry).where(ident, SourceRegistry.record_is_active).with_for_update())

        if active is None:
            latest = db.scalar(select(func.max(SourceRegistry.version)).where(ident)) or 0
            row = new_row(latest + 1)
            try:
                with db.begin_nested():
                    db.add(row)
                    db.flush()
            except IntegrityError:
                continue
            return RegisterResult("created", row, None)

        if active.schema_hash == new_hash:
            active.last_verified_at = func.now()
            db.flush()
            db.refresh(active)
            return RegisterResult("unchanged", active, active.version)

        # Drift: close the active version first (the partial unique index allows
        # only one active row), then open the next.
        previous_version = active.version
        active.record_end_ts = func.now()
        active.record_is_active = False
        db.flush()
        row = new_row(previous_version + 1)
        db.add(row)
        db.flush()
        return RegisterResult("drifted", row, previous_version)

    raise RuntimeError("Could not register source after repeated concurrent conflicts")


def stale_pipelines(db: Session, org_id: uuid.UUID) -> list[dict]:
    """Pipelines generated from a source version that is no longer the active one."""
    old = aliased(SourceRegistry)
    cur = aliased(SourceRegistry)
    rows = db.execute(
        select(
            Pipeline.id,
            Pipeline.job_name,
            PipelineSource.table_name,
            old.id,
            old.source_type,
            old.source_path,
            old.schema_definition_path,
            old.version,
            cur.id,
            cur.version,
        )
        .select_from(Pipeline)
        .join(PipelineSource, PipelineSource.pipeline_id == Pipeline.id)
        .join(old, old.id == PipelineSource.source_registry_id)
        .join(
            cur,
            and_(
                cur.org_id == old.org_id,
                cur.source_type == old.source_type,
                cur.source_path == old.source_path,
                func.coalesce(cur.schema_definition_path, "") == func.coalesce(old.schema_definition_path, ""),
                cur.record_is_active,
            ),
        )
        # Filter on all three so a mis-linked cross-tenant row can never surface here.
        .where(Pipeline.org_id == org_id, old.org_id == org_id, cur.id != old.id)
        .order_by(Pipeline.job_name, PipelineSource.table_name)
    ).all()
    return [
        {
            "pipeline_id": r[0],
            "job_name": r[1],
            "table_name": r[2],
            "built_from_source_id": r[3],
            "source_type": r[4],
            "source_path": r[5],
            "schema_definition_path": r[6],
            "built_from_version": r[7],
            "current_source_id": r[8],
            "current_version": r[9],
        }
        for r in rows
    ]
