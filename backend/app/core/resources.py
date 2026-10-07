from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from app.config import settings
from app.core.uploads import delete_upload, is_valid_id

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE_HOURS = 24


def file_path(resource_id: str) -> Path:
    return settings.outputs_dir / f"{resource_id}.mp4"


def meta_path(resource_id: str) -> Path:
    return settings.outputs_dir / f"{resource_id}.json"


def save(record: dict) -> None:
    """Persist a completed clip's metadata next to its output file."""
    meta_path(record["id"]).write_text(json.dumps(record), encoding="utf-8")


def _load(meta: Path) -> dict | None:
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def get(resource_id: str) -> dict | None:
    if not is_valid_id(resource_id):
        return None
    if not file_path(resource_id).is_file():
        return None
    data = _load(meta_path(resource_id))
    if data is None:
        return None
    return _decorate(data)


def list_recent(max_age_hours: int = DEFAULT_MAX_AGE_HOURS, limit: int = 200) -> list[dict]:
    cutoff = time.time() - max_age_hours * 3600
    items: list[dict] = []
    for meta in settings.outputs_dir.glob("*.json"):
        data = _load(meta)
        if data is None:
            continue
        if float(data.get("created", 0)) < cutoff:
            continue
        if not file_path(data.get("id", "")).is_file():
            continue
        items.append(_decorate(data))
    items.sort(key=lambda item: item.get("created", 0), reverse=True)
    return items[:limit]


def _decorate(data: dict) -> dict:
    resource_id = data.get("id", "")
    output = file_path(resource_id)
    try:
        size = output.stat().st_size
    except OSError:
        size = 0
    data = dict(data)
    data["id"] = resource_id
    data["size"] = size
    data["download_url"] = f"/api/resources/{resource_id}/download"
    data["thumbnail_url"] = _thumbnail_url(data.get("upload_id", ""))
    return data


def _thumbnail_url(upload_id: str) -> str | None:
    if not is_valid_id(upload_id):
        return None
    first = settings.thumbs_dir / upload_id / "thumb_001.jpg"
    if first.is_file():
        return f"/api/uploads/{upload_id}/thumbs/thumb_001.jpg"
    return None


def delete(resource_id: str) -> bool:
    if not is_valid_id(resource_id):
        return False
    output = file_path(resource_id)
    meta = meta_path(resource_id)
    existed = output.is_file() or meta.is_file()
    if not existed:
        return False

    upload_id = ""
    data = _load(meta)
    if data:
        upload_id = str(data.get("upload_id", ""))

    output.unlink(missing_ok=True)
    meta.unlink(missing_ok=True)

    if upload_id and not _has_other_outputs(upload_id, resource_id):
        delete_upload(upload_id)
    return True


def _has_other_outputs(upload_id: str, exclude_id: str) -> bool:
    for meta in settings.outputs_dir.glob("*.json"):
        if meta.stem == exclude_id:
            continue
        data = _load(meta)
        if data and data.get("upload_id") == upload_id:
            return True
    return False
