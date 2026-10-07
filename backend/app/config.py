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

    # --- Subtitles / speech-to-text (Groq) ---
    stt_base_url: str = field(
        default_factory=lambda: os.environ.get(
            "VC_STT_BASE_URL", "https://api.groq.com/openai/v1"
        )
    )
    stt_model: str = field(
        default_factory=lambda: os.environ.get(
            "VC_STT_MODEL", "whisper-large-v3-turbo"
        )
    )
    groq_api_key: str = field(
        default_factory=lambda: os.environ.get("GROQ_API_KEY", "")
    )
    stt_max_bytes: int = field(
        default_factory=lambda: _env_int("VC_STT_MAX_BYTES", 25 * 1024 * 1024)
    )
    subtitle_max_duration_seconds: float = field(
        default_factory=lambda: float(os.environ.get("VC_SUBTITLE_MAX_DURATION", "1200"))
    )
    subtitle_timeout_seconds: float = field(
        default_factory=lambda: float(os.environ.get("VC_SUBTITLE_TIMEOUT", "300"))
    )

    # --- Translation (DeepSeek, OpenAI-compatible) ---
    translate_base_url: str = field(
        default_factory=lambda: os.environ.get(
            "VC_TRANSLATE_BASE_URL", "https://api.deepseek.com"
        )
    )
    translate_model: str = field(
        default_factory=lambda: os.environ.get("VC_TRANSLATE_MODEL", "deepseek-flash")
    )
    deepseek_api_key: str = field(
        default_factory=lambda: os.environ.get("DEEPSEEK_API_KEY", "")
    )
    translate_batch_size: int = field(
        default_factory=lambda: _env_int("VC_TRANSLATE_BATCH_SIZE", 30)
    )
    subtitle_targets: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            t.strip()
            for t in os.environ.get("VC_SUBTITLE_TARGETS", "en,id").split(",")
            if t.strip()
        )
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
