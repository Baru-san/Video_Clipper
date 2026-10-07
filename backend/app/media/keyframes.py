from __future__ import annotations

import os

from app.media import probe as probe_module

_cache: dict[str, tuple[float, list[float]]] = {}


async def get_keyframes(path: str) -> list[float]:
    """Return keyframe timestamps for `path`, cached by (path, mtime)."""
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return []
    cached = _cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    frames = await probe_module.keyframes(path)
    _cache[path] = (mtime, frames)
    return frames


def snap_to_keyframe(frames: list[float], target: float) -> float:
    return probe_module.nearest_keyframe(frames, target)
