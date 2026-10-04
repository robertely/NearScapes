from __future__ import annotations

from sqlalchemy import select

from nearscapes.analyzers.registry import autorun_specs, get_analyzer
from nearscapes.config import get_settings
from nearscapes.db.models import AnalysisRun, Job, SourceRecording
from nearscapes.db.session import SessionLocal
from nearscapes.jobs.dispatch import dispatch_analysis, dispatch_audacity_export

_PIPELINE_MARKER = "_pipeline"
_UPLOAD_PIPELINE = "upload"
_TERMINAL_RUN_STATES = {"complete", "failed"}


def queue_upload_analysis(source_id: str) -> list[str]:
    """Create and dispatch one run for every registered analyzer."""
    if not get_settings().auto_analyze_uploads:
        return []

    dispatches: list[tuple[str, str]] = []
    with SessionLocal() as db:
        source = db.get(SourceRecording, source_id)
        if not source or source.status != "ready":
            return []

        existing = db.scalars(
            select(AnalysisRun).where(AnalysisRun.source_id == source_id)
        ).all()
        auto_existing = {
            run.analyzer
            for run in existing
            if (run.parameters or {}).get(_PIPELINE_MARKER) == _UPLOAD_PIPELINE
        }

        for spec in autorun_specs():
            analyzer_id = spec["analyzer"]
            if analyzer_id in auto_existing:
                continue
            analyzer = get_analyzer(analyzer_id)
            parameters = dict(spec["parameters"])
            parameters[_PIPELINE_MARKER] = _UPLOAD_PIPELINE
            run = AnalysisRun(
                source_id=source_id,
                analyzer=analyzer.id,
                analyzer_version=analyzer.version,
                parameters=parameters,
                status="queued",
            )
            db.add(run)
            db.flush()
            job = Job(kind="analysis", source_id=source_id, run_id=run.id)
            db.add(job)
            db.flush()
            dispatches.append((job.id, run.id))

        db.commit()

    for job_id, run_id in dispatches:
        dispatch_analysis(job_id, run_id)
    return [run_id for _, run_id in dispatches]


def maybe_queue_audacity_export(source_id: str, *, force: bool = False) -> str | None:
    """Queue one Audacity export once all automatic analyzers have finished."""
    if not get_settings().auto_audacity_export and not force:
        return None

    expected = {spec["analyzer"] for spec in autorun_specs()}
    with SessionLocal() as db:
        source = db.scalar(
            select(SourceRecording)
            .where(SourceRecording.id == source_id)
            .with_for_update()
        )
        if not source:
            return None

        runs = db.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.source_id == source_id)
            .order_by(AnalysisRun.created_at.asc())
        ).all()
        auto_runs = [
            run
            for run in runs
            if (run.parameters or {}).get(_PIPELINE_MARKER) == _UPLOAD_PIPELINE
        ]
        latest_by_analyzer = {run.analyzer: run for run in auto_runs}

        if not force:
            if not expected.issubset(latest_by_analyzer):
                return None
            if any(
                latest_by_analyzer[analyzer].status not in _TERMINAL_RUN_STATES
                for analyzer in expected
            ):
                return None

        existing_jobs = db.scalars(
            select(Job)
            .where(Job.source_id == source_id, Job.kind == "audacity-export")
            .order_by(Job.created_at.desc())
        ).all()
        if existing_jobs and existing_jobs[0].status in {"queued", "running", "complete"}:
            return existing_jobs[0].id

        job = Job(kind="audacity-export", source_id=source_id, status="queued")
        db.add(job)
        db.commit()
        job_id = job.id

    dispatch_audacity_export(job_id, source_id)
    return job_id
