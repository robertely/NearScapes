from __future__ import annotations

import re
import threading
from functools import lru_cache
from pathlib import Path

import httpx
import numpy as np

from nearscapes.analyzers.base import AnalyzerContext, Detection
from nearscapes.audio.pcm import ensure_mono_pcm, load_pcm
from nearscapes.config import get_settings

_CPU_WHISPER_LOCK = threading.Lock()


@lru_cache(maxsize=2)
def _cpu_whisper_model(model: str, cache_root: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "CPU slate transcription requires the NearScapes cpu-asr dependency"
        ) from exc

    root = Path(cache_root)
    root.mkdir(parents=True, exist_ok=True)
    return WhisperModel(
        model,
        device="cpu",
        compute_type="int8",
        download_root=str(root),
    )


def _transcribe_cpu(
    samples: np.ndarray,
    *,
    model: str,
    language: str,
    cache_root: Path,
) -> dict:
    whisper = _cpu_whisper_model(model, str(cache_root))
    with _CPU_WHISPER_LOCK:
        segments, info = whisper.transcribe(
            samples,
            language=language or None,
            task="transcribe",
            beam_size=5,
            condition_on_previous_text=False,
            word_timestamps=False,
            vad_filter=False,
        )
        text = " ".join(
            segment.text.strip()
            for segment in segments
            if segment.text and segment.text.strip()
        ).strip()

    return {
        "text": text,
        "model": model,
        "language": getattr(info, "language", None) or language,
        "backend": "faster-whisper",
        "accelerator": "cpu",
    }


def _transcribe_metal(
    samples: np.ndarray,
    *,
    sample_rate: int,
    model: str,
    language: str,
    inference_url: str,
    timeout_seconds: float,
) -> dict:
    response = httpx.post(
        f"{inference_url.rstrip('/')}/v1/whisper/transcribe",
        files={
            "file": (
                "segment.f32",
                samples.tobytes(),
                "application/octet-stream",
            )
        },
        data={
            "sample_rate": str(sample_rate),
            "model": model,
            "language": language,
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    payload.setdefault("backend", "mlx-whisper")
    payload.setdefault("accelerator", "metal")
    return payload


_TIME_RE = re.compile(
    r"""^\s*(?:(?:the\s+)?time\s+is\s+)?(?P<hour>1[0-2]|0?[1-9])"""
    r"""(?:\s*[:.]\s*|\s+)(?P<minute>[0-5]\d)\s*"""
    r"""(?P<meridiem>[ap])\.?\s*m\.?\b""",
    re.IGNORECASE,
)


def parse_announced_time(text: str) -> str | None:
    match = _TIME_RE.match(text)
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    meridiem = match.group("meridiem").upper()
    return f"{hour}:{minute:02d} {meridiem}M"


_COORDINATE = r"(?:negative\s+|minus\s+|-)?\d{1,3}(?:\.\d+)"
_COORDINATE_SUFFIX = r"(?:\s*degrees?)?"
_LABELLED_LOCATION_RE = re.compile(
    rf"""\blat(?:itude)?\s*(?:(?:is|of|equals?)\s*)?"""
    rf"""(?P<lat>{_COORDINATE}){_COORDINATE_SUFFIX}\s*[,;/]?\s*"""
    rf"""(?:and\s+)?(?:lon(?:gitude)?|long)\s*"""
    rf"""(?:(?:is|of|equals?)\s*)?(?P<lon>{_COORDINATE}){_COORDINATE_SUFFIX}""",
    re.IGNORECASE,
)
_RECORDING_AT_LOCATION_RE = re.compile(
    rf"""\brecording\s+at\s+(?:coordinates?\s+)?"""
    rf"""(?P<lat>{_COORDINATE}){_COORDINATE_SUFFIX}\s*"""
    rf"""(?:,|;|/|\band\b|\bby\b)?\s*"""
    rf"""(?P<lon>{_COORDINATE}){_COORDINATE_SUFFIX}""",
    re.IGNORECASE,
)


def _coordinate_value(value: str) -> float:
    normalized = value.strip().lower()
    sign = -1.0 if normalized.startswith(("negative", "minus", "-")) else 1.0
    normalized = re.sub(r"^(?:negative|minus)\s+", "", normalized)
    normalized = normalized.removeprefix("-").strip()
    return sign * float(normalized)


def parse_opening_location(text: str) -> dict | None:
    normalized = text.replace("−", "-").replace("–", "-")
    normalized = re.sub(
        r"(?<=\d)\s+(?:point|dot)\s+(?=\d)",
        ".",
        normalized,
        flags=re.IGNORECASE,
    )
    for pattern in (_LABELLED_LOCATION_RE, _RECORDING_AT_LOCATION_RE):
        match = pattern.search(normalized)
        if not match:
            continue
        latitude = _coordinate_value(match.group("lat"))
        longitude = _coordinate_value(match.group("lon"))
        if -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0:
            return {
                "latitude": latitude,
                "longitude": longitude,
                "source": "opening-slate",
            }
    return None


class SlateTranscriptAnalyzer:
    id = "slate-transcript"
    version = "0.4.0"
    display_name = "Slate Speech Transcript"

    def analyze(self, context: AnalyzerContext, parameters: dict) -> list[Detection]:
        settings = get_settings()
        windows = list(parameters.get("windows") or [])
        if not windows:
            return []

        accelerator = settings.accelerator.lower()

        sample_rate = int(
            parameters.get("sample_rate", settings.slate_transcription_sample_rate)
        )
        model = str(parameters.get("model", settings.slate_transcription_model))
        language = str(parameters.get("language", settings.slate_transcription_language))

        pcm_path = ensure_mono_pcm(context.source_path, context.cache_dir, sample_rate)
        samples = load_pcm(pcm_path)
        duration_seconds = len(samples) / sample_rate
        detections: list[Detection] = []

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

            if accelerator in {"metal", "mps"}:
                payload = _transcribe_metal(
                    segment,
                    sample_rate=sample_rate,
                    model=model,
                    language=language,
                    inference_url=settings.inference_url,
                    timeout_seconds=settings.inference_timeout_seconds,
                )
            else:
                payload = _transcribe_cpu(
                    segment,
                    model=model,
                    language=language,
                    cache_root=settings.model_cache / "whisper",
                )

            text = str(payload.get("text") or "").strip()
            if not text:
                continue

            boundary = str(window.get("boundary", "slate"))
            announced_time = None
            opening_location = (
                parse_opening_location(text) if boundary == "opening-slate" else None
            )
            category = "slate-transcript"
            label = "Opening slate transcript"

            if boundary == "note-slate-candidate":
                announced_time = parse_announced_time(text)
                if announced_time is None:
                    continue
                category = "slate-note"
                label = announced_time

            detections.append(
                Detection(
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    category=category,
                    label=label,
                    text=text,
                    attributes={
                        "boundary": boundary,
                        "announced_time": announced_time,
                        "location": opening_location,
                        "model": payload.get("model", model),
                        "language": payload.get("language", language),
                        "backend": payload.get("backend"),
                        "accelerator": payload.get("accelerator"),
                    },
                )
            )

        return detections
