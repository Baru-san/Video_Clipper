from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ClipMode(str, Enum):
    copy = "copy"
    reencode = "reencode"


class SubtitlesMode(str, Enum):
    none = "none"
    srt = "srt"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class MediaInfoOut(BaseModel):
    duration: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str | None = None
    has_audio: bool
    size_bytes: int


class UploadOut(BaseModel):
    upload_id: str
    metadata: MediaInfoOut
    thumbnail_urls: list[str] = Field(default_factory=list)


class ClipRequest(BaseModel):
    upload_id: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    mode: ClipMode = ClipMode.copy
    scale_height: int | None = Field(default=None, ge=144, le=2160)
    subtitles: SubtitlesMode = SubtitlesMode.none
    translate_to: str | None = Field(default=None, max_length=8)


class JobOut(BaseModel):
    id: str
    status: JobStatus
    percent: float = 0.0
    error: str | None = None
    download_url: str | None = None
    requested_start: float | None = None
    effective_start: float | None = None
    warning: str | None = None
    phase: str | None = None
    subtitle_status: str | None = None
    subtitle_url: str | None = None
    detected_language: str | None = None
