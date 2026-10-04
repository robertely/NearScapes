from __future__ import annotations

from sqlalchemy import select

from nearscapes.analyzers.registry import autorun_specs, default_parameters, get_analyzer
from nearscapes.analyzers.slate_transcript import parse_opening_location
from nearscapes.config import get_settings
from nearscapes.db.models import AnalysisRun, Event, Job, SourceRecording
from nearscapes.db.session import SessionLocal
from nearscapes.jobs.dispatch import dispatch_analysis, dispatch_audacity_export

_PIPELINE_MARKER = "_pipeline"
_UPLOAD_PIPELINE = "upload"
_TERMINAL_RUN_STATES = {"complete", "failed"}
_SLATE_TRANSCRIPT_ANALYZER = "slate-transcript"
_BIRDNET_ANALYZER = "birdnet"
_BIRDNET_PARAMETERS = "_birdnet_parameters"


def backfill_location_from_opening_slate(source_id: str) -> dict | None:
    """Persist location from an existing opening-slate transcript if metadata lacks it."""
    with SessionLocal() as db:
        source = db.get(SourceRecording, source_id)
        if not source:
            return None

        metadata = dict(source.embedded_metadata or {})
        if metadata.get("location"):
            return dict(metadata["location"])

        transcript_runs = db.scalars(
            select(AnalysisRun)
            .where(
                AnalysisRun.source_id == source_id,
                AnalysisRun.analyzer == _SLATE_TRANSCRIPT_ANALYZER,
                AnalysisRun.status == "complete",
            )
            .order_by(AnalysisRun.created_at.desc())
        ).all()

        for run in transcript_runs:
            events = db.scalars(
                select(Event)
                .where(
                    Event.run_id == run.id,
                    Event.category == "slate-transcript",
                )
                .order_by(Event.start_seconds.asc())
            ).all()
            for event in events:
                if not event.text:
                    continue
                location = parse_opening_location(event.text)
                if not location:
                    continue
                metadata["location"] = location
                source.embedded_metadata = metadata
                db.commit()
                return location

    return None


def queue_upload_analysis(
    source_id: str,
    analyzer_parameter_overrides: dict[str, dict] | None = None,
) -> list[str]:
    """Create and dispatch one run for every automatic analyzer."""
    if not get_settings().auto_analyze_uploads:
        return []

    overrides = analyzer_parameter_overrides or {}
    backfill_location_from_opening_slate(source_id)
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
            parameters.update(overrides.get(analyzer_id, {}))
            if analyzer_id == "slate-tone":
                parameters[_BIRDNET_PARAMETERS] = dict(overrides.get("birdnet", {}))
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


def build_slate_transcription_windows(
    events: list[Event],
    *,
    source_duration_seconds: float,
    post_seconds: float,
) -> list[dict]:
    markers = sorted(
        (event for event in events if event.category == "slate-marker"),
        key=lambda event: event.start_seconds,
    )
    regions = sorted(
        (event for event in events if event.category == "slate-region"),
        key=lambda event: event.start_seconds,
    )

    windows: list[dict] = []
    used_markers: set[int] = set()
    tolerance_seconds = 0.25

    for region in regions:
        if region.end_seconds - region.start_seconds >= 0.1:
            windows.append(
                {
                    "start_seconds": float(region.start_seconds),
                    "end_seconds": float(region.end_seconds),
                    "boundary": "opening-slate",
                }
            )

        for index, marker_event in enumerate(markers):
            if abs(marker_event.end_seconds - region.start_seconds) <= tolerance_seconds:
                used_markers.add(index)
            if abs(marker_event.start_seconds - region.end_seconds) <= tolerance_seconds:
                used_markers.add(index)

    for index, marker_event in enumerate(markers):
        if index in used_markers:
            continue

        start_seconds = float(marker_event.end_seconds)
        end_seconds = min(source_duration_seconds, start_seconds + post_seconds)

        if end_seconds - start_seconds >= 0.1:
            windows.append(
                {
                    "start_seconds": start_seconds,
                    "end_seconds": end_seconds,
                    "boundary": "note-slate-candidate",
                }
            )

    return sorted(
        windows,
        key=lambda window: (window["start_seconds"], window["end_seconds"]),
    )


