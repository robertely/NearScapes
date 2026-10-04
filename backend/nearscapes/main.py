from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from nearscapes.analyzers.registry import get_analyzer, list_analyzers
from nearscapes.api.schemas import CreateRunRequest
from nearscapes.config import get_settings
from nearscapes.db.models import AnalysisRun, Event, Job, SourceRecording
from nearscapes.db.session import get_db
from nearscapes.jobs.dispatch import dispatch_analysis, dispatch_ingest
from nearscapes.logging import configure_logging
from nearscapes.pipeline import (
    backfill_location_from_opening_slate,
    maybe_queue_audacity_export,
    queue_upload_analysis,
    resume_upload_pipeline,
)
from nearscapes.storage.local import LocalStorage

settings = get_settings()
configure_logging(settings.log_level)
storage = LocalStorage()
app = FastAPI(title="NearScapes", version="0.1.0")

DbSession = Annotated[Session, Depends(get_db)]
UploadedFile = Annotated[UploadFile, File(...)]


def source_payload(source: SourceRecording) -> dict:
    audacity_path = storage.audacity_project_path(source.sha256, source.filename)
    return {
        "id": source.id,
        "sha256": source.sha256,
        "filename": source.filename,
        "duration_seconds": source.duration_seconds,
        "sample_rate": source.sample_rate,
        "channels": source.channels,
        "codec": source.codec,
        "embedded_metadata": source.embedded_metadata or {},
        "location": (source.embedded_metadata or {}).get("location"),
        "status": source.status,
        "error": source.error,
        "created_at": source.created_at.isoformat(),
        "audacity_project_ready": audacity_path.exists(),
    }


def run_payload(run: AnalysisRun) -> dict:
    return {
        "id": run.id,
        "source_id": run.source_id,
        "analyzer": run.analyzer,
        "analyzer_version": run.analyzer_version,
        "parameters": run.parameters,
        "status": run.status,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def event_payload(event: Event) -> dict:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "start_seconds": event.start_seconds,
        "end_seconds": event.end_seconds,
        "category": event.category,
        "label": event.label,
        "confidence": event.confidence,
        "text": event.text,
        "frequency_low_hz": event.frequency_low_hz,
        "frequency_high_hz": event.frequency_high_hz,
        "attributes": event.attributes,
    }


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/sources")
def list_sources(db: DbSession) -> list[dict]:
    sources = db.scalars(select(SourceRecording).order_by(SourceRecording.created_at.desc())).all()
    return [source_payload(source) for source in sources]


@app.post("/api/sources", status_code=202)
async def upload_source(
    file: UploadedFile,
    db: DbSession,
    birdnet_confidence: Annotated[float, Form()] = settings.birdnet_confidence_default,
) -> dict:
    if not 0.0 <= birdnet_confidence <= 1.0:
        raise HTTPException(400, "birdnet_confidence must be between 0 and 1")
    analysis_overrides = {"birdnet": {"confidence": birdnet_confidence}}

    filename = Path(file.filename or "upload").name
    hasher = hashlib.sha256()
    byte_count = 0
    temporary = storage.create_upload_temp()
    try:
        with temporary.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                byte_count += len(chunk)
                if byte_count > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=413, detail="Upload exceeds configured size limit"
                    )
                hasher.update(chunk)
                output.write(chunk)

        sha256 = hasher.hexdigest()
        existing = db.scalar(select(SourceRecording).where(SourceRecording.sha256 == sha256))
        if existing:
            if existing.status == "ready":
                backfill_location_from_opening_slate(existing.id)
                queue_upload_analysis(existing.id, analysis_overrides)
                resume_upload_pipeline(existing.id)
                maybe_queue_audacity_export(existing.id)
                db.refresh(existing)
            return {"source": source_payload(existing), "job": None, "deduplicated": True}

        destination = storage.source_path(sha256, filename)
        temporary.replace(destination)
        source = SourceRecording(
            sha256=sha256,
            filename=filename,
            storage_path=str(destination),
            status="uploaded",
        )
        db.add(source)
        db.flush()
        job = Job(kind="ingest", source_id=source.id)
        db.add(job)
        db.commit()
        dispatch_ingest(job.id, source.id, analysis_overrides)
        return {
            "source": source_payload(source),
            "job": {"id": job.id, "status": job.status},
            "deduplicated": False,
        }
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()


