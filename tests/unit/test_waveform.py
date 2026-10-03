import numpy as np

from nearscapes.audio.waveform import calculate_waveform


def test_waveform_is_normalized_and_bounded():
    samples = np.array([0.0, 0.5, -1.0, 0.25, 0.0, 0.1, -0.2, 0.0], dtype=np.float32)
    result = calculate_waveform(samples, sample_rate=4, points=4)
    assert result["duration_seconds"] == 2.0
    assert len(result["peaks"]) == 4
    assert max(result["peaks"]) == 1.0
