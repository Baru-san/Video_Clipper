from __future__ import annotations

from app.media.analyze import sanitize
from app.media.stt import merge_transcripts
from app.media.subtitles import Segment, Transcript


def test_sanitize_drops_invalid_and_clamps_length():
    raw = [
        {"start": -1, "end": 5, "title": "neg"},
        {"start": 10, "end": 5, "title": "reversed"},
        {"start": 200, "end": 210, "title": "past-end"},
        {"start": 10, "end": 12, "title": "short", "score": 0.9},
        {"start": 40, "end": 200, "title": "long"},
    ]
    result = sanitize(raw, duration=120, min_length=15, max_length=60, max_clips=10)
    titles = [c.title for c in result]
    assert "neg" not in titles and "reversed" not in titles and "past-end" not in titles
    short = next(c for c in result if c.title == "short")
    assert short.end - short.start == 15  # expanded to min length
    long = next(c for c in result if c.title == "long")
    assert long.end - long.start == 60  # truncated to max length
    assert long.end <= 120


def test_sanitize_removes_overlaps_and_caps_count():
    raw = [
        {"start": 0, "end": 30, "title": "a"},
        {"start": 10, "end": 40, "title": "overlap"},
        {"start": 40, "end": 70, "title": "b"},
        {"start": 80, "end": 110, "title": "c"},
    ]
    result = sanitize(raw, duration=120, min_length=10, max_length=60, max_clips=2)
    assert len(result) == 2
    assert [c.title for c in result] == ["a", "b"]


def test_merge_transcripts_offsets_and_sorts():
    parts = [
        (0.0, Transcript("en", [Segment(0, 1, "one")])),
        (600.0, Transcript("", [Segment(0, 2, "two")])),
    ]
    merged = merge_transcripts(parts)
    assert merged.language == "en"
    assert [(s.start, s.text) for s in merged.segments] == [(0.0, "one"), (600.0, "two")]
