from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.config import settings
from app.core.queue import JobQueue
from app.core.uploads import (
    delete_upload,
    new_upload_path,
    resolve_upload,
    save_metadata,
)
from app.media import probe as probe_module
from app.media.command_builder import thumbnail_argv
from app.media.ffmpeg import FFmpegRunner
from app.models import ClipRequest, JobOut, MediaInfoOut, UploadOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

CHUNK_SIZE = 1024 * 1024


def _queue(request: Request) -> JobQueue:
    return request.app.state.queue


@router.post("/uploads", response_model=UploadOut)
async def upload_video(request: Request, file: UploadFile = File(...)) -> UploadOut:
    upload_id, dest = new_upload_path(Path(file.filename or "").suffix)

    written = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="file too large")
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except OSError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="failed to store upload") from exc
    finally:
        await file.close()

    try:
        info = await probe_module.probe(str(dest))
    except probe_module.ProbeError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    if info.duration <= 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=415, detail="could not determine duration")
    if info.duration > settings.max_duration_seconds:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="video too long")

    save_metadata(upload_id, dest, info)
    await _generate_thumbnails(str(dest), upload_id, info.duration)

    thumb_urls = [
        f"/api/uploads/{upload_id}/thumbs/{p.name}"
        for p in sorted((settings.thumbs_dir / upload_id).glob("thumb_*.jpg"))
    ]

    return UploadOut(
        upload_id=upload_id,
        metadata=MediaInfoOut(
            duration=info.duration,
            width=info.width,
            height=info.height,
            fps=info.fps,
            video_codec=info.video_codec,
            audio_codec=info.audio_codec,
            has_audio=info.has_audio,
            size_bytes=info.size_bytes,
        ),
        thumbnail_urls=thumb_urls,
    )


async def _generate_thumbnails(source: str, upload_id: str, duration: float) -> None:
    if duration <= 0:
        return
    thumb_dir = settings.thumbs_dir / upload_id
    thumb_dir.mkdir(parents=True, exist_ok=True)
    fps = max(min(settings.thumbnail_count / duration, 1.0), 0.001)
    pattern = str(thumb_dir / "thumb_%03d.jpg")
    runner = FFmpegRunner()
    try:
        await runner.run(thumbnail_argv(source, pattern, fps))
    except Exception:  # noqa: BLE001 - thumbnails are best-effort
        logger.warning("thumbnail generation failed for %s", upload_id, exc_info=True)


@router.get("/uploads/{upload_id}", response_model=MediaInfoOut)
async def get_upload(upload_id: str) -> MediaInfoOut:
    try:
        _, info = resolve_upload(upload_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="upload not found") from exc
    return MediaInfoOut(
        duration=info.duration,
        width=info.width,
        height=info.height,
        fps=info.fps,
        video_codec=info.video_codec,
        audio_codec=info.audio_codec,
        has_audio=info.has_audio,
        size_bytes=info.size_bytes,
    )


@router.delete("/uploads/{upload_id}", status_code=204)
async def remove_upload(upload_id: str) -> None:
    delete_upload(upload_id)


_THUMB_RE = re.compile(r"^thumb_\d{3}\.jpg$")


@router.get("/uploads/{upload_id}/thumbs/{name}")
async def get_thumbnail(upload_id: str, name: str) -> FileResponse:
    if not re.fullmatch(r"[0-9a-f]{32}", upload_id) or not _THUMB_RE.match(name):
        raise HTTPException(status_code=404, detail="not found")
    path = settings.thumbs_dir / upload_id / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(request: Request, job_id: str) -> JobOut:
    job = _queue(request).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.to_out()


@router.post("/clips", response_model=JobOut)
async def create_clip(request: Request, payload: ClipRequest) -> JobOut:
    try:
        source, info = resolve_upload(payload.upload_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="upload not found") from exc

    if payload.start >= payload.end:
        raise HTTPException(status_code=422, detail="start must be before end")
    if payload.end > info.duration + 0.05:
        raise HTTPException(status_code=422, detail="end exceeds video duration")

    duration = payload.end - payload.start
    output_id = uuid.uuid4().hex
    output = settings.outputs_dir / f"{output_id}.mp4"

    job = _queue(request).submit(
        upload_id=payload.upload_id,
        source=str(source),
        output=str(output),
        start=payload.start,
        duration=duration,
        mode=payload.mode,
        scale_height=payload.scale_height,
    )
    return job.to_out()


@router.delete("/jobs/{job_id}", status_code=202)
async def cancel_job(request: Request, job_id: str) -> dict[str, bool]:
    cancelled = await _queue(request).cancel(job_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="job not running or not found")
    return {"cancelled": True}


@router.get("/jobs/{job_id}/events")
async def job_events(request: Request, job_id: str) -> StreamingResponse:
    queue = _queue(request)
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    subscriber = queue.subscribe(job_id)
    assert subscriber is not None

    async def event_stream():
        try:
            yield f"data: {json.dumps(job.to_out().model_dump(mode='json'))}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    update = await asyncio.wait_for(subscriber.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(update)}\n\n"
                if update.get("status") in ("done", "failed", "cancelled"):
                    break
        finally:
            queue.unsubscribe(job_id, subscriber)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/jobs/{job_id}/download")
async def download_job(request: Request, job_id: str) -> FileResponse:
    job = _queue(request).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status.value != "done":
        raise HTTPException(status_code=409, detail="job not finished")
    output = Path(job.output)
    if not output.is_file():
        raise HTTPException(status_code=404, detail="output missing")
    return FileResponse(output, media_type="video/mp4", filename="clip.mp4")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
