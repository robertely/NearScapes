from copy import deepcopy

from nearscapes.analyzers.birdnet import BirdNetAnalyzer
from nearscapes.analyzers.slate import SlateToneAnalyzer

_ANALYZERS = {
    SlateToneAnalyzer.id: SlateToneAnalyzer(),
    BirdNetAnalyzer.id: BirdNetAnalyzer(),
}

_AUTORUN_PARAMETERS = {
    "slate-tone": {},
    "birdnet": {
        "backend": "onnx",
        "precision": "fp16",
        "confidence": 0.25,
        "n_workers": 1,
    },
}


def list_analyzers() -> list[dict]:
    return [
        {
            "id": item.id,
            "version": item.version,
            "display_name": item.display_name,
            "auto_run": True,
            "default_parameters": deepcopy(_AUTORUN_PARAMETERS.get(item.id, {})),
        }
        for item in _ANALYZERS.values()
    ]


def autorun_specs() -> list[dict]:
    """All registered analyzers run automatically after upload by default."""
    return [
        {
            "analyzer": item.id,
            "parameters": deepcopy(_AUTORUN_PARAMETERS.get(item.id, {})),
        }
        for item in _ANALYZERS.values()
    ]


def get_analyzer(analyzer_id: str):
    try:
        return _ANALYZERS[analyzer_id]
    except KeyError as exc:
        raise KeyError(f"Unknown analyzer: {analyzer_id}") from exc
