from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from nearscapes.analyzers.base import AnalyzerContext
from nearscapes.analyzers.registry import get_analyzer
from nearscapes.analyzers.slate_transcript import parse_opening_location
from nearscapes.audio.pcm import ensure_mono_pcm, load_pcm
from nearscapes.audio.probe import ffprobe
from nearscapes.audio.waveform import calculate_waveform, write_waveform
from nearscapes.config import get_settings
from nearscapes.db.models import AnalysisRun, Event, Job, SourceRecording
from nearscapes.db.session import SessionLocal
from nearscapes.pipeline import (
    maybe_queue_audacity_export,
    queue_birdnet_analysis,
    queue_slate_transcription,
    queue_upload_analysis,
)
from nearscapes.storage.local import LocalStorage

logger = logging.getLogger(__name__)
settings = get_settings()


def _now() -> datetime:
    return datetime.now(UTC)


def _fail_job(job_id: str, message: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job:
            job.status = "failed"
            job.error = message[:4000]
            job.finished_at = _now()
            db.commit()


def metadata_with_detected_location(metadata: dict, detections: list) -> dict:
    updated = dict(metadata or {})
    if updated.get("location"):
        return updated

    for detection in detections:
        attributes = detection.attributes or {}
        location = attributes.get("location")
        if not location and getattr(detection, "category", None) == "slate-transcript":
            text = getattr(detection, "text", None)
            if text:
                location = parse_opening_location(text)
        if location:
            updated["location"] = dict(location)
            break
    return updated


def ingest_source_impl(
    job_id: str,
    source_id: str,
    analyzer_parameter_overrides: dict[str, dict] | None = None,
) -> None:
    storage = LocalStorage()
    try:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            source = db.get(SourceRecording, source_id)
            if not job or not source:
                return
            job.status = "running"
            job.started_at = _now()
            job.progress = 0.05
            source.status = "processing"
            db.commit()
            source_path = Path(source.storage_path)
            source_sha = source.sha256

        metadata = ffprobe(source_path)
        cache_dir = storage.cache_dir(source_sha)
        pcm_path = ensure_mono_pcm(source_path, cache_dir, settings.pcm_sample_rate)
        samples = load_pcm(pcm_path)
        waveform = calculate_waveform(samples, settings.pcm_sample_rate, settings.waveform_points)
        waveform_path = cache_dir / "waveform.json"
        write_waveform(waveform_path, waveform)

        with SessionLocal() as db:
            source = db.get(SourceRecording, source_id)
            job = db.get(Job, job_id)
            if not source or not job:
                return
            source.duration_seconds = metadata["duration_seconds"]
            source.sample_rate = metadata["sample_rate"]
            source.channels = metadata["channels"]
            source.codec = metadata["codec"]
            source.embedded_metadata = {
                "tags": metadata.get("tags") or {},
                "location": metadata.get("location"),
            }
            source.waveform_path = str(waveform_path)
            source.status = "ready"
            source.error = None
            job.status = "complete"
            job.progress = 1.0
            job.finished_at = _now()
            db.commit()

        queue_upload_analysis(source_id, analyzer_parameter_overrides)
    except Exception as exc:
        logger.exception(
            "source ingestion failed", extra={"job_id": job_id, "source_id": source_id}
        )
        with SessionLocal() as db:
            source = db.get(SourceRecording, source_id)
            if source:
                source.status = "failed"
                source.error = str(exc)[:4000]
                db.commit()
        _fail_job(job_id, str(exc))
        raise


def execute_analysis_impl(job_id: str, run_id: str) -> None:
    storage = LocalStorage()
    source_id: str | None = None
    analyzer_id: str | None = None
    parameters: dict = {}
    try:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            run = db.get(AnalysisRun, run_id)
            if not job or not run:
                return
            source_id = run.source_id
            source = db.get(SourceRecording, source_id)
            if not source:
                raise RuntimeError("Source no longer exists")
            if source.status != "ready":
                raise RuntimeError(f"Source is not ready: {source.status}")
            job.status = "running"
            job.started_at = _now()
            job.progress = 0.05
            run.status = "running"
            run.started_at = _now()
            db.commit()
            analyzer_id = run.analyzer
            parameters = dict(run.parameters or {})
            if analyzer_id == "birdnet":
                location = (source.embedded_metadata or {}).get("location") or {}
                if location.get("latitude") is not None and location.get("longitude") is not None:
                    parameters.setdefault("latitude", float(location["latitude"]))
                    parameters.setdefault("longitude", float(location["longitude"]))
                    parameters.setdefault("location_source", location.get("source"))
                    run.parameters = parameters
            source_path = Path(source.storage_path)
            source_sha = source.sha256

        analyzer = get_analyzer(analyzer_id)
        context = AnalyzerContext(
            source_path=source_path,
            cache_dir=storage.cache_dir(source_sha),
            source_sha256=source_sha,
            source_filename=source.filename,
        )
        detections = analyzer.analyze(context, parameters)

        with SessionLocal() as db:
            run = db.get(AnalysisRun, run_id)
            job = db.get(Job, job_id)
            if not run or not job:
                return
            source = db.get(SourceRecording, run.source_id)
            if analyzer_id == "slate-transcript" and source:
                source.embedded_metadata = metadata_with_detected_location(
                    source.embedded_metadata or {},
                    detections,
                )
            for detection in detections:
                db.add(
                    Event(
                        run_id=run.id,
                        start_seconds=detection.start_seconds,
                        end_seconds=detection.end_seconds,
                        category=detection.category,
                        label=detection.label,
                        text=detection.text,
                        confidence=detection.confidence,
                        frequency_low_hz=detection.frequency_low_hz,
                        frequency_high_hz=detection.frequency_high_hz,
                        attributes=detection.attributes,
                    )
                )
            run.status = "complete"
            run.finished_at = _now()
            run.error = None
            job.status = "complete"
            job.progress = 1.0
            job.finished_at = _now()
            db.commit()

        is_upload_pipeline = parameters.get("_pipeline") == "upload"
        if source_id and is_upload_pipeline and analyzer_id == "slate-tone":
            transcript_run_id = queue_slate_transcription(source_id, run_id)
            if transcript_run_id is None:
                queue_birdnet_analysis(source_id, run_id)
        elif source_id and is_upload_pipeline and analyzer_id == "slate-transcript":
            with SessionLocal() as db:
                source = db.get(SourceRecording, source_id)
                location = (source.embedded_metadata or {}).get("location") if source else None
            if location:
                queue_birdnet_analysis(source_id, run_id)
        if source_id:
            maybe_queue_audacity_export(source_id)
    except Exception as exc:
        logger.exception("analysis failed", extra={"job_id": job_id, "run_id": run_id})
        with SessionLocal() as db:
            run = db.get(AnalysisRun, run_id)
            if run:
                run.status = "failed"
                run.error = str(exc)[:4000]
                run.finished_at = _now()
                db.commit()
        _fail_job(job_id, str(exc))
        if source_id and parameters.get("_pipeline") == "upload":
            if analyzer_id in {"slate-tone", "slate-transcript"}:
                queue_birdnet_analysis(source_id, run_id)
        if source_id:
            maybe_queue_audacity_export(source_id)
        raise
