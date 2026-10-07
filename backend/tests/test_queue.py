from __future__ import annotations

from pathlib import Path

import pytest

from app.core.queue import JobQueue, QueueFullError
from app.models import ClipMode


def _kwargs(**overrides):
    base = {
        "upload_id": "u",
        "source": "/tmp/src.mp4",
        "output": "/tmp/out.mp4",
        "start": 0.0,
        "duration": 1.0,
        "mode": ClipMode.copy,
    }
    base.update(overrides)
    return base


def test_queue_full_raises():
    queue = JobQueue(max_size=1)
    queue.submit(**_kwargs())
    with pytest.raises(QueueFullError):
        queue.submit(**_kwargs())


def test_active_paths_includes_queued_jobs():
    queue = JobQueue(max_size=2)
    queue.submit(**_kwargs(source="/data/src.mp4", output="/data/out.mp4"))
    paths = queue.active_paths()
    assert Path("/data/src.mp4").resolve() in paths
    assert Path("/data/out.mp4").resolve() in paths


def test_prune_skips_unfinished_jobs():
    queue = JobQueue()
    queue.submit(**_kwargs())
    assert queue.prune() == 0
