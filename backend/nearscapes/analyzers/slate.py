from dataclasses import dataclass

import numpy as np

from nearscapes.analyzers.base import AnalyzerContext, Detection
from nearscapes.audio.pcm import ensure_mono_pcm, load_pcm
from nearscapes.config import get_settings


@dataclass(frozen=True)
class ToneRegion:
    start_seconds: float
    end_seconds: float
    median_tone_to_guard_db: float


def detect_tones(
    samples: np.ndarray,
    sample_rate: int,
    *,
    target_hz: float = 1000.0,
    frequency_tolerance_hz: float = 30.0,
    expected_duration_seconds: float = 2.0,
    duration_tolerance_seconds: float = 0.5,
    min_tone_to_guard_db: float = 25.0,
    frame_seconds: float = 0.05,
) -> list[ToneRegion]:
    frame_size = max(64, round(sample_rate * frame_seconds))
    frame_count = len(samples) // frame_size
    if frame_count == 0:
        return []

    frequencies = np.fft.rfftfreq(frame_size, 1.0 / sample_rate)
    target_band = np.abs(frequencies - target_hz) <= frequency_tolerance_hz
    if not np.any(target_band):
        raise ValueError("Sample rate/frame size cannot resolve slate detector target band")
    guard = (
        (frequencies >= target_hz - 300)
        & (frequencies <= target_hz + 300)
        & (np.abs(frequencies - target_hz) >= 80)
    )
    if not np.any(guard):
        raise ValueError("Sample rate/frame size cannot resolve slate detector guard band")

    window = np.hanning(frame_size).astype(np.float32)
    active = np.zeros(frame_count, dtype=bool)
    ratios = np.full(frame_count, -120.0, dtype=np.float32)

    batch_frames = 4096
    for frame_start in range(0, frame_count, batch_frames):
        frame_end = min(frame_count, frame_start + batch_frames)
        first_sample = frame_start * frame_size
        last_sample = frame_end * frame_size
        block = np.asarray(samples[first_sample:last_sample], dtype=np.float32)
        frames = block.reshape(frame_end - frame_start, frame_size)
        weighted = frames * window
        spectrum = np.abs(np.fft.rfft(weighted, axis=1)) ** 2
        target_power = np.max(spectrum[:, target_band], axis=1)
        guard_power = np.mean(spectrum[:, guard], axis=1) + 1e-12
        tone_to_guard_db = 10.0 * np.log10((target_power + 1e-12) / guard_power)
        rms = np.sqrt(np.mean(weighted * weighted, axis=1))
        ratios[frame_start:frame_end] = tone_to_guard_db
        active[frame_start:frame_end] = (tone_to_guard_db >= min_tone_to_guard_db) & (
            rms >= 1e-4
        )

    # Fill tiny one-frame dropouts and merge short gaps. Real phone-speaker playback can
    # contain brief broadband transients while the 1 kHz carrier is still present.
    if len(active) >= 3:
        holes = (~active[1:-1]) & active[:-2] & active[2:]
        active[1:-1][holes] = True

    changes = np.diff(np.r_[False, active, False].astype(np.int8))
    starts = np.where(changes == 1)[0]
    ends = np.where(changes == -1)[0]

    merged: list[list[int]] = []
    merge_gap_frames = max(1, round(0.15 / frame_seconds))
    for start, end in zip(starts, ends, strict=True):
        if merged and start - merged[-1][1] <= merge_gap_frames:
            merged[-1][1] = int(end)
        else:
            merged.append([int(start), int(end)])

    minimum = expected_duration_seconds - duration_tolerance_seconds
    maximum = expected_duration_seconds + duration_tolerance_seconds
    regions: list[ToneRegion] = []
    for start, end in merged:
        duration = (end - start) * frame_seconds
        if minimum <= duration <= maximum:
            regions.append(
                ToneRegion(
                    start_seconds=start * frame_seconds,
                    end_seconds=end * frame_seconds,
                    median_tone_to_guard_db=float(np.median(ratios[start:end])),
                )
            )
    return regions


def pair_tones(
    tones: list[ToneRegion], max_gap_seconds: float
) -> list[tuple[ToneRegion, ToneRegion]]:
    pairs = []
    index = 0
    while index + 1 < len(tones):
        first, second = tones[index], tones[index + 1]
        gap = second.start_seconds - first.end_seconds
        if 0.25 <= gap <= max_gap_seconds:
            pairs.append((first, second))
            index += 2
        else:
            index += 1
    return pairs


class SlateToneAnalyzer:
    id = "slate-tone"
    version = "0.1.0"
    display_name = "Recording Slate (1 kHz)"

    def analyze(self, context: AnalyzerContext, parameters: dict) -> list[Detection]:
        settings = get_settings()
        sample_rate = int(parameters.get("sample_rate", settings.pcm_sample_rate))
        pcm_path = ensure_mono_pcm(context.source_path, context.cache_dir, sample_rate)
        samples = load_pcm(pcm_path)
        tones = detect_tones(
            samples,
            sample_rate,
            target_hz=float(parameters.get("frequency_hz", settings.slate_frequency_hz)),
            frequency_tolerance_hz=float(
                parameters.get("frequency_tolerance_hz", settings.slate_frequency_tolerance_hz)
            ),
            expected_duration_seconds=float(
                parameters.get(
                    "expected_duration_seconds", settings.slate_expected_duration_seconds
                )
            ),
            duration_tolerance_seconds=float(
                parameters.get(
                    "duration_tolerance_seconds", settings.slate_duration_tolerance_seconds
                )
            ),
            min_tone_to_guard_db=float(
                parameters.get("min_tone_to_guard_db", settings.slate_min_tone_to_guard_db)
            ),
        )

        detections: list[Detection] = []
        for tone in tones:
            confidence = max(0.0, min(1.0, (tone.median_tone_to_guard_db - 15.0) / 35.0))
            detections.append(
                Detection(
                    start_seconds=tone.start_seconds,
                    end_seconds=tone.end_seconds,
                    category="slate-marker",
                    label="1 kHz marker",
                    confidence=confidence,
                    frequency_low_hz=settings.slate_frequency_hz
                    - settings.slate_frequency_tolerance_hz,
                    frequency_high_hz=settings.slate_frequency_hz
                    + settings.slate_frequency_tolerance_hz,
                    attributes={"median_tone_to_guard_db": tone.median_tone_to_guard_db},
                )
            )

        max_pair_gap = float(
            parameters.get("max_pair_gap_seconds", settings.slate_max_pair_gap_seconds)
        )
        for first, second in pair_tones(tones, max_pair_gap):
            detections.append(
                Detection(
                    start_seconds=first.end_seconds,
                    end_seconds=second.start_seconds,
                    category="slate-region",
                    label="Bracketed recording slate",
                    attributes={
                        "opening_marker_start": first.start_seconds,
                        "closing_marker_end": second.end_seconds,
                    },
                )
            )
        return sorted(detections, key=lambda item: (item.start_seconds, item.end_seconds))
