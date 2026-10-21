"""Run history for deployed pipelines.

Databricks is the source of truth for what a run did; `pipeline_runs` is our copy of it, kept
fresh by *pulling* (refresh) — there is no background poller yet, so a scheduled run shows up
the next time someone refreshes. Two rules keep the copy honest:

  * a Databricks run is recorded at most once (unique index on pipeline_id + databricks_run_id),
    so syncing is safe to repeat and safe to run concurrently;
  * everything that talks to Databricks for one pipeline first locks that pipeline's row, so two
    people pressing "run" or "refresh" at the same moment are handled one after the other.
"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Pipeline, PipelineRun
from app.services import engine, run_metrics
from app.services.databricks_client import ClientBuilder, client_for_org
from app.services.errors import BadGateway, Conflict, NotFound
from app.services.pipelines import ensure_same_workspace

ACTIVE = ("pending", "running")

# Newest first. `created_at` alone can't do it: every run found by one sync is inserted in the same
# transaction and shares a timestamp. A run we just started has no start time yet, so it falls back
# to created_at; Databricks run ids only ever grow, which settles any remaining tie.
RECENT_FIRST = (
    func.coalesce(PipelineRun.started_at, PipelineRun.created_at).desc(),
    PipelineRun.databricks_run_id.desc().nulls_last(),
    PipelineRun.id,
)
SYNC_WINDOW = 25  # how many of the job's most recent runs each refresh looks at
METRICS_PER_REFRESH = 10  # finished runs whose reported counts are fetched per refresh (2 Databricks calls each)


def _locked_pipeline(db: Session, org_id: uuid.UUID, pipeline_id: uuid.UUID) -> Pipeline:
    pipeline = db.scalar(
        select(Pipeline)
        .where(Pipeline.id == pipeline_id, Pipeline.org_id == org_id)  # tenant filter
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if pipeline is None:
        raise NotFound("Pipeline not found")
    if pipeline.databricks_job_id is None:
        raise Conflict("This pipeline has no Databricks job to run")
    return pipeline


def get_owned_pipeline(db: Session, org_id: uuid.UUID, pipeline_id: uuid.UUID) -> Pipeline:
    pipeline = db.scalar(select(Pipeline).where(Pipeline.id == pipeline_id, Pipeline.org_id == org_id))
    if pipeline is None:
        raise NotFound("Pipeline not found")
    return pipeline


def _sync(db: Session, client, pipeline: Pipeline) -> None:
    """Copies the job's recent runs from Databricks into pipeline_runs. Caller holds the pipeline lock."""
    try:
        remote = engine.list_runs(client, pipeline.databricks_job_id, SYNC_WINDOW)
    except engine.EngineRemoteError as exc:
        raise BadGateway(f"Couldn't read the run history from Databricks: {exc}")

    known = {
        r.databricks_run_id: r
        for r in db.scalars(
            select(PipelineRun).where(
                PipelineRun.pipeline_id == pipeline.id,
                PipelineRun.databricks_run_id.in_([r.run_id for r in remote]),
            )
        )
    }
    for r in remote:
        row = known.get(r.run_id)
        if row is None:
            row = PipelineRun(pipeline_id=pipeline.id, databricks_run_id=r.run_id, triggered_by=r.triggered_by)
            db.add(row)
        # triggered_by is left alone on existing rows: we know better than Databricks whether
        # a run came from a deploy or a manual press.
        row.status = r.status
        row.started_at = r.started_at
        row.finished_at = r.finished_at
        row.error_message = r.error_message
        row.databricks_run_url = r.url
    db.flush()
    _fetch_metrics(db, client, pipeline)