@app.get("/api/sources/{source_id}")
def get_source(source_id: str, db: DbSession) -> dict:
    source = db.get(SourceRecording, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    if not (source.embedded_metadata or {}).get("location"):
        backfill_location_from_opening_slate(source_id)
        resume_upload_pipeline(source_id)
        db.refresh(source)
    return source_payload(source)


@app.get("/api/sources/{source_id}/analysis")
def get_analysis(source_id: str, db: DbSession) -> dict:
    source = db.get(SourceRecording, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    if not (source.embedded_metadata or {}).get("location"):
        backfill_location_from_opening_slate(source_id)
        resume_upload_pipeline(source_id)
        db.refresh(source)
    runs = db.scalars(
        select(AnalysisRun)
        .where(AnalysisRun.source_id == source_id)
        .order_by(AnalysisRun.created_at.asc())
    ).all()
    run_results = []
    for run in runs:
        events = db.scalars(
            select(Event).where(Event.run_id == run.id).order_by(Event.start_seconds.asc())
        ).all()
        run_results.append(
            {**run_payload(run), "events": [event_payload(event) for event in events]}
        )
    return {
        "schema": "nearscapes/audio-analysis/v1",
        "source": source_payload(source),
        "recording_metadata": {
            "location": (source.embedded_metadata or {}).get("location"),
        },
        "runs": run_results,
    }





@app.get("/api/sources/{source_id}/audacity-status")
def get_audacity_status(source_id: str, db: DbSession) -> dict:
    source = db.get(SourceRecording, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    path = storage.audacity_project_path(source.sha256, source.filename)
    job = db.scalar(
        select(Job)
        .where(Job.source_id == source_id, Job.kind == "audacity-export")
        .order_by(Job.created_at.desc())
    )
    return {
        "ready": path.exists(),
        "job": (
            {
                "id": job.id,
                "status": job.status,
                "progress": job.progress,
                "error": job.error if job.status == "failed" else None,
            }
            if job
            else None
        ),
    }


@app.post("/api/sources/{source_id}/audacity-project", status_code=202)
def queue_audacity_project(source_id: str, db: DbSession) -> dict:
    source = db.get(SourceRecording, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    job_id = maybe_queue_audacity_export(source_id, force=True)
    return {"job_id": job_id}


@app.get("/api/sources/{source_id}/audacity-project")
def get_audacity_project(source_id: str, db: DbSession):
    source = db.get(SourceRecording, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    path = storage.audacity_project_path(source.sha256, source.filename)
    if not path.exists():
        raise HTTPException(409, "Audacity project is not ready")
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/octet-stream",
    )


@app.get("/api/sources/{source_id}/audio")
def get_audio(source_id: str, db: DbSession):
    source = db.get(SourceRecording, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    media_type = mimetypes.guess_type(source.filename)[0] or "application/octet-stream"
    return FileResponse(source.storage_path, filename=source.filename, media_type=media_type)


@app.get("/api/sources/{source_id}/waveform")
def get_waveform(source_id: str, db: DbSession) -> dict:
    source = db.get(SourceRecording, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    if source.status != "ready" or not source.waveform_path:
        raise HTTPException(409, f"Waveform not ready; source status is {source.status}")
    return json.loads(Path(source.waveform_path).read_text())


@app.get("/api/analyzers")
def analyzers() -> list[dict]:
    return list_analyzers()


@app.post("/api/sources/{source_id}/runs", status_code=202)
def create_run(source_id: str, request: CreateRunRequest, db: DbSession) -> dict:
    source = db.get(SourceRecording, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    if source.status != "ready":
        raise HTTPException(409, f"Source is not ready: {source.status}")
    try:
        analyzer = get_analyzer(request.analyzer)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc
    run = AnalysisRun(
        source_id=source.id,
        analyzer=analyzer.id,
        analyzer_version=analyzer.version,
        parameters=request.parameters,
        status="queued",
    )
    db.add(run)
    db.flush()
    job = Job(kind="analysis", source_id=source.id, run_id=run.id)
    db.add(job)
    db.commit()
    dispatch_analysis(job.id, run.id)
    return {"run": run_payload(run), "job": {"id": job.id, "status": job.status}}


@app.get("/api/sources/{source_id}/runs")
def list_runs(source_id: str, db: DbSession) -> list[dict]:
    runs = db.scalars(
        select(AnalysisRun)
        .where(AnalysisRun.source_id == source_id)
        .order_by(AnalysisRun.created_at.asc())
    ).all()
    return [run_payload(run) for run in runs]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, db: DbSession) -> dict:
    run = db.get(AnalysisRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run_payload(run)


@app.get("/api/runs/{run_id}/events")
def list_events(run_id: str, db: DbSession) -> list[dict]:
    run = db.get(AnalysisRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    events = db.scalars(
        select(Event).where(Event.run_id == run_id).order_by(Event.start_seconds.asc())
    ).all()
    return [event_payload(event) for event in events]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, db: DbSession) -> dict:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "source_id": job.source_id,
        "run_id": job.run_id,
        "error": job.error,
    }


frontend = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
