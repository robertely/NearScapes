import sys
from types import SimpleNamespace

import pandas as pd

from nearscapes.analyzers.base import AnalyzerContext
from nearscapes.analyzers.birdnet import BirdNetAnalyzer


class FakePredictions(pd.DataFrame):
    @property
    def _constructor(self):
        return FakePredictions


class FakeModel:
    def predict(self, path, device, n_workers):
        assert device == "CPU"
        assert n_workers == 1
        return FakePredictions(
            [
                {
                    "file_path": str(path),
                    "start_time": "00:00:03.00",
                    "end_time": "00:00:06.00",
                    "species_name": "Poecile atricapillus_Black-capped Chickadee",
                    "confidence": 0.91,
                },
                {
                    "file_path": str(path),
                    "start_time": "00:00:09.00",
                    "end_time": "00:00:12.00",
                    "species_name": "Low confidence thing",
                    "confidence": 0.05,
                },
            ]
        )


def test_birdnet_normalizes_cpu_predictions(monkeypatch, tmp_path):
    fake_birdnet = SimpleNamespace(
        load=lambda *args, **kwargs: FakeModel(),
    )
    monkeypatch.setitem(sys.modules, "birdnet", fake_birdnet)

    source = tmp_path / "recording.wav"
    source.write_bytes(b"fixture")
    context = AnalyzerContext(
        source_path=source,
        cache_dir=tmp_path / "cache",
        source_sha256="abc",
    )

    events = BirdNetAnalyzer().analyze(
        context,
        {"confidence": 0.25, "backend": "onnx", "n_workers": 1},
    )

    assert len(events) == 1
    event = events[0]
    assert event.start_seconds == 3.0
    assert event.end_seconds == 6.0
    assert event.category == "wildlife"
    assert event.label == "Black-capped Chickadee"
    assert event.confidence == 0.91
    assert event.attributes["scientific_name"] == "Poecile atricapillus"
    assert event.attributes["device"] == "CPU"
