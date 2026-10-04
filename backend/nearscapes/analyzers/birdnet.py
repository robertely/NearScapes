from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import httpx

from nearscapes.analyzers.base import AnalyzerContext, Detection
from nearscapes.audio.pcm import ensure_mono_pcm
from nearscapes.config import get_settings


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
    version = "0.2.0"
    display_name = "BirdNET+ V3 wildlife"

    def analyze(self, context: AnalyzerContext, parameters: dict) -> list[Detection]:
        settings = get_settings()
        accelerator = settings.accelerator.lower()
        if accelerator in {"metal", "mps"}:
            return self._analyze_metal(context, parameters)
        if accelerator != "cpu":
            raise RuntimeError(
                f"Unsupported BirdNET accelerator '{settings.accelerator}'. "
                "Use cpu or metal."
            )
        return self._analyze_cpu(context, parameters)

    def _analyze_metal(
        self,
        context: AnalyzerContext,
        parameters: dict,
    ) -> list[Detection]:
        settings = get_settings()
        threshold = float(
            parameters.get("confidence", settings.birdnet_confidence_default)
        )
        locale = str(parameters.get("locale", "en_us"))
        batch_size = int(parameters.get("batch_size", 16))
        top_k = int(parameters.get("top_k", 5))

        pcm_path = ensure_mono_pcm(
            context.source_path,
            context.cache_dir,
            sample_rate=32_000,
        )

        try:
            with pcm_path.open("rb") as pcm, httpx.Client(
                timeout=settings.inference_timeout_seconds
            ) as client:
                response = client.post(
                    f"{settings.inference_url.rstrip('/')}/v1/birdnet/predict",
                    data={
                        "sample_rate": "32000",
                        "confidence": str(threshold),
                        "batch_size": str(batch_size),
                        "top_k": str(top_k),
                        "locale": locale,
                    },
                    files={
                        "file": (
                            "audio-f32le.raw",
                            pcm,
                            "application/octet-stream",
                        )
                    },
                )
        except httpx.RequestError as exc:
            raise RuntimeError(
                "BirdNET Metal inference service is unavailable at "
                f"{settings.inference_url}. Run NearScapes with 'just run' on Apple Silicon."
            ) from exc

        if response.status_code >= 400:
            detail = response.text
            try:
                detail = response.json().get("detail", detail)
            except ValueError:
                pass
            raise RuntimeError(f"BirdNET Metal inference failed: {detail}")

        payload = response.json()
        if payload.get("device") != "MPS":
            raise RuntimeError(
                f"Metal service returned unexpected device {payload.get('device')!r}; "
                "refusing silent CPU fallback."
            )

        detections: list[Detection] = []
        for item in payload.get("events", []):
            attributes = dict(item.get("attributes") or {})
            attributes["remote_inference"] = settings.inference_url
            detections.append(
                Detection(
                    start_seconds=float(item["start_seconds"]),
                    end_seconds=float(item["end_seconds"]),
                    category=str(item.get("category", "wildlife")),
                    label=str(item["label"]),
                    confidence=(
                        float(item["confidence"])
                        if item.get("confidence") is not None
                        else None
                    ),
                    attributes=attributes,
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

    def _analyze_cpu(
        self,
        context: AnalyzerContext,
        parameters: dict,
    ) -> list[Detection]:
        settings = get_settings()
        try:
            import birdnet
        except ImportError as exc:
            raise RuntimeError(
                "BirdNET is not installed. Install NearScapes with the wildlife extra."
            ) from exc

        threshold = float(
            parameters.get("confidence", settings.birdnet_confidence_default)
        )
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
                        "accelerator": "cpu",
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
