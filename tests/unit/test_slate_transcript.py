from types import SimpleNamespace

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
            "boundary": "between-beeps",
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
            "boundary": "after-beep",
        }
    ]


def test_post_window_stops_at_next_beep():
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
        "end_seconds": 12.0,
        "boundary": "after-beep",
    }
