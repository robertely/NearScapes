from __future__ import annotations

import configparser
import json
import logging
import os
import select as io_select
import signal
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from nearscapes.analyzers.registry import get_analyzer
from nearscapes.config import get_settings
from nearscapes.db.models import AnalysisRun, Event, Job, SourceRecording
from nearscapes.db.session import SessionLocal
from nearscapes.storage.local import LocalStorage

logger = logging.getLogger(__name__)
settings = get_settings()


def _now() -> datetime:
    return datetime.now(UTC)


def _clean_label(value: str) -> str:
    return " ".join(value.replace("\t", " ").replace("\n", " ").split()).replace("'", "’")


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "’") + "'"


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


class AudacityPipe:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.uid = os.getuid()
        self.to_pipe = Path(f"/tmp/audacity_script_pipe.to.{self.uid}")
        self.from_pipe = Path(f"/tmp/audacity_script_pipe.from.{self.uid}")
        self.process: subprocess.Popen | None = None
        self.to_fd: int | None = None
        self.from_fd: int | None = None
        self.log_handle = None

    @property
    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["XDG_CONFIG_HOME"] = str(self.home / ".config")
        env["NO_AT_BRIDGE"] = "1"
        return env

    def _launch(self) -> subprocess.Popen:
        log_path = self.home / "audacity.log"
        self.log_handle = log_path.open("ab")
        return subprocess.Popen(
            ["xvfb-run", "-a", "dbus-run-session", "--", "audacity"],
            env=self.env,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def bootstrap_module(self) -> None:
        module_path = Path("/usr/lib/audacity/modules/mod-script-pipe.so")
        if not module_path.exists():
            candidates = list(Path("/usr/lib").glob("**/mod-script-pipe.so"))
            if not candidates:
                raise RuntimeError("mod-script-pipe.so is not installed")
            module_path = candidates[0]

        config_path = self.home / ".config" / "audacity" / "audacity.cfg"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        parser = configparser.RawConfigParser(strict=False)
        parser.optionxform = str
        if config_path.exists():
            parser.read(config_path)
        for section in ("Module", "ModulePath", "ModuleDateTime"):
            if not parser.has_section(section):
                parser.add_section(section)

        parser.set("Module", "mod-script-pipe", "1")
        parser.set("ModulePath", "mod-script-pipe", str(module_path))
        modified = datetime.fromtimestamp(module_path.stat().st_mtime).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        parser.set("ModuleDateTime", "mod-script-pipe", modified)

        with config_path.open("w") as handle:
            parser.write(handle, space_around_delimiters=False)

    def start(self) -> None:
        self.to_pipe.unlink(missing_ok=True)
        self.from_pipe.unlink(missing_ok=True)
        self.process = self._launch()

        deadline = time.monotonic() + settings.audacity_start_timeout_seconds
        while time.monotonic() < deadline:
            if self.to_pipe.exists() and self.from_pipe.exists():
                break
            if self.process.poll() is not None:
                raise RuntimeError("Audacity exited before creating scripting pipes")
            time.sleep(0.1)
        else:
            raise TimeoutError("Timed out waiting for Audacity scripting pipes")

        self.to_fd = os.open(self.to_pipe, os.O_WRONLY)
        self.from_fd = os.open(self.from_pipe, os.O_RDONLY | os.O_NONBLOCK)

    def command(self, command: str) -> str:
        if self.to_fd is None or self.from_fd is None:
            raise RuntimeError("Audacity scripting pipe is not connected")

        os.write(self.to_fd, (command + "\n").encode())
        deadline = time.monotonic() + settings.audacity_command_timeout_seconds
        chunks: list[bytes] = []

        while time.monotonic() < deadline:
            ready, _, _ = io_select.select([self.from_fd], [], [], 0.25)
            if not ready:
                continue
            data = os.read(self.from_fd, 65536)
            if data:
                chunks.append(data)
                decoded = b"".join(chunks).decode(errors="replace")
                if "BatchCommand finished:" in decoded:
                    if "BatchCommand finished: Failed!" in decoded:
                        raise RuntimeError(f"Audacity command failed: {command}\n{decoded}")
                    return decoded
            elif self.process and self.process.poll() is not None:
                break

        raise TimeoutError(f"Timed out waiting for Audacity command: {command}")

    def close(self) -> None:
        if self.to_fd is not None:
            os.close(self.to_fd)
            self.to_fd = None
        if self.from_fd is not None:
            os.close(self.from_fd)
            self.from_fd = None
        if self.process:
            _stop_process(self.process)
            self.process = None
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None
        self.to_pipe.unlink(missing_ok=True)
        self.from_pipe.unlink(missing_ok=True)


def _track_name(run: AnalysisRun) -> str:
    try:
        display = get_analyzer(run.analyzer).display_name
    except KeyError:
        display = run.analyzer
    return f"{display} · {run.id[:6]}"


def _event_label(event: Event) -> str:
    text = event.text or event.label
    if event.confidence is not None:
        text = f"{text} [{event.confidence:.0%}]"
    return _clean_label(text)[:240]


def build_audacity_project(
    source: SourceRecording,
    runs: list[tuple[AnalysisRun, list[Event]]],
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_name(f".{output.stem}.tmp.aup3")
    temporary_output.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="nearscapes-audacity-") as temp:
        home = Path(temp) / "home"
        home.mkdir(parents=True)
        pipe = AudacityPipe(home)
        try:
            pipe.bootstrap_module()
            pipe.start()

            pipe.command(f"Import2: Filename={_quoted(str(Path(source.storage_path)))}")
            pipe.command("SelectTracks: Track=0 TrackCount=1 Mode=Set")
            pipe.command(
                f"SetTrackStatus: Name={_quoted(_clean_label(source.filename))} "
                "Selected=1 Focused=1"
            )

            track_index = 1
            for run, events in runs:
                pipe.command("NewLabelTrack:")
                pipe.command(
                    f"SelectTracks: Track={track_index} TrackCount=1 Mode=Set"
                )
                pipe.command(
                    f"SetTrackStatus: Name={_quoted(_track_name(run))} "
                    "Selected=1 Focused=1"
                )
                for label_index, event in enumerate(events):
                    start = max(0.0, float(event.start_seconds))
                    end = max(start, float(event.end_seconds))
                    pipe.command(
                        f"SelectTime: Start={start:.6f} End={end:.6f} "
                        "RelativeTo=ProjectStart"
                    )
                    pipe.command("AddLabel:")
                    pipe.command(
                        f"SetLabel: Label={label_index} "
                        f"Text={_quoted(_event_label(event))} "
                        f"Start={start:.6f} End={end:.6f}"
                    )
                track_index += 1

            pipe.command(
                f"SaveProject2: Filename={_quoted(str(temporary_output))} "
                "AddToHistory=0 Compress=0"
            )
        finally:
            pipe.close()

    if not temporary_output.exists() or temporary_output.stat().st_size < 4096:
        raise RuntimeError("Audacity did not create a usable AUP3 project")
    with temporary_output.open("rb") as handle:
        if handle.read(16) != b"SQLite format 3\x00":
            raise RuntimeError("Audacity export is not an AUP3 SQLite project")
    temporary_output.replace(output)


def export_audacity_impl(job_id: str, source_id: str) -> None:
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
            db.commit()

            runs = db.scalars(
                select(AnalysisRun)
                .where(
                    AnalysisRun.source_id == source_id,
                    AnalysisRun.status == "complete",
                )
                .order_by(AnalysisRun.created_at.asc())
            ).all()
            run_events = []
            for run in runs:
                events = db.scalars(
                    select(Event)
                    .where(Event.run_id == run.id)
                    .order_by(Event.start_seconds.asc(), Event.end_seconds.asc())
                ).all()
                run_events.append((run, list(events)))

            source_data = {
                "id": source.id,
                "sha256": source.sha256,
                "filename": source.filename,
                "storage_path": source.storage_path,
            }

        detached_source = SourceRecording(**source_data)
        output = storage.audacity_project_path(
            detached_source.sha256,
            detached_source.filename,
        )
        build_audacity_project(detached_source, run_events, output)

        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if not job:
                return
            job.status = "complete"
            job.progress = 1.0
            job.error = json.dumps({"path": str(output)})
            job.finished_at = _now()
            db.commit()
    except Exception as exc:
        logger.exception(
            "Audacity project export failed",
            extra={"job_id": job_id, "source_id": source_id},
        )
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job:
                job.status = "failed"
                job.error = str(exc)[:4000]
                job.finished_at = _now()
                db.commit()
        raise
