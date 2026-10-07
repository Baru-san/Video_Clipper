from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    pass


@dataclass
class Progress:
    frame: int = 0
    out_time: float = 0.0
    speed: float = 0.0
    percent: float = 0.0
    done: bool = False


def _parse_out_time(value: str) -> float:
    value = value.strip()
    if not value or value == "N/A":
        return 0.0
    if ":" in value:
        try:
            hours, minutes, seconds = value.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


class FFmpegRunner:
    """Runs FFmpeg as a subprocess and reports progress from `-progress pipe:1`."""

    def __init__(self, ffmpeg_path: str | None = None) -> None:
        self.ffmpeg_path = ffmpeg_path or settings.ffmpeg_path

    async def run(
        self,
        argv: list[str],
        *,
        total_duration: float | None = None,
        on_progress: Callable[[Progress], None] | None = None,
        on_start: Callable[[asyncio.subprocess.Process], None] | None = None,
    ) -> None:
        stderr_chunks: list[bytes] = []
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if on_start is not None:
            on_start(proc)

        async def drain_stderr() -> None:
            assert proc.stderr is not None
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                stderr_chunks.append(chunk)

        stderr_task = asyncio.create_task(drain_stderr())
        progress = Progress()
        assert proc.stdout is not None
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", "replace").strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key == "frame":
                progress.frame = int(value or 0)
            elif key in ("out_time_us", "out_time_ms"):
                # ffmpeg historically emits microseconds for both keys.
                progress.out_time = int(value or 0) / 1_000_000
            elif key == "out_time":
                progress.out_time = _parse_out_time(value)
            elif key == "speed":
                progress.speed = _parse_out_time(value.replace("x", ""))
            elif key == "progress" and value == "end":
                progress.done = True
                if total_duration:
                    progress.percent = 100.0
                if on_progress is not None:
                    on_progress(progress)
                break

            if total_duration and total_duration > 0:
                progress.percent = min(progress.out_time / total_duration * 100.0, 100.0)
            if on_progress is not None:
                on_progress(progress)

        returncode = await proc.wait()
        await stderr_task

        if returncode != 0:
            message = b"".join(stderr_chunks).decode("utf-8", "replace").strip()
            logger.warning("ffmpeg failed (%s): %s", returncode, message)
            raise FFmpegError(message or f"ffmpeg exited with {returncode}")


async def cancel_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if proc.stdin is not None:
            proc.stdin.write(b"q")
            await proc.stdin.drain()
            proc.stdin.close()
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
