from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from nearscapes.analyzers.slate import detect_tones, pair_tones
from nearscapes.audio.pcm import ensure_mono_pcm, load_pcm
from nearscapes.audio.probe import ffprobe


def analyze_file(path: Path, sample_rate: int = 8000) -> dict:
    source = path.resolve()
    sha256 = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha256.update(chunk)

    metadata = ffprobe(source)
    with tempfile.TemporaryDirectory(prefix="nearscapes-smoke-") as temp:
        pcm_path = ensure_mono_pcm(source, Path(temp), sample_rate)
        samples = load_pcm(pcm_path)
        tones = detect_tones(samples, sample_rate)

    events = [
        {
            "start_seconds": round(tone.start_seconds, 3),
            "end_seconds": round(tone.end_seconds, 3),
            "category": "slate-marker",
            "label": "1 kHz marker",
            "median_tone_to_guard_db": round(tone.median_tone_to_guard_db, 2),
        }
        for tone in tones
    ]
    for first, second in pair_tones(tones, 60.0):
        events.append(
            {
                "start_seconds": round(first.end_seconds, 3),
                "end_seconds": round(second.start_seconds, 3),
                "category": "slate-region",
                "label": "Bracketed recording slate",
            }
        )

    return {
        "schema": "nearscapes/audio-analysis/v1",
        "source": {
            "filename": source.name,
            "sha256": sha256.hexdigest(),
            "duration_seconds": metadata["duration_seconds"],
            "sample_rate": metadata["sample_rate"],
            "channels": metadata["channels"],
            "codec": metadata["codec"],
        },
        "recording_metadata": {
            "location": metadata.get("location"),
        },
        "runs": [
            {
                "analyzer": "slate-tone",
                "analyzer_version": "0.1.0",
                "events": sorted(
                    events,
                    key=lambda item: (item["start_seconds"], item["end_seconds"]),
                ),
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the NearScapes first-slice analyzer locally")
    parser.add_argument("audio", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze_file(args.audio), indent=2))


if __name__ == "__main__":
    main()
