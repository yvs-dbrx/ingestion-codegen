"""What a run's notebook reports about itself, and how much of it we're willing to keep.

The generated notebooks hand their counts back through dbutils.notebook.exit(). That JSON is produced
inside the customer's workspace by a notebook anyone with access there can edit, so it is treated as
untrusted input: only known keys survive, numbers must be plain non-negative integers, strings are
capped, and nothing is stored that the UI doesn't understand.
"""
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DataAsset, Pipeline

INT_KEYS = (
    "rows_read",
    "rows_written",
    "duplicates_removed",
    "distinct_records",
    "rows_inserted",
    "already_in_target",
    "rows_in_target",
    "sightings_inserted",
    "sightings_in_target",
    # Silver
    "null_key_records",
    "distinct_keys",
    "conflicting_keys",
    "quarantined_keys",
    "rows_updated",
    "unchanged",
    "unmapped_keys",
    "crosswalk_rows",
    "crosswalk_version",
)
MAX_TABLES = 200
MAX_INT = 2**53  # beyond this a JavaScript client would silently lose precision


def _count(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= MAX_INT


def clean(data: dict) -> dict | None:
    """The version of `data` that is safe to store and show, or None if it isn't a metrics report."""
    if not isinstance(data, dict) or data.get("v") != 1 or data.get("layer") not in ("raw", "bronze", "silver"):
        return None
    tables_in = data.get("tables")
    if not isinstance(tables_in, dict):
        return None

    tables: dict[str, dict] = {}
    for name, t in list(tables_in.items())[:MAX_TABLES]:
        if not isinstance(name, str) or not isinstance(t, dict):
            continue
        entry: dict = {k: t[k] for k in INT_KEYS if _count(t.get(k))}
        for k in ("source", "target"):
            if isinstance(t.get(k), str):
                entry[k] = t[k][:300]
        extra = t.get("extra_source_columns")
        if isinstance(extra, list):
            entry["extra_source_columns"] = [x[:128] for x in extra[:50] if isinstance(x, str)]
        if isinstance(t.get("reconciled"), bool):
            entry["reconciled"] = t["reconciled"]
        tables[name[:128]] = entry

    return {
        "v": 1,
        "layer": data["layer"],
        "mode": str(data.get("mode", ""))[:20],
        # only an affirmative, and only if no table disagrees
        "reconciled": data.get("reconciled") is True and all(e.get("reconciled") is not False for e in tables.values()),
        "tables": tables,
    }


def apply_to_assets(db: Session, pipeline: Pipeline, metrics: dict, finished_at: datetime | None) -> None:
    """Refresh the registry's last-known row count for each table this pipeline writes.

    Only tables this pipeline itself produces are touched, however the report words it, and an older
    run never overwrites a newer one's numbers.
    """
    for entry in metrics["tables"].values():
        target, count = entry.get("target"), entry.get("rows_in_target")
        parts = target.split(".") if isinstance(target, str) else []
        if len(parts) != 3 or count is None:
            continue
        asset = db.scalar(
            select(DataAsset).where(
                DataAsset.org_id == pipeline.org_id,
                DataAsset.produced_by == pipeline.id,
                DataAsset.catalog == parts[0],
                DataAsset.schema_name == parts[1],
                DataAsset.table_name == parts[2],
            )
        )
        if asset is None:
            continue
        if finished_at is not None and asset.last_run_at is not None and finished_at < asset.last_run_at:
            continue
        asset.row_count = count
        asset.last_run_at = finished_at or func.now()
