import numpy as np

from nearscapes.analyzers.slate import detect_tones, pair_tones


def synth(sample_rate: int, duration: float, tones: list[tuple[float, float, float]]) -> np.ndarray:
    rng = np.random.default_rng(42)
    samples = rng.normal(0, 0.002, round(sample_rate * duration)).astype(np.float32)
    for start, end, frequency in tones:
        first = round(start * sample_rate)
        last = round(end * sample_rate)
        t = np.arange(last - first) / sample_rate
        samples[first:last] += (0.2 * np.sin(2 * np.pi * frequency * t)).astype(np.float32)
    return samples


def test_detects_two_second_1khz_tones():
    sr = 8000
    samples = synth(sr, 20, [(2, 4, 1000), (12, 14, 1000)])
    tones = detect_tones(samples, sr)
    assert len(tones) == 2
    assert abs(tones[0].start_seconds - 2.0) < 0.1
    assert abs(tones[0].end_seconds - 4.0) < 0.1
    assert len(pair_tones(tones, 60)) == 1


def test_rejects_wrong_frequency_and_duration():
    sr = 8000
    samples = synth(sr, 15, [(1, 3, 800), (5, 5.5, 1000), (8, 12, 1000)])
    tones = detect_tones(samples, sr)
    assert tones == []


def test_rejects_weak_1khz_component_inside_louder_audio():
    sr = 8000
    duration = 8
    samples = np.zeros(round(sr * duration), dtype=np.float32)
    first = round(2 * sr)
    last = round(4 * sr)
    t = np.arange(last - first) / sr
    samples[first:last] = (
        0.04 * np.sin(2 * np.pi * 1000 * t)
        + 0.30 * np.sin(2 * np.pi * 1500 * t)
    ).astype(np.float32)

    assert detect_tones(samples, sr) == []
