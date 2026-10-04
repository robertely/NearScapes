from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from nearscapes.analyzers.base import AnalyzerContext, Detection


def _seconds(value) -> float:
    if isinstance(value, timedelta):
        return value.total_seconds()
    if hasattr(value, "total_seconds"):
        return float(value.total_seconds())
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value)
    parts = text.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return float(text)


def _birdnet_input_path(context: AnalyzerContext) -> Path:
    source = Path(context.source_path)
    if source.suffix:
        return source

    suffix = Path(context.source_filename or "").suffix.lower()
    if not suffix:
        return source

    alias = context.cache_dir / f"birdnet-input{suffix}"
    if alias.exists() or alias.is_symlink():
        alias.unlink()
    try:
        os.link(source, alias)
    except OSError:
        alias.symlink_to(source)
    return alias


def _split_species_name(value: str) -> tuple[str | None, str]:
    if "_" not in value:
        return None, value
    scientific, common = value.split("_", 1)
    return scientific or None, common or value


class BirdNetAnalyzer:
    id = "birdnet"
    version = "0.1.0"
    display_name = "BirdNET+ V3 wildlife (CPU)"

    def analyze(self, context: AnalyzerContext, parameters: dict) -> list[Detection]:
        try:
            import birdnet
        except ImportError as exc:
            raise RuntimeError(
                "BirdNET is not installed. Install NearScapes with the wildlife extra."
            ) from exc

        threshold = float(parameters.get("confidence", 0.25))
        backend = str(parameters.get("backend", "onnx"))
        precision = str(parameters.get("precision", "fp16"))
        n_workers = int(parameters.get("n_workers", 1))
        locale = str(parameters.get("locale", "en_us"))

        if backend not in {"onnx", "pt"}:
            raise ValueError("BirdNET wildlife analyzer supports only the onnx or pt backends")

        load_kwargs = {"lang": locale}
        if backend == "onnx":
            load_kwargs["precision"] = precision

        model = birdnet.load("acoustic", "3.0", backend, **load_kwargs)
        input_path = _birdnet_input_path(context)
        predictions = model.predict(
            input_path,
            device="CPU",
            n_workers=n_workers,
        )

        detections: list[Detection] = []
        for row in predictions.to_structured_array():
            confidence = float(row["confidence"])
            if confidence < threshold:
                continue

            raw_name = str(row["species_name"])
            scientific_name, common_name = _split_species_name(raw_name)
            detections.append(
                Detection(
                    start_seconds=_seconds(row["start_time"]),
                    end_seconds=_seconds(row["end_time"]),
                    category="wildlife",
                    label=common_name,
                    confidence=confidence,
                    attributes={
                        "scientific_name": scientific_name,
                        "birdnet_species_name": raw_name,
                        "model": "BirdNET+ V3.0",
                        "backend": backend,
                        "device": "CPU",
                        "input_path": str(input_path),
                    },
                )
            )

        return sorted(
            detections,
            key=lambda item: (
                item.start_seconds,
                item.end_seconds,
                -(item.confidence or 0.0),
            ),
        )
