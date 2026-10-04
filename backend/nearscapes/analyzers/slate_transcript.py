from __future__ import annotations

import httpx
import numpy as np

from nearscapes.analyzers.base import AnalyzerContext, Detection
from nearscapes.audio.pcm import ensure_mono_pcm, load_pcm
from nearscapes.config import get_settings


class SlateTranscriptAnalyzer:
    id = "slate-transcript"
    version = "0.1.0"
    display_name = "Slate Speech Transcript"

    def analyze(self, context: AnalyzerContext, parameters: dict) -> list[Detection]:
        settings = get_settings()
        windows = list(parameters.get("windows") or [])
        if not windows:
            return []

        if settings.accelerator != "metal":
            raise RuntimeError(
                "Slate transcription currently requires the Apple-Silicon Metal helper"
            )

        sample_rate = int(
            parameters.get("sample_rate", settings.slate_transcription_sample_rate)
        )
        model = str(parameters.get("model", settings.slate_transcription_model))
        language = str(parameters.get("language", settings.slate_transcription_language))

        pcm_path = ensure_mono_pcm(context.source_path, context.cache_dir, sample_rate)
        samples = load_pcm(pcm_path)
        duration_seconds = len(samples) / sample_rate
        detections: list[Detection] = []

        with httpx.Client(timeout=settings.inference_timeout_seconds) as client:
            for window in windows:
                start_seconds = max(0.0, float(window["start_seconds"]))
                end_seconds = min(
                    duration_seconds,
                    max(start_seconds, float(window["end_seconds"])),
                )
                if end_seconds - start_seconds < 0.1:
                    continue

                start_sample = int(round(start_seconds * sample_rate))
                end_sample = int(round(end_seconds * sample_rate))
                segment = np.asarray(samples[start_sample:end_sample], dtype="<f4")

                response = client.post(
                    f"{settings.inference_url.rstrip('/')}/v1/whisper/transcribe",
                    files={
                        "file": (
                            "segment.f32",
                            segment.tobytes(),
                            "application/octet-stream",
                        )
                    },
                    data={
                        "sample_rate": str(sample_rate),
                        "model": model,
                        "language": language,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                text = str(payload.get("text") or "").strip()
                if not text:
                    continue

                detections.append(
                    Detection(
                        start_seconds=start_seconds,
                        end_seconds=end_seconds,
                        category="speech-transcript",
                        label="Speech transcript",
                        text=text,
                        attributes={
                            "boundary": window.get("boundary", "slate"),
                            "model": payload.get("model", model),
                            "language": payload.get("language", language),
                            "backend": "mlx-whisper",
                            "accelerator": "metal",
                        },
                    )
                )

        return detections
