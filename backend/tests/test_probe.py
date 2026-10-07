from __future__ import annotations

from app.media.ffmpeg import _parse_out_time
from app.media.probe import _parse_fraction, nearest_keyframe


def test_parse_fraction():
    assert _parse_fraction("30000/1001") == 30000 / 1001
    assert _parse_fraction("25") == 25.0
    assert _parse_fraction("") == 0.0
    assert _parse_fraction("0/0") == 0.0


def test_parse_out_time():
    assert _parse_out_time("00:01:30.500") == 90.5
    assert _parse_out_time("12.5") == 12.5
    assert _parse_out_time("N/A") == 0.0


def test_nearest_keyframe_picks_largest_not_after():
    frames = [0.0, 2.0, 4.0, 6.0]
    assert nearest_keyframe(frames, 5.0) == 4.0
    assert nearest_keyframe(frames, 2.0) == 2.0
    assert nearest_keyframe(frames, 0.5) == 0.0
    assert nearest_keyframe([], 5.0) == 0.0
