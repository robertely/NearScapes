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
            and run.status in {"queued", "running", "complete"}
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
    accelerator = settings.accelerator.lower()
    transcription_model = (
        settings.slate_transcription_model
        if accelerator in {"metal", "mps"}
        else settings.slate_transcription_cpu_model
    )

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
                "model": transcription_model,
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
        source_location = (source.embedded_metadata or {}).get("location") or {}
        location_available = (
            source_location.get("latitude") is not None
            and source_location.get("longitude") is not None
        )
        if not location_available:
            return None

        existing_parameters = (existing.parameters or {}) if existing else {}
        existing_is_location_filtered = (
            existing_parameters.get("latitude") is not None
            and existing_parameters.get("longitude") is not None
        )
        if (
            existing
            and (existing.parameters or {}).get(_PIPELINE_MARKER) == _UPLOAD_PIPELINE
            and existing.analyzer_version == analyzer.version
        ):
            if existing.status in {"queued", "running"}:
                return existing.id
            if existing.status == "complete" and (
                not location_available or existing_is_location_filtered
            ):
                return existing.id

        parameters = default_parameters(_BIRDNET_ANALYZER)
        parameters.update(
            dict((upstream.parameters or {}).get(_BIRDNET_PARAMETERS, {}))
        )
        parameters[_PIPELINE_MARKER] = _UPLOAD_PIPELINE
        parameters["source_upstream_run_id"] = upstream_run_id
        parameters["latitude"] = float(source_location["latitude"])
        parameters["longitude"] = float(source_location["longitude"])
        parameters["location_source"] = source_location.get("source")

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


def advance_upload_pipeline(
    source_id: str,
    analyzer_parameter_overrides: dict[str, dict] | None = None,
) -> str | None:
    """Advance exactly one serial stage of the automatic upload pipeline."""
    settings = get_settings()
    if not settings.auto_analyze_uploads:
        return None

    overrides = analyzer_parameter_overrides or {}
    backfill_location_from_opening_slate(source_id)

    with SessionLocal() as db:
        source = db.get(SourceRecording, source_id)
        if not source or source.status != "ready":
            return None

        runs = db.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.source_id == source_id)
            .order_by(AnalysisRun.created_at.desc())
        ).all()

        active_slate = next(
            (
                run
                for run in runs
                if run.analyzer == "slate-tone"
                and run.status in {"queued", "running"}
            ),
            None,
        )
        completed_slate = next(
            (
                run
                for run in runs
                if run.analyzer == "slate-tone" and run.status == "complete"
            ),
            None,
        )

        if active_slate:
            return active_slate.id

        if not completed_slate:
            queued = queue_upload_analysis(source_id, overrides)
            return queued[0] if queued else None

        birdnet_overrides = dict(overrides.get(_BIRDNET_ANALYZER, {}))
        if birdnet_overrides:
            slate_parameters = dict(completed_slate.parameters or {})
            existing_overrides = dict(slate_parameters.get(_BIRDNET_PARAMETERS, {}))
            existing_overrides.update(birdnet_overrides)
            slate_parameters[_BIRDNET_PARAMETERS] = existing_overrides
            completed_slate.parameters = slate_parameters
            db.commit()

        transcript_run = next(
            (
                run
                for run in runs
                if run.analyzer == _SLATE_TRANSCRIPT_ANALYZER
            ),
            None,
        )

    transcript_analyzer = get_analyzer(_SLATE_TRANSCRIPT_ANALYZER)
    if transcript_run and transcript_run.status in {"queued", "running"}:
        return transcript_run.id
    if transcript_run and transcript_run.status == "failed":
        return transcript_run.id
    if (
        transcript_run is None
        or transcript_run.analyzer_version != transcript_analyzer.version
    ):
        return queue_slate_transcription(source_id, completed_slate.id)
    if transcript_run.status != "complete":
        return transcript_run.id

    location = backfill_location_from_opening_slate(source_id)
    if not location:
        return transcript_run.id
    upstream_run_id = transcript_run.id

    with SessionLocal() as db:
        runs = db.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.source_id == source_id)
            .order_by(AnalysisRun.created_at.desc())
        ).all()
        birdnet_analyzer = get_analyzer(_BIRDNET_ANALYZER)
        filtered_birdnet = next(
            (
                run
                for run in runs
                if run.analyzer == _BIRDNET_ANALYZER
                and run.analyzer_version == birdnet_analyzer.version
                and (run.parameters or {}).get("latitude") is not None
                and (run.parameters or {}).get("longitude") is not None
            ),
            None,
        )

    if filtered_birdnet:
        if filtered_birdnet.status in {"queued", "running", "failed"}:
            return filtered_birdnet.id
        if filtered_birdnet.status == "complete":
            return maybe_queue_audacity_export(source_id)

    return queue_birdnet_analysis(source_id, upstream_run_id)


