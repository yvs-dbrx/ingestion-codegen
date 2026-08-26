"""
pipeline_store.py

Local stand-in for the PRD's org/pipeline/billing tables (PRD Section 5.1,
5.8) and for Polar's usage meter (PRD Section 9) — enough to prove the
"block pipeline creation at the plan limit, prompt to upgrade" UX in the
Streamlit app without a real database, auth, or Polar integration behind
it. Single tenant, JSON file on disk, no concurrent-write handling.

Swap this module out (same function signatures) once there's a real
Postgres-backed multi-tenant store and a live Polar meter to read from —
callers (app.py) shouldn't need to change.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

STORE_PATH = Path(__file__).parent / "pipelines_store.json"

# Mirrors PRD Section 5.8's pricing table.
TIER_LIMITS = {"free": 3, "team": 10, "pro": 20}
DEFAULT_TIER = "free"


def _load() -> dict:
    if not STORE_PATH.exists():
        return {"tier": DEFAULT_TIER, "pipelines": []}
    return json.loads(STORE_PATH.read_text())


def _save(data: dict) -> None:
    STORE_PATH.write_text(json.dumps(data, indent=2))


def get_tier() -> str:
    return _load().get("tier", DEFAULT_TIER)


def set_tier(tier: str) -> None:
    if tier not in TIER_LIMITS:
        raise ValueError(f"Unknown tier '{tier}'. Use one of {list(TIER_LIMITS)}.")
    data = _load()
    data["tier"] = tier
    _save(data)


def tier_limit(tier: str | None = None) -> int:
    return TIER_LIMITS[tier or get_tier()]


def list_pipelines() -> list[dict]:
    return _load().get("pipelines", [])


def pipeline_count() -> int:
    return len(list_pipelines())


def can_create_pipeline() -> bool:
    return pipeline_count() < tier_limit()


def add_pipeline(record: dict) -> None:
    data = _load()
    record = {**record, "created_at": datetime.now(timezone.utc).isoformat()}
    data.setdefault("pipelines", []).append(record)
    _save(data)


def remove_pipeline(job_name: str) -> None:
    data = _load()
    data["pipelines"] = [p for p in data.get("pipelines", []) if p["job_name"] != job_name]
    _save(data)
