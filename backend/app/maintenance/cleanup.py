from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from app.config import Settings, settings

logger = logging.getLogger(__name__)


def disk_free_bytes(path: Path) -> int:
    usage = shutil.disk_usage(path)
    return usage.free


def has_free_space(path: Path | None = None) -> bool:
    return disk_free_bytes(path or settings.base_dir) >= settings.min_free_bytes


def _sweep_dir(directory: Path, cutoff: float, keep: set[Path]) -> int:
    removed = 0
    if not directory.is_dir():
        return 0
    for entry in directory.iterdir():
        if entry in keep:
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            removed += 1
        except OSError:
            logger.warning("failed to remove %s", entry, exc_info=True)
    return removed


def sweep(active_paths: set[Path] | None = None) -> int:
    cutoff = time.time() - settings.retention_hours * 3600
    keep = active_paths or set()
    removed = 0
    removed += _sweep_dir(settings.uploads_dir, cutoff, keep)
    removed += _sweep_dir(settings.outputs_dir, cutoff, keep)
    removed += _sweep_dir(settings.thumbs_dir, cutoff, keep)
    removed += _sweep_dir(settings.tmp_dir, cutoff, keep)
    if removed:
        logger.info("cleanup removed %d old entries", removed)
    return removed


async def cleanup_loop(cfg: Settings = settings) -> None:
    while True:
        await asyncio.sleep(cfg.cleanup_interval_seconds)
        try:
            await asyncio.to_thread(sweep)
        except Exception:  # noqa: BLE001
            logger.exception("cleanup sweep failed")