def resume_upload_pipeline(source_id: str) -> str | None:
    """Compatibility wrapper for older callers."""
    return advance_upload_pipeline(source_id)


def upload_pipeline_status(source_id: str) -> dict:
    """Return explicit serial pipeline stage state for the UI."""
    backfill_location_from_opening_slate(source_id)

    with SessionLocal() as db:
        source = db.get(SourceRecording, source_id)
        if not source:
            return {
                "complete": False,
                "current_stage": "ingest",
                "location_ready": False,
                "slate_transcript_status": None,
                "birdnet_status": None,
                "birdnet_location_filtered": False,
                "stages": [],
            }

        runs = db.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.source_id == source_id)
            .order_by(AnalysisRun.created_at.desc())
        ).all()
        slate_run = next((run for run in runs if run.analyzer == "slate-tone"), None)
        transcript_run = next(
            (run for run in runs if run.analyzer == _SLATE_TRANSCRIPT_ANALYZER),
            None,
        )

        birdnet_analyzer = get_analyzer(_BIRDNET_ANALYZER)
        birdnet_run = next(
            (
                run
                for run in runs
                if run.analyzer == _BIRDNET_ANALYZER
                and run.analyzer_version == birdnet_analyzer.version
                and (run.parameters or {}).get("latitude") is not None
                and (run.parameters or {}).get("longitude") is not None
            ),
            None,
        )

        location = (source.embedded_metadata or {}).get("location")

        ingest_status = (
            "complete"
            if source.status == "ready"
            else "failed"
            if source.status == "failed"
            else "running"
            if source.status == "processing"
            else "pending"
        )
        slate_status = slate_run.status if slate_run else "pending"

        transcript_status = transcript_run.status if transcript_run else "pending"

        if location:
            location_status = "complete"
        elif transcript_run and transcript_run.status == "failed":
            location_status = "failed"
        elif transcript_run and transcript_run.status == "complete":
            location_status = "failed"
        elif transcript_status == "blocked":
            location_status = "blocked"
        else:
            location_status = "pending"

        birdnet_status = birdnet_run.status if birdnet_run else "pending"
        birdnet_location_filtered = bool(
            birdnet_run
            and (birdnet_run.parameters or {}).get("latitude") is not None
            and (birdnet_run.parameters or {}).get("longitude") is not None
        )

        stages = [
            {"id": "ingest", "label": "Prepare audio", "status": ingest_status},
            {"id": "slate-tone", "label": "Detect slate beeps", "status": slate_status},
            {
                "id": "slate-transcript",
                "label": "Transcribe opening slate",
                "status": transcript_status,
            },
            {"id": "location", "label": "Resolve location", "status": location_status},
            {
                "id": "birdnet",
                "label": "Location-filtered BirdNET",
                "status": birdnet_status,
            },
        ]

        current_stage = None
        for stage in stages:
            if stage["status"] not in {"complete", "skipped"}:
                current_stage = stage["id"]
                break

        complete = bool(
            location
            and birdnet_run
            and birdnet_run.status == "complete"
            and birdnet_location_filtered
            and transcript_run
            and transcript_run.status == "complete"
        )

        return {
            "complete": complete,
            "current_stage": current_stage,
            "location_ready": bool(location),
            "slate_transcript_status": (
                transcript_run.status if transcript_run else None
            ),
            "birdnet_status": birdnet_run.status if birdnet_run else None,
            "birdnet_location_filtered": birdnet_location_filtered,
            "stages": stages,
        }


def maybe_queue_audacity_export(source_id: str, *, force: bool = False) -> str | None:
    """Queue one Audacity export once all automatic analyzers have finished."""
    if not get_settings().auto_audacity_export and not force:
        return None

    if not force and not upload_pipeline_status(source_id)["complete"]:
        return None

    with SessionLocal() as db:
        source = db.scalar(
            select(SourceRecording)
            .where(SourceRecording.id == source_id)
            .with_for_update()
        )
        if not source:
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
