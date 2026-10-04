import numpy as np
from nearscapes.analyzers.slate import ToneRegion, detect_tones, pair_tones


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
    assert len(pair_tones(tones, 25)) == 1


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



def test_pairs_realistic_opening_slate_gap():
    tones = [
        ToneRegion(18.375, 20.375, 40.0),
        ToneRegion(41.025, 43.025, 55.0),
    ]

    pairs = pair_tones(tones, 25, 60)

    assert pairs == [(tones[0], tones[1])]


def test_does_not_pair_later_tones_by_proximity():
    tones = [
        ToneRegion(690.525, 692.525, 41.0),
        ToneRegion(694.825, 696.825, 50.0),
    ]

    assert pair_tones(tones, 25, 60) == []


def test_pairs_at_most_one_opening_slate():
    tones = [
        ToneRegion(18.375, 20.375, 40.0),
        ToneRegion(41.025, 43.025, 55.0),
        ToneRegion(50.0, 52.0, 45.0),
        ToneRegion(54.0, 56.0, 45.0),
    ]

    pairs = pair_tones(tones, 25, 60)

    assert pairs == [(tones[0], tones[1])]
