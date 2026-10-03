import json
from pathlib import Path

import numpy as np


def calculate_waveform(samples: np.ndarray, sample_rate: int, points: int) -> dict:
    if len(samples) == 0:
        return {"sample_rate": sample_rate, "duration_seconds": 0.0, "peaks": []}
    points = max(1, min(points, len(samples)))
    edges = np.linspace(0, len(samples), points + 1, dtype=np.int64)
    peaks = []
    for start, end in zip(edges[:-1], edges[1:], strict=True):
        segment = samples[start:end]
        peaks.append(float(np.max(np.abs(segment))) if len(segment) else 0.0)
    maximum = max(peaks) or 1.0
    return {
        "sample_rate": sample_rate,
        "duration_seconds": len(samples) / sample_rate,
        "peaks": [round(value / maximum, 6) for value in peaks],
    }


def write_waveform(path: Path, waveform: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(waveform, separators=(",", ":")))
    temporary.replace(path)
