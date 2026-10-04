from copy import deepcopy

from nearscapes.analyzers.birdnet import BirdNetAnalyzer
from nearscapes.analyzers.slate import SlateToneAnalyzer
from nearscapes.analyzers.slate_transcript import SlateTranscriptAnalyzer
from nearscapes.config import get_settings

_ANALYZERS = {
    SlateToneAnalyzer.id: SlateToneAnalyzer(),
    SlateTranscriptAnalyzer.id: SlateTranscriptAnalyzer(),
    BirdNetAnalyzer.id: BirdNetAnalyzer(),
}

_AUTORUN_PARAMETERS = {
    "slate-tone": {},
    "birdnet": {
        "backend": "onnx",
        "precision": "fp16",
        "confidence": 0.60,
        "n_workers": 1,
    },
}


def _default_parameters(analyzer_id: str) -> dict:
    parameters = deepcopy(_AUTORUN_PARAMETERS.get(analyzer_id, {}))
    if analyzer_id == "birdnet":
        parameters["confidence"] = get_settings().birdnet_confidence_default
    return parameters


def list_analyzers() -> list[dict]:
    return [
        {
            "id": item.id,
            "version": item.version,
            "display_name": item.display_name,
            "auto_run": item.id in _AUTORUN_PARAMETERS,
            "default_parameters": _default_parameters(item.id),
        }
        for item in _ANALYZERS.values()
    ]


def autorun_specs() -> list[dict]:
    """Return analyzers that run directly after upload."""
    return [
        {
            "analyzer": analyzer_id,
            "parameters": _default_parameters(analyzer_id),
        }
        for analyzer_id, parameters in _AUTORUN_PARAMETERS.items()
    ]


def get_analyzer(analyzer_id: str):
    try:
        return _ANALYZERS[analyzer_id]
    except KeyError as exc:
        raise KeyError(f"Unknown analyzer: {analyzer_id}") from exc
