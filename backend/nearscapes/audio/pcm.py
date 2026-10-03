import subprocess
from pathlib import Path

import numpy as np


def ensure_mono_pcm(source: Path, cache_dir: Path, sample_rate: int) -> Path:
    output = cache_dir / f"pcm-{sample_rate}-mono-f32le.raw"
    if output.exists() and output.stat().st_size > 0:
        return output
    temporary = output.with_suffix(".tmp")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        str(temporary),
    ]
    subprocess.run(command, check=True)
    temporary.replace(output)
    return output


def load_pcm(path: Path) -> np.memmap:
    return np.memmap(path, dtype="<f4", mode="r")
