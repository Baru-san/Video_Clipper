from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from app.config import settings
from app.media.probe import MediaInfo

_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class UploadError(RuntimeError):
    pass


def is_valid_id(upload_id: str) -> bool:
    return bool(_ID_RE.match(upload_id))


def new_upload_path(suffix: str) -> tuple[str, Path]:
    upload_id = uuid.uuid4().hex
    safe_suffix = suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix or "") else ""
    return upload_id, settings.uploads_dir / f"{upload_id}{safe_suffix}"


def _meta_path(upload_id: str) -> Path:
    return settings.uploads_dir / f"{upload_id}.json"


def save_metadata(upload_id: str, path: Path, info: MediaInfo) -> None:
    payload = {
        "upload_id": upload_id,
        "path": str(path),
        "media": {
            "duration": info.duration,
            "width": info.width,
            "height": info.height,
            "fps": info.fps,
            "video_codec": info.video_codec,
            "audio_codec": info.audio_codec,
            "has_audio": info.has_audio,
            "size_bytes": info.size_bytes,
        },
    }
    _meta_path(upload_id).write_text(json.dumps(payload), encoding="utf-8")


def resolve_upload(upload_id: str) -> tuple[Path, MediaInfo]:
    if not is_valid_id(upload_id):
        raise UploadError("invalid upload id")
    meta_path = _meta_path(upload_id)
    if not meta_path.is_file():
        raise UploadError("upload not found")
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    path = Path(data["path"])
    if not path.is_file() or path.parent != settings.uploads_dir:
        raise UploadError("upload file missing")
    media = data["media"]
    info = MediaInfo(
        path=str(path),
        duration=float(media["duration"]),
        width=int(media["width"]),
        height=int(media["height"]),
        fps=float(media["fps"]),
        video_codec=str(media["video_codec"]),
        audio_codec=media["audio_codec"],
        has_audio=bool(media["has_audio"]),
        size_bytes=int(media["size_bytes"]),
    )
    return path, info


def delete_upload(upload_id: str) -> None:
    if not is_valid_id(upload_id):
        return
    _meta_path(upload_id).unlink(missing_ok=True)
    for entry in settings.uploads_dir.glob(f"{upload_id}.*"):
        if entry.suffix != ".json":
            entry.unlink(missing_ok=True)
    thumbs = settings.thumbs_dir / upload_id
    if thumbs.is_dir():
        shutil.rmtree(thumbs, ignore_errors=True)
