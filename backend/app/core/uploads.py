from __future__ import annotations

import json
import re
import shutil
import time
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


def save_metadata(upload_id: str, path: Path, info: MediaInfo, name: str = "") -> None:
    payload = {
        "upload_id": upload_id,
        "path": str(path),
        "name": name,
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


def get_upload_name(upload_id: str) -> str:
    if not is_valid_id(upload_id):
        return ""
    meta_path = _meta_path(upload_id)
    if not meta_path.is_file():
        return ""
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("name", "") or "")


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


def list_uploads(max_age_hours: int = 24, limit: int = 50) -> list[dict]:
    """List recent source uploads (for transcript / reuse), newest first."""
    cutoff = time.time() - max_age_hours * 3600
    items: list[dict] = []
    for meta in settings.uploads_dir.glob("*.json"):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        path = Path(data.get("path", ""))
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        upload_id = str(data.get("upload_id", ""))
        media = data.get("media", {})
        thumb = settings.thumbs_dir / upload_id / "thumb_001.jpg"
        items.append(
            {
                "upload_id": upload_id,
                "name": str(data.get("name", "") or ""),
                "duration": float(media.get("duration", 0.0)),
                "size": int(media.get("size_bytes", 0)),
                "created": mtime,
                "thumbnail_url": (
                    f"/api/uploads/{upload_id}/thumbs/thumb_001.jpg"
                    if thumb.is_file()
                    else None
                ),
            }
        )
    items.sort(key=lambda item: item["created"], reverse=True)
    return items[:limit]


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
