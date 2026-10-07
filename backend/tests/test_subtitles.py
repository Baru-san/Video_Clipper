from __future__ import annotations

import pytest

from app.media import translate as translate_module
from app.media import stt as stt_module
from app.media.subtitles import (
    Segment,
    build_srt,
    parse_verbose_json,
    wrap_text,
)


def test_build_srt_formats_timestamps():
    srt = build_srt([Segment(0.0, 1.5, "Hello world"), Segment(2.0, 3.25, "Bye")])
    assert "1\n00:00:00,000 --> 00:00:01,500\nHello world" in srt
    assert "2\n00:00:02,000 --> 00:00:03,250\nBye" in srt


def test_wrap_text_keeps_all_words_within_width():
    text = " ".join(f"word{i}" for i in range(40))
    lines = wrap_text(text)
    assert all(len(line) <= 42 for line in lines)
    assert " ".join(lines).split() == text.split()


def test_build_srt_splits_long_segment_into_multiple_cues():
    text = " ".join(f"word{i}" for i in range(30))
    srt = build_srt([Segment(0.0, 10.0, text)])
    blocks = [b for b in srt.strip().split("\n\n") if b]
    assert len(blocks) > 1
    assert "word0" in srt and "word29" in srt
    assert blocks[0].startswith("1\n")
    assert blocks[1].startswith("2\n")


def test_parse_verbose_json_drops_empty_segments():
    data = {
        "language": "id",
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "  Halo  "},
            {"start": 1.0, "end": 2.0, "text": "   "},
        ],
    }
    transcript = parse_verbose_json(data)
    assert transcript.language == "id"
    assert len(transcript.segments) == 1
    assert transcript.segments[0].text == "Halo"


async def test_translate_segments_preserves_timestamps(monkeypatch):
    monkeypatch.setattr(translate_module, "is_configured", lambda: True)

    async def fake_request(texts, target, source):
        return [f"{target}:{t}" for t in texts]

    monkeypatch.setattr(translate_module, "_request", fake_request)
    segments = [Segment(0.0, 1.0, "hello"), Segment(1.0, 2.0, "world")]
    result = await translate_module.translate_segments(segments, "id", "en")
    assert [s.text for s in result] == ["id:hello", "id:world"]
    assert [(s.start, s.end) for s in result] == [(0.0, 1.0), (1.0, 2.0)]


async def test_transcribe_requires_config(monkeypatch, tmp_path):
    monkeypatch.setattr(stt_module, "is_configured", lambda: False)
    audio = tmp_path / "a.flac"
    audio.write_bytes(b"x")
    with pytest.raises(stt_module.STTError):
        await stt_module.transcribe(str(audio))
