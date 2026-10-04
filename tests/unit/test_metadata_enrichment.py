from types import SimpleNamespace

from nearscapes.jobs.work import metadata_with_detected_location


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


def test_existing_metadata_location_wins_over_opening_slate():
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

    assert updated["location"] == embedded
