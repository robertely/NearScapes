from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Detection:
    start_seconds: float
    end_seconds: float
    category: str
    label: str
    confidence: float | None = None
    frequency_low_hz: float | None = None
    frequency_high_hz: float | None = None
    attributes: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AnalyzerContext:
    source_path: Path
    cache_dir: Path
    source_sha256: str


class Analyzer(Protocol):
    id: str
    version: str
    display_name: str

    def analyze(self, context: AnalyzerContext, parameters: dict) -> list[Detection]: ...