def _fetch_metrics(db: Session, client, pipeline: Pipeline) -> None:
    """For finished runs we haven't read the counts of yet, ask Databricks what the notebook reported.

    Best effort: if Databricks can't be asked, the run keeps no metrics and is tried again on the next
    refresh. A run that finished but reported nothing usable (an older notebook) is marked so it is
    never asked about again. Oldest first, so the registry ends up with the newest run's numbers.
    """
    pending = db.scalars(
        select(PipelineRun)
        .where(
            PipelineRun.pipeline_id == pipeline.id,
            PipelineRun.status == "success",
            PipelineRun.metrics.is_(None),
            PipelineRun.databricks_run_id.is_not(None),
        )
        .order_by(PipelineRun.finished_at.asc().nulls_last(), PipelineRun.id)
        .limit(METRICS_PER_REFRESH)
    ).all()
    for row in pending:
        try:
            reported = engine.run_output(client, row.databricks_run_id)
        except (engine.EngineRemoteError, engine.EngineInputError):
            continue
        cleaned = run_metrics.clean(reported) if reported is not None else None
        row.metrics = cleaned or {"unavailable": True}
        if cleaned:
            run_metrics.apply_to_assets(db, pipeline, cleaned, row.finished_at)
    db.flush()


def refresh(db: Session, *, org_id: uuid.UUID, pipeline_id: uuid.UUID, client_builder: ClientBuilder) -> Pipeline:
    pipeline = _locked_pipeline(db, org_id, pipeline_id)
    client = client_for_org(db, org_id, client_builder)
    ensure_same_workspace(client, pipeline)
    _sync(db, client, pipeline)
    db.commit()
    return pipeline


def run_now(
    db: Session, *, org_id: uuid.UUID, pipeline_id: uuid.UUID, client_builder: ClientBuilder
) -> PipelineRun:
    pipeline = _locked_pipeline(db, org_id, pipeline_id)
    client = client_for_org(db, org_id, client_builder)
    ensure_same_workspace(client, pipeline)

    _sync(db, client, pipeline)  # our idea of "is it already running?" must not be stale
    active = db.scalar(
        select(PipelineRun)
        .where(PipelineRun.pipeline_id == pipeline.id, PipelineRun.status.in_(ACTIVE))
        .order_by(*RECENT_FIRST)
    )
    if active is not None:
        db.commit()  # keep what the sync learned
        raise Conflict("This pipeline already has a run in progress — wait for it to finish (refresh to update its status)")

    try:
        run_id = engine.run_now(client, pipeline.databricks_job_id)
    except engine.EngineRemoteError as exc:
        db.rollback()
        raise BadGateway(f"Databricks couldn't start the run: {exc}")

    run = PipelineRun(pipeline_id=pipeline.id, databricks_run_id=run_id, triggered_by="manual", status="pending")
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def list_runs(
    db: Session, *, org_id: uuid.UUID, pipeline_id: uuid.UUID, limit: int, offset: int
) -> list[PipelineRun]:
    get_owned_pipeline(db, org_id, pipeline_id)
    return list(
        db.scalars(
            select(PipelineRun)
            .where(PipelineRun.pipeline_id == pipeline_id)
            .order_by(*RECENT_FIRST)
            .limit(limit)
            .offset(offset)
        )
    )


def org_runs(db: Session, *, org_id: uuid.UUID, status: str | None, limit: int) -> list[tuple[PipelineRun, str]]:
    """Recent runs across every pipeline in the org, with the pipeline's name."""
    stmt = (
        select(PipelineRun, Pipeline.job_name)
        .join(Pipeline, Pipeline.id == PipelineRun.pipeline_id)
        .where(Pipeline.org_id == org_id)  # tenant filter
        .order_by(*RECENT_FIRST)
        .limit(limit)
    )
    if status:
        stmt = stmt.where(PipelineRun.status == status)
    return [(r, name) for r, name in db.execute(stmt).all()]


def latest_runs(db: Session, pipeline_ids: list[uuid.UUID]) -> dict[uuid.UUID, PipelineRun]:
    if not pipeline_ids:
        return {}
    rows = db.scalars(
        select(PipelineRun)
        .where(PipelineRun.pipeline_id.in_(pipeline_ids))
        .distinct(PipelineRun.pipeline_id)
        .order_by(PipelineRun.pipeline_id, *RECENT_FIRST)
    )
    return {r.pipeline_id: r for r in rows}
