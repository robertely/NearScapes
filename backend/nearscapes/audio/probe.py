from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_ISO6709 = re.compile(
    r"^(?P<lat>[+-]\d+(?:\.\d+)?)(?P<lon>[+-]\d+(?:\.\d+)?)"
    r"(?P<alt>[+-]\d+(?:\.\d+)?)?/?$"
)


def _as_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return None


def location_from_tags(tags: dict[str, str] | None) -> dict | None:
    """Extract a location from common audio/video metadata tag conventions.

    This intentionally only accepts formats we can parse deterministically. Spoken slate
    metadata is handled by the slate/ASR pipeline rather than guessed here.
    """

    if not tags:
        return None
    normalized = {str(key).lower(): str(value) for key, value in tags.items()}

    for key in (
        "com.apple.quicktime.location.iso6709",
        "location",
        "location-eng",
        "gpscoordinates",
        "gps_position",
    ):
        value = normalized.get(key)
        if not value:
            continue
        match = _ISO6709.match(value.strip())
        if match:
            result = {
                "latitude": float(match.group("lat")),
                "longitude": float(match.group("lon")),
                "source": f"embedded:{key}",
            }
            if match.group("alt") is not None:
                result["elevation_m"] = float(match.group("alt"))
            return result

    latitude = _as_float(normalized.get("gpslatitude") or normalized.get("latitude"))
    longitude = _as_float(normalized.get("gpslongitude") or normalized.get("longitude"))
    if latitude is None or longitude is None:
        return None

    if normalized.get("gpslatituderef", "").strip().upper() == "S":
        latitude = -abs(latitude)
    if normalized.get("gpslongituderef", "").strip().upper() == "W":
        longitude = -abs(longitude)

    result = {
        "latitude": latitude,
        "longitude": longitude,
        "source": "embedded:gps-fields",
    }
    altitude = _as_float(normalized.get("gpsaltitude") or normalized.get("altitude"))
    if altitude is not None:
        result["elevation_m"] = altitude
    return result


def ffprobe(path: Path) -> dict:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name,sample_rate,channels,duration:format=duration:format_tags",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    if not streams:
        raise ValueError("No audio stream found")
    stream = streams[0]
    format_data = payload.get("format") or {}
    duration = stream.get("duration") or format_data.get("duration")
    tags = format_data.get("tags") or {}
    return {
        "duration_seconds": float(duration) if duration is not None else None,
        "sample_rate": int(stream["sample_rate"]) if stream.get("sample_rate") else None,
        "channels": int(stream["channels"]) if stream.get("channels") else None,
        "codec": stream.get("codec_name"),
        "tags": tags,
        "location": location_from_tags(tags),
    }
