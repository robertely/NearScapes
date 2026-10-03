from nearscapes.audio.probe import location_from_tags


def test_extracts_iso6709_location():
    result = location_from_tags(
        {"com.apple.quicktime.location.ISO6709": "+39.8398-105.1054+1690.0/"}
    )
    assert result == {
        "latitude": 39.8398,
        "longitude": -105.1054,
        "elevation_m": 1690.0,
        "source": "embedded:com.apple.quicktime.location.iso6709",
    }


def test_no_location_returns_none():
    assert location_from_tags({"title": "260925_0098.mp3", "album": "TASCAM DR-05"}) is None
