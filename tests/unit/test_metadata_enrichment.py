from types import SimpleNamespace

from nearscapes.jobs.work import (
    metadata_with_detected_location,
    metadata_with_slate_detections,
)


def _detection(location):
    return SimpleNamespace(attributes={"location": location})


def test_opening_slate_location_fills_missing_metadata_location():
    metadata = {"tags": {"artist": "field recorder"}, "location": None}
    location = {
        "latitude": 39.81234,
        "longitude": -105.12345,
        "source": "opening-slate",
    }

    updated = metadata_with_detected_location(metadata, [_detection(location)])

    assert updated["location"] == location
    assert updated["tags"] == metadata["tags"]


def test_opening_slate_location_replaces_embedded_metadata_location():
    embedded = {
        "latitude": 40.0,
        "longitude": -105.0,
        "source": "embedded",
    }
    detected = {
        "latitude": 39.81234,
        "longitude": -105.12345,
        "source": "opening-slate",
    }

    updated = metadata_with_detected_location(
        {"location": embedded},
        [_detection(detected)],
    )

    assert updated["location"] == detected
    assert updated["embedded_location"] == embedded



def test_recording_metadata_is_persisted_from_post_slate_transcript():
    detection = SimpleNamespace(
        category="recording-metadata-transcript",
        text="Microphone Rode VideoMic GO II.",
        attributes={
            "location": None,
            "recording_metadata": {"microphone": "Rode VideoMic GO II"},
        },
    )

    updated = metadata_with_slate_detections(
        {"location": None, "recording_metadata": {}},
        [detection],
    )

    assert updated["recording_metadata"] == {
        "microphone": "Rode VideoMic GO II",
    }


def test_recording_metadata_is_still_updated_when_slate_location_already_exists():
    location = {
        "latitude": 39.81234,
        "longitude": -105.12345,
        "source": "opening-slate",
    }
    detection = SimpleNamespace(
        category="recording-metadata-transcript",
        text="Microphone Rode VideoMic GO II.",
        attributes={
            "location": None,
            "recording_metadata": {"microphone": "Rode VideoMic GO II"},
        },
    )

    updated = metadata_with_slate_detections(
        {"location": location},
        [detection],
    )

    assert updated["location"] == location
    assert updated["recording_metadata"]["microphone"] == "Rode VideoMic GO II"
