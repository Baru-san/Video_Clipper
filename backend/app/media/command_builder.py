from __future__ import annotations

from app.config import settings

DEFAULT_CRF = 20
DEFAULT_PRESET = "veryfast"


def _t(value: float) -> str:
    if value < 0:
        raise ValueError("timestamp must be >= 0")
    return f"{value:.3f}"


def probe_argv(path: str) -> list[str]:
    return [
        settings.ffprobe_path,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
    ]


def keyframe_probe_argv(path: str) -> list[str]:
    return [
        settings.ffprobe_path,
        "-v",
        "quiet",
        "-select_streams",
        "v:0",
        "-skip_frame",
        "nokey",
        "-show_entries",
        "frame=pts_time",
        "-of",
        "csv=p=0",
        path,
    ]


def copy_cut_argv(path: str, output: str, start: float, duration: float) -> list[str]:
    return [
        settings.ffmpeg_path,
        "-hide_banner",
        "-y",
        "-ss",
        _t(start),
        "-i",
        path,
        "-t",
        _t(duration),
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-nostats",
        output,
    ]


def reencode_cut_argv(
    path: str,
    output: str,
    start: float,
    duration: float,
    crf: int = DEFAULT_CRF,
    preset: str = DEFAULT_PRESET,
    scale_height: int | None = None,
) -> list[str]:
    argv = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-y",
        "-ss",
        _t(start),
        "-i",
        path,
        "-t",
        _t(duration),
        "-c:v",
        "libx264",
        "-threads",
        "1",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
    ]
    if scale_height:
        argv += ["-vf", f"scale=-2:{int(scale_height)}"]
    argv += [
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-nostats",
        output,
    ]
    return argv


def thumbnail_argv(path: str, out_pattern: str, fps: float, width: int = 160) -> list[str]:
    if fps <= 0:
        raise ValueError("fps must be > 0")
    return [
        settings.ffmpeg_path,
        "-hide_banner",
        "-y",
        "-i",
        path,
        "-vf",
        f"fps={fps:.6f},scale={width}:-2",
        "-frames:v",
        "1000",
        "-q:v",
        "4",
        out_pattern,
    ]


def extract_audio_argv(path: str, output: str) -> list[str]:
    """Extract a 16 kHz mono FLAC track for speech-to-text (small + lossless)."""
    return [
        settings.ffmpeg_path,
        "-hide_banner",
        "-y",
        "-i",
        path,
        "-vn",
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "flac",
        output,
    ]


def build_concat_list(paths: list[str]) -> str:
    lines = []
    for path in paths:
        escaped = path.replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"
