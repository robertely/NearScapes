from __future__ import annotations

import math
import struct
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace

from nearscapes.db.models import AnalysisRun, Event
from nearscapes.exports.audacity_project import build_audacity_project


def _write_fixture(path: Path) -> None:
    sample_rate = 8000
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        samples = [
            int(3000 * math.sin(2 * math.pi * 440 * index / sample_rate))
            for index in range(sample_rate)
        ]
        output.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="nearscapes aup3 smoke ") as temp:
        root = Path(temp)
        audio = root / "Two Ponds Walk.wav"
        project = root / "Two Ponds Walk.analysis.aup3"
        _write_fixture(audio)

        source = SimpleNamespace(
            storage_path=str(audio),
            filename="Two Ponds Walk.wav",
        )
        run = AnalysisRun(
            id="smoke-run",
            source_id="smoke-source",
            analyzer="slate-tone",
            analyzer_version="smoke",
            parameters={},
            status="complete",
        )
        event = Event(
            id="smoke-event",
            run_id=run.id,
            start_seconds=0.2,
            end_seconds=0.6,
            category="smoke",
            label="Smoke detection",
            confidence=0.9,
            attributes={},
        )

        build_audacity_project(source, [(run, [event])], project)
        print(f"created {project} ({project.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
