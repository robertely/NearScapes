from types import SimpleNamespace

from nearscapes.analyzers.slate_transcript import parse_announced_time
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
