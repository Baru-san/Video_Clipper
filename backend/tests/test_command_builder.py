from __future__ import annotations

import pytest

from app.media import command_builder as cb


def test_probe_argv_uses_ffprobe_json():
    argv = cb.probe_argv("/data/in.mp4")
    assert argv[0].endswith("ffprobe")
    assert "json" in argv
    assert argv[-1] == "/data/in.mp4"


def test_copy_cut_argv_is_lossless():
    argv = cb.copy_cut_argv("/data/in.mp4", "/data/out.mp4", 10.0, 5.5)
    assert "-ss" in argv and argv[argv.index("-ss") + 1] == "10.000"
    assert "-t" in argv and argv[argv.index("-t") + 1] == "5.500"
    assert argv[argv.index("-c") + 1] == "copy"
    assert argv[-1] == "/data/out.mp4"
    assert "-progress" in argv


def test_reencode_argv_single_threaded_and_scaled():
    argv = cb.reencode_cut_argv("/i.mp4", "/o.mp4", 0, 1, scale_height=720)
    assert argv[argv.index("-threads") + 1] == "1"
    assert argv[argv.index("-preset") + 1] == "veryfast"
    assert "scale=-2:720" in argv


def test_thumbnail_argv_rejects_nonpositive_fps():
    with pytest.raises(ValueError):
        cb.thumbnail_argv("/i.mp4", "/t_%03d.jpg", 0)


def test_negative_timestamp_rejected():
    with pytest.raises(ValueError):
        cb.copy_cut_argv("/i.mp4", "/o.mp4", -1, 1)


def test_build_concat_list_escapes_quotes():
    content = cb.build_concat_list(["/a/b.mp4", "/c/it's.mp4"])
    assert "file '/a/b.mp4'" in content
    assert content.endswith("\n")
    assert "it'\\''s" in content


def test_extract_audio_argv_is_16k_mono_flac():
    argv = cb.extract_audio_argv("/in.mp4", "/out.flac")
    assert "-vn" in argv
    assert argv[argv.index("-ac") + 1] == "1"
    assert argv[argv.index("-ar") + 1] == "16000"
    assert argv[argv.index("-c:a") + 1] == "flac"
    assert argv[-1] == "/out.flac"


def test_reencode_vertical_crops_to_9x16():
    argv = cb.reencode_cut_argv("/i.mp4", "/o.mp4", 0, 5, vertical=True)
    vf = argv[argv.index("-vf") + 1]
    assert "crop=" in vf and "scale=1080:1920" in vf


def test_slice_audio_argv_stream_copies():
    argv = cb.slice_audio_argv("/a.flac", "/chunk.flac", 600, 600)
    assert argv[argv.index("-ss") + 1] == "600.000"
    assert argv[argv.index("-t") + 1] == "600.000"
    assert argv[argv.index("-c") + 1] == "copy"
    assert argv[-1] == "/chunk.flac"
