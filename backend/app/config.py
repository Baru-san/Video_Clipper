from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    base_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("VC_DATA_DIR", "data"))
    )
    ffmpeg_path: str = field(default_factory=lambda: os.environ.get("VC_FFMPEG", "ffmpeg"))
    ffprobe_path: str = field(
        default_factory=lambda: os.environ.get("VC_FFPROBE", "ffprobe")
    )
    max_upload_bytes: int = field(
        default_factory=lambda: _env_int("VC_MAX_UPLOAD_BYTES", 500 * 1024 * 1024)
    )
    max_duration_seconds: float = field(
        default_factory=lambda: float(os.environ.get("VC_MAX_DURATION", "7200"))
    )
    retention_hours: int = field(
        default_factory=lambda: _env_int("VC_RETENTION_HOURS", 24)
    )
    min_free_bytes: int = field(
        default_factory=lambda: _env_int("VC_MIN_FREE_BYTES", 1024 * 1024 * 1024)
    )
    thumbnail_count: int = field(
        default_factory=lambda: _env_int("VC_THUMBNAIL_COUNT", 20)
    )
    cleanup_interval_seconds: int = field(
        default_factory=lambda: _env_int("VC_CLEANUP_INTERVAL", 3600)
    )
    max_queue_size: int = field(
        default_factory=lambda: _env_int("VC_MAX_QUEUE_SIZE", 20)
    )

    @property
    def uploads_dir(self) -> Path:
        return self.base_dir / "uploads"

    @property
    def outputs_dir(self) -> Path:
        return self.base_dir / "outputs"

    @property
    def thumbs_dir(self) -> Path:
        return self.base_dir / "thumbs"

    @property
    def tmp_dir(self) -> Path:
        return self.base_dir / "tmp"

    def ensure_dirs(self) -> None:
        for directory in (
            self.base_dir,
            self.uploads_dir,
            self.outputs_dir,
            self.thumbs_dir,
            self.tmp_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
