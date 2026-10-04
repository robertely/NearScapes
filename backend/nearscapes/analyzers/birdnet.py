from __future__ import annotations

import os
from datetime import timedelta
from functools import lru_cache
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


@lru_cache(maxsize=16)
def _geo_model(locale: str, precision: str):
    import birdnet

    return birdnet.load(
        "geo",
        "3.0",
        "onnx",
        lang=locale,
        precision=precision,
    )


def _geo_support(
    parameters: dict,
    *,
    locale: str,
    precision: str,
) -> dict[str, float] | None:
    latitude = parameters.get("latitude")
    longitude = parameters.get("longitude")
    if latitude is None or longitude is None:
        return None

    settings = get_settings()
    threshold = float(
        parameters.get(
            "geo_confidence",
            settings.birdnet_geo_confidence_default,
        )
    )
    model = _geo_model(locale, precision)
    result = model.predict(
        float(latitude),
        float(longitude),
        min_confidence=threshold,
        device="CPU",
    )

    support: dict[str, float] = {}
    for row in result.to_structured_array(sort_by=None):
        scientific_name, _ = _split_species_name(str(row["species_name"]))
        if scientific_name:
            support[scientific_name] = float(row["confidence"])
    return support


class BirdNetAnalyzer:
    id = "birdnet"
    version = "0.4.0"
    display_name = "BirdNET+ V3 wildlife"

    def analyze(self, context: AnalyzerContext, parameters: dict) -> list[Detection]:
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
                        **(
                            {
                                "latitude": str(parameters["latitude"]),
                                "longitude": str(parameters["longitude"]),
                                "geo_confidence": str(
                                    parameters.get(
                                        "geo_confidence",
                                        settings.birdnet_geo_confidence_default,
                                    )
                                ),
                            }
                            if parameters.get("latitude") is not None
                            and parameters.get("longitude") is not None
                            else {}
                        ),
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
            attributes["location_source"] = parameters.get("location_source")
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
        geo_support = _geo_support(
            parameters,
            locale=locale,
            precision=precision,
        )
        custom_species_list = None
        if geo_support is not None:
            custom_species_list = [
                str(species_name)
                for species_name in model.species_list
                if _split_species_name(str(species_name))[0] in geo_support
            ]
            if not custom_species_list:
                raise RuntimeError(
                    "BirdNET GeoModel returned no species that match the acoustic model"
                )

        input_path = _birdnet_input_path(context)
        predictions = model.predict(
            input_path,
            device="CPU",
            n_workers=n_workers,
            default_confidence_threshold=threshold,
            custom_species_list=custom_species_list,
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
                        "geo_filter_applied": geo_support is not None,
                        "geo_confidence": (
                            geo_support.get(scientific_name)
                            if geo_support is not None and scientific_name
                            else None
                        ),
                        "latitude": parameters.get("latitude"),
                        "longitude": parameters.get("longitude"),
                        "location_source": parameters.get("location_source"),
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
