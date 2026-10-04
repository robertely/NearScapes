import sys
from types import SimpleNamespace

import numpy as np
import pytest
from nearscapes.analyzers.base import AnalyzerContext
from nearscapes.analyzers.birdnet import BirdNetAnalyzer


class FakePredictions:
    def to_structured_array(self):
        return np.array(
            [
                (
                    3.0,
                    6.0,
                    "Poecile atricapillus_Black-capped Chickadee",
                    0.91,
                ),
                (
                    9.0,
                    12.0,
                    "Low confidence thing",
                    0.05,
                ),
            ],
            dtype=[
                ("start_time", "f4"),
                ("end_time", "f4"),
                ("species_name", object),
                ("confidence", "f4"),
            ],
        )


class FakeModel:
    def predict(self, path, device, n_workers, **kwargs):
        assert path.name == "recording.wav"
        assert device == "CPU"
        assert n_workers == 1
        return FakePredictions()


def test_birdnet_normalizes_cpu_predictions(monkeypatch, tmp_path):
    fake_birdnet = SimpleNamespace(load=lambda *args, **kwargs: FakeModel())
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
    assert event.confidence == pytest.approx(0.91)
    assert event.attributes["scientific_name"] == "Poecile atricapillus"
    assert event.attributes["device"] == "CPU"


def test_birdnet_uses_original_extension_for_extensionless_storage(monkeypatch, tmp_path):
    class ExtensionCheckingModel:
        def predict(self, path, device, n_workers, **kwargs):
            assert path.suffix == ".mp3"
            assert path.exists()
            return FakePredictions()

    fake_birdnet = SimpleNamespace(load=lambda *args, **kwargs: ExtensionCheckingModel())
    monkeypatch.setitem(sys.modules, "birdnet", fake_birdnet)

    source = tmp_path / "source"
    source.write_bytes(b"fixture")
    cache = tmp_path / "cache"
    cache.mkdir()
    context = AnalyzerContext(
        source_path=source,
        cache_dir=cache,
        source_sha256="abc",
        source_filename="Two Ponds Walk.mp3",
    )

    events = BirdNetAnalyzer().analyze(
        context,
        {"confidence": 0.25, "backend": "onnx", "n_workers": 1},
    )

    assert len(events) == 1
