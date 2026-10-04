from __future__ import annotations

import math
import threading
from functools import lru_cache
from typing import Annotated

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

app = FastAPI(title="NearScapes Metal Inference", version="0.1.0")

_MODEL_LOCK = threading.Lock()
_SAMPLE_RATE = 32_000
_SEGMENT_SECONDS = 3.0
_SEGMENT_SAMPLES = int(_SAMPLE_RATE * _SEGMENT_SECONDS)


def _mps_status() -> dict:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("PyTorch is not installed in the Metal helper environment") from exc

    built = bool(torch.backends.mps.is_built())
    available = bool(torch.backends.mps.is_available())
    if not built or not available:
        raise RuntimeError(
            "Apple Metal Performance Shaders are unavailable. "
            "NearScapes will not silently fall back to CPU for a Metal run."
        )

    try:
        probe = torch.ones(1, device="mps")
        _ = probe.cpu()
    except Exception as exc:
        raise RuntimeError(
            f"MPS is reported available but a test allocation failed: {exc}"
        ) from exc

    return {
        "accelerator": "mps",
        "torch_version": torch.__version__,
        "mps_built": built,
        "mps_available": available,
    }


@app.get("/health")
def health() -> dict:
    try:
        return {"status": "ok", **_mps_status()}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@lru_cache(maxsize=1)
def _model():
    import torch
    from birdnet.acoustic.models.v3_0.pt import AcousticPTDownloaderV3_0
    from birdnet.globals import MODEL_PRECISION_FP32

    _mps_status()
    model_path, _ = AcousticPTDownloaderV3_0.get_model_path_and_labels(
        "en_us", MODEL_PRECISION_FP32
    )
    device = torch.device("mps")
    model = torch.jit.load(str(model_path), map_location=device)
    model.eval()
    return model, device, model_path


@lru_cache(maxsize=32)
def _labels(locale: str) -> tuple[str, ...]:
    from birdnet.acoustic.models.v3_0.pt import AcousticPTDownloaderV3_0
    from birdnet.globals import MODEL_PRECISION_FP32

    _, labels = AcousticPTDownloaderV3_0.get_model_path_and_labels(
        locale, MODEL_PRECISION_FP32
    )
    return tuple(labels)


def _species_parts(value: str) -> tuple[str | None, str]:
    if "_" not in value:
        return None, value
    scientific, common = value.split("_", 1)
    return scientific or None, common or value


def _read_exactish(handle, size: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total < size:
        chunk = handle.read(size - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _run_birdnet(
    handle,
    *,
    threshold: float,
    batch_size: int,
    top_k: int,
    locale: str,
) -> dict:
    import torch

    model, device, model_path = _model()
    labels = _labels(locale)
    bytes_per_batch = _SEGMENT_SAMPLES * batch_size * 4
    segment_index = 0
    events: list[dict] = []

    with _MODEL_LOCK, torch.inference_mode():
        while True:
            raw = _read_exactish(handle, bytes_per_batch)
            if not raw:
                break
            if len(raw) % 4:
                raise ValueError("PCM payload length is not aligned to float32 samples")

            samples = np.frombuffer(raw, dtype="<f4")
            segment_count = math.ceil(samples.size / _SEGMENT_SAMPLES)
            batch = np.zeros((segment_count, _SEGMENT_SAMPLES), dtype=np.float32)
            valid_lengths: list[int] = []

            for offset in range(segment_count):
                start = offset * _SEGMENT_SAMPLES
                end = min(start + _SEGMENT_SAMPLES, samples.size)
                valid = end - start
                batch[offset, :valid] = samples[start:end]
                valid_lengths.append(valid)

            tensor = torch.from_numpy(batch).to(device)
            output = model(tensor)
            if not isinstance(output, (tuple, list)) or not output:
                raise RuntimeError("BirdNET TorchScript model returned an unexpected output")
            predictions = output[0]
            if predictions.ndim == 1:
                predictions = predictions.unsqueeze(0)

            k = min(top_k, int(predictions.shape[1]))
            values, indices = torch.topk(predictions, k=k, dim=1)
            values_np = values.detach().cpu().numpy()
            indices_np = indices.detach().cpu().numpy()

            for row in range(segment_count):
                start_seconds = (segment_index + row) * _SEGMENT_SECONDS
                end_seconds = start_seconds + valid_lengths[row] / _SAMPLE_RATE
                for score, label_index in zip(values_np[row], indices_np[row], strict=True):
                    confidence = float(score)
                    if confidence < threshold:
                        continue
                    raw_name = labels[int(label_index)]
                    scientific_name, common_name = _species_parts(raw_name)
                    events.append(
                        {
                            "start_seconds": start_seconds,
                            "end_seconds": end_seconds,
                            "category": "wildlife",
                            "label": common_name,
                            "confidence": confidence,
                            "attributes": {
                                "scientific_name": scientific_name,
                                "birdnet_species_name": raw_name,
                                "model": "BirdNET+ V3.0",
                                "backend": "pt",
                                "device": "MPS",
                                "accelerator": "metal",
                            },
                        }
                    )

            segment_index += segment_count

    return {
        "model": model_path.name,
        "backend": "pt",
        "device": "MPS",
        "sample_rate": _SAMPLE_RATE,
        "segment_seconds": _SEGMENT_SECONDS,
        "events": events,
    }


@app.post("/v1/birdnet/predict")
def birdnet_predict(
    file: Annotated[UploadFile, File(...)],
    sample_rate: Annotated[int, Form()] = _SAMPLE_RATE,
    confidence: Annotated[float, Form()] = 0.25,
    batch_size: Annotated[int, Form()] = 16,
    top_k: Annotated[int, Form()] = 5,
    locale: Annotated[str, Form()] = "en_us",
) -> dict:
    if sample_rate != _SAMPLE_RATE:
        raise HTTPException(
            status_code=400,
            detail=f"BirdNET Metal service expects {_SAMPLE_RATE} Hz float32 mono PCM",
        )
    if not 0.0 <= confidence <= 1.0:
        raise HTTPException(status_code=400, detail="confidence must be between 0 and 1")
    if not 1 <= batch_size <= 64:
        raise HTTPException(status_code=400, detail="batch_size must be between 1 and 64")
    if not 1 <= top_k <= 20:
        raise HTTPException(status_code=400, detail="top_k must be between 1 and 20")

    try:
        _mps_status()
        file.file.seek(0)
        return _run_birdnet(
            file.file,
            threshold=confidence,
            batch_size=batch_size,
            top_k=top_k,
            locale=locale,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
