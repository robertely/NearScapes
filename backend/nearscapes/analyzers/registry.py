from nearscapes.analyzers.slate import SlateToneAnalyzer

_ANALYZERS = {SlateToneAnalyzer.id: SlateToneAnalyzer()}


def list_analyzers() -> list[dict]:
    return [
        {"id": item.id, "version": item.version, "display_name": item.display_name}
        for item in _ANALYZERS.values()
    ]


def get_analyzer(analyzer_id: str):
    try:
        return _ANALYZERS[analyzer_id]
    except KeyError as exc:
        raise KeyError(f"Unknown analyzer: {analyzer_id}") from exc
