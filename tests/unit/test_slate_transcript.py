from types import SimpleNamespace

from nearscapes.analyzers.slate_transcript import (
    parse_announced_time,
    parse_opening_location,
)
from nearscapes.pipeline import build_slate_transcription_windows


def _event(start: float, end: float, category: str):
    return SimpleNamespace(
        start_seconds=start,
        end_seconds=end,
        category=category,
    )


def test_bracketed_region_becomes_transcription_window():
    events = [
        _event(1.0, 3.0, "slate-marker"),
        _event(3.0, 8.0, "slate-region"),
        _event(8.0, 10.0, "slate-marker"),
    ]

    windows = build_slate_transcription_windows(
        events,
        source_duration_seconds=60.0,
        post_seconds=30.0,
    )

    assert windows == [
        {
            "start_seconds": 3.0,
            "end_seconds": 8.0,
            "boundary": "opening-slate",
        }
    ]


def test_unpaired_beep_uses_post_window():
    events = [_event(1.0, 3.0, "slate-marker")]

    windows = build_slate_transcription_windows(
        events,
        source_duration_seconds=60.0,
        post_seconds=30.0,
    )

    assert windows == [
        {
            "start_seconds": 3.0,
            "end_seconds": 33.0,
            "boundary": "note-slate-candidate",
        }
    ]


def test_note_window_is_not_truncated_by_later_tone_candidate():
    events = [
        _event(1.0, 3.0, "slate-marker"),
        _event(12.0, 14.0, "slate-marker"),
    ]

    windows = build_slate_transcription_windows(
        events,
        source_duration_seconds=60.0,
        post_seconds=30.0,
    )

    assert windows[0] == {
        "start_seconds": 3.0,
        "end_seconds": 33.0,
        "boundary": "note-slate-candidate",
    }
    assert windows[1] == {
        "start_seconds": 14.0,
        "end_seconds": 44.0,
        "boundary": "note-slate-candidate",
    }



def test_parses_announced_clock_times():
    assert parse_announced_time("11:20 AM") == "11:20 AM"
    assert parse_announced_time("11 20 a.m. walking by the tracks") == "11:20 AM"
    assert parse_announced_time("The time is 7:05 PM. Train passing.") == "7:05 PM"


def test_rejects_non_time_speech_as_note_slate():
    assert parse_announced_time("train crossing, lots of birds") is None
    assert parse_announced_time("I think it is around eleven") is None



def test_parses_opening_slate_location():
    assert parse_opening_location(
        "This is Robert Ely recording at 39.81234, negative 105.12345. "
        "The current time is 11:20 AM."
    ) == {
        "latitude": 39.81234,
        "longitude": -105.12345,
        "source": "opening-slate",
    }


def test_parses_labelled_opening_slate_location():
    assert parse_opening_location(
        "Latitude 39.81234, longitude minus 105.12345."
    ) == {
        "latitude": 39.81234,
        "longitude": -105.12345,
        "source": "opening-slate",
    }


def test_rejects_invalid_opening_slate_coordinates():
    assert parse_opening_location(
        "This is a recording at 139.81234, -205.12345."
    ) is None



def test_parses_opening_slate_coordinates_with_whitespace_separator():
    assert parse_opening_location(
        "This is Robert Ely recording at 39.8123 -105.1234. "
        "The current time is 11:20 AM."
    ) == {
        "latitude": 39.8123,
        "longitude": -105.1234,
        "source": "opening-slate",
    }


def test_parses_opening_slate_coordinates_with_degrees_and_unicode_minus():
    assert parse_opening_location(
        "This is Robert Ely recording at 39.8123 degrees, −105.1234 degrees."
    ) == {
        "latitude": 39.8123,
        "longitude": -105.1234,
        "source": "opening-slate",
    }


def test_parses_opening_slate_labelled_coordinates_with_of():
    assert parse_opening_location(
        "Latitude of 39.8123 and longitude of minus 105.1234."
    ) == {
        "latitude": 39.8123,
        "longitude": -105.1234,
        "source": "opening-slate",
    }

def test_parses_whisper_split_decimal_in_labelled_coordinates():
    assert parse_opening_location(
        "This is Robert Ely recording at latitude 39.7 989, "
        "longitude minus 105.0894 at altitude 5227.91913074271. "
        "The current time is 9.10am on October 4th, 2026."
    ) == {
        "latitude": 39.7989,
        "longitude": -105.0894,
        "source": "opening-slate",
    }