def queue_slate_transcription(source_id: str, slate_run_id: str) -> str | None:
    settings = get_settings()
    if settings.accelerator.lower() not in {"metal", "mps"}:
        return None

    dispatch: tuple[str, str] | None = None
    with SessionLocal() as db:
        source = db.get(SourceRecording, source_id)
        slate_run = db.get(AnalysisRun, slate_run_id)
        if (
            not source
            or not slate_run
            or slate_run.source_id != source_id
            or slate_run.analyzer != "slate-tone"
            or slate_run.status != "complete"
        ):
            return None

        analyzer = get_analyzer(_SLATE_TRANSCRIPT_ANALYZER)
        existing = db.scalars(
            select(AnalysisRun)
            .where(
                AnalysisRun.source_id == source_id,
                AnalysisRun.analyzer == _SLATE_TRANSCRIPT_ANALYZER,
            )
            .order_by(AnalysisRun.created_at.desc())
        ).first()
        if (
            existing
            and existing.analyzer_version == analyzer.version
            and existing.status in {"queued", "running", "complete"}
        ):
            return existing.id

        events = db.scalars(
            select(Event)
            .where(Event.run_id == slate_run_id)
            .order_by(Event.start_seconds.asc(), Event.end_seconds.asc())
        ).all()
        windows = build_slate_transcription_windows(
            list(events),
            source_duration_seconds=float(source.duration_seconds or 0.0),
            post_seconds=settings.slate_transcription_post_seconds,
        )

        run = AnalysisRun(
            source_id=source_id,
            analyzer=analyzer.id,
            analyzer_version=analyzer.version,
            parameters={
                _PIPELINE_MARKER: _UPLOAD_PIPELINE,
                "source_slate_run_id": slate_run_id,
                "windows": windows,
                "sample_rate": settings.slate_transcription_sample_rate,
                "model": settings.slate_transcription_model,
                "language": settings.slate_transcription_language,
                _BIRDNET_PARAMETERS: dict(
                    (slate_run.parameters or {}).get(_BIRDNET_PARAMETERS, {})
                ),
            },
            status="queued",
        )
        db.add(run)
        db.flush()
        job = Job(kind="analysis", source_id=source_id, run_id=run.id)
        db.add(job)
        db.flush()
        dispatch = (job.id, run.id)
        db.commit()

    if dispatch:
        dispatch_analysis(*dispatch)
        return dispatch[1]
    return None


def queue_birdnet_analysis(source_id: str, upstream_run_id: str) -> str | None:
    dispatch: tuple[str, str] | None = None
    with SessionLocal() as db:
        source = db.get(SourceRecording, source_id)
        upstream = db.get(AnalysisRun, upstream_run_id)
        if (
            not source
            or not upstream
            or upstream.source_id != source_id
            or upstream.status not in _TERMINAL_RUN_STATES
        ):
            return None

        analyzer = get_analyzer(_BIRDNET_ANALYZER)
        existing = db.scalars(
            select(AnalysisRun)
            .where(
                AnalysisRun.source_id == source_id,
                AnalysisRun.analyzer == _BIRDNET_ANALYZER,
            )
            .order_by(AnalysisRun.created_at.desc())
        ).first()
        if (
            existing
            and (existing.parameters or {}).get(_PIPELINE_MARKER) == _UPLOAD_PIPELINE
            and existing.analyzer_version == analyzer.version
            and existing.status in {"queued", "running", "complete"}
        ):
            return existing.id

        parameters = default_parameters(_BIRDNET_ANALYZER)
        parameters.update(
            dict((upstream.parameters or {}).get(_BIRDNET_PARAMETERS, {}))
        )
        parameters[_PIPELINE_MARKER] = _UPLOAD_PIPELINE
        parameters["source_upstream_run_id"] = upstream_run_id

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
        dispatch = (job.id, run.id)
        db.commit()

    if dispatch:
        dispatch_analysis(*dispatch)
        return dispatch[1]
    return None


def resume_upload_pipeline(source_id: str) -> str | None:
    """Resume missing/stale derived stages for an existing recording."""
    settings = get_settings()
    backfill_location_from_opening_slate(source_id)

    with SessionLocal() as db:
        runs = db.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.source_id == source_id)
            .order_by(AnalysisRun.created_at.desc())
        ).all()

        slate_run = next(
            (
                run
                for run in runs
                if run.analyzer == "slate-tone"
                and (run.parameters or {}).get(_PIPELINE_MARKER) == _UPLOAD_PIPELINE
                and run.status == "complete"
            ),
            None,
        )
        if not slate_run:
            return None

        transcript_run = next(
            (
                run
                for run in runs
                if run.analyzer == _SLATE_TRANSCRIPT_ANALYZER
                and (run.parameters or {}).get(_PIPELINE_MARKER) == _UPLOAD_PIPELINE
            ),
            None,
        )

    if settings.accelerator.lower() in {"metal", "mps"}:
        transcript_analyzer = get_analyzer(_SLATE_TRANSCRIPT_ANALYZER)
        if (
            transcript_run is None
            or transcript_run.analyzer_version != transcript_analyzer.version
            or transcript_run.status == "failed"
        ):
            return queue_slate_transcription(source_id, slate_run.id)
        if transcript_run.status in {"queued", "running"}:
            return transcript_run.id
        if transcript_run.status == "complete":
            backfill_location_from_opening_slate(source_id)
            return queue_birdnet_analysis(source_id, transcript_run.id)

    return queue_birdnet_analysis(source_id, slate_run.id)


def maybe_queue_audacity_export(source_id: str, *, force: bool = False) -> str | None:
    """Queue one Audacity export once all automatic analyzers have finished."""
    if not get_settings().auto_audacity_export and not force:
        return None

    settings = get_settings()
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
        expected = {"slate-tone", _BIRDNET_ANALYZER}
        slate_run = latest_by_analyzer.get("slate-tone")
        if settings.accelerator.lower() in {"metal", "mps"} and (
            _SLATE_TRANSCRIPT_ANALYZER in latest_by_analyzer
            or (slate_run and slate_run.status == "complete")
        ):
            expected.add(_SLATE_TRANSCRIPT_ANALYZER)

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
        if existing_jobs:
            latest = existing_jobs[0]
            if latest.status in {"queued", "running"}:
                return latest.id
            if latest.status == "complete" and not force:
                return latest.id

        job = Job(kind="audacity-export", source_id=source_id, status="queued")
        db.add(job)
        db.commit()
        job_id = job.id

    dispatch_audacity_export(job_id, source_id)
    return job_id
