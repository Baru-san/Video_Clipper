from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from app.config import settings
from app.media.command_builder import keyframe_probe_argv, probe_argv


class ProbeError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str | None
    has_audio: bool
    size_bytes: int


def _parse_fraction(value: str) -> float:
    if not value:
        return 0.0
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            d = float(den)
            return float(num) / d if d else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


async def _run(argv: list[str]) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise ProbeError(stderr.decode("utf-8", "replace").strip() or "ffprobe failed")
    return stdout.decode("utf-8", "replace")


async def probe(path: str) -> MediaInfo:
    raw = await _run(probe_argv(path))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProbeError("invalid ffprobe output") from exc

    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("file has no video stream")

    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = _parse_fraction(fmt.get("duration", ""))
    if duration <= 0:
        duration = _parse_fraction(video.get("duration", ""))

    return MediaInfo(
        path=path,
        duration=duration,
        width=int(video.get("width", 0) or 0),
        height=int(video.get("height", 0) or 0),
        fps=_parse_fraction(video.get("r_frame_rate", "")),
        video_codec=str(video.get("codec_name", "")),
        audio_codec=str(audio.get("codec_name")) if audio else None,
        has_audio=audio is not None,
        size_bytes=int(fmt.get("size", 0) or 0),
    )


async def keyframes(path: str) -> list[float]:
    raw = await _run(keyframe_probe_argv(path))
    return parse_keyframes(raw)


def parse_keyframes(raw: str) -> list[float]:
    """Parse ffprobe CSV output; each line may carry extra trailing columns."""
    result: list[float] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        first = line.split(",", 1)[0].strip()
        try:
            result.append(float(first))
        except ValueError:
            continue
    return result


def nearest_keyframe(frames: list[float], target: float) -> float:
    candidate = 0.0
    for ts in frames:
        if ts <= target:
            candidate = ts
        else:
            break
    return candidate
