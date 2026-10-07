from __future__ import annotations

from app.media.ffmpeg import _parse_out_time
from app.media.probe import _parse_fraction, nearest_keyframe, parse_keyframes


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


def test_parse_keyframes_ignores_trailing_columns():
    raw = "0.000000,\n2.000000,\n4.000000,H.26[45] SEI\n"
    assert parse_keyframes(raw) == [0.0, 2.0, 4.0]


def test_parse_keyframes_skips_garbage():
    assert parse_keyframes("N/A\n\n1.5,\n") == [1.5]
