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
from app.core import resources
from app.core.queue import JobQueue, QueueFullError
from app.core.uploads import (
    delete_upload,
    get_upload_name,
    new_upload_path,
    resolve_upload,
    save_metadata,
)
from app.media import probe as probe_module
from app.media import analyze, stt, translate
from app.media.command_builder import thumbnail_argv
from app.media.ffmpeg import FFmpegRunner
from app.media.keyframes import get_keyframes, snap_to_keyframe
from app.maintenance.cleanup import has_free_space
from app.models import (
    AnalyzeRequest,
    Aspect,
    BatchClipRequest,
    ClipMode,
    ClipRequest,
    JobKind,
    JobOut,
    MediaInfoOut,
    SubtitlesMode,
    UploadOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

CHUNK_SIZE = 1024 * 1024


def _queue(request: Request) -> JobQueue:
    return request.app.state.queue


@router.post("/uploads", response_model=UploadOut)
async def upload_video(request: Request, file: UploadFile = File(...)) -> UploadOut:
    if not has_free_space():
        raise HTTPException(status_code=507, detail="insufficient storage")

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

    original_name = Path(file.filename or "").name[:200]
    save_metadata(upload_id, dest, info, name=original_name)
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


async def _prepare_clip(
    queue: JobQueue,
    *,
    upload_id: str,
    source: Path,
    info,
    start: float,
    end: float,
    mode: ClipMode,
    scale_height: int | None,
    subtitles: SubtitlesMode,
    translate_to: str | None,
    aspect: Aspect,
    title: str | None,
):
    if start >= end:
        raise HTTPException(status_code=422, detail="start must be before end")
    if end > info.duration + 0.05:
        raise HTTPException(status_code=422, detail="end exceeds video duration")

    is_lossless = mode == ClipMode.copy and aspect == Aspect.original
    effective_start = start
    warning: str | None = None
    if is_lossless:
        try:
            frames = await get_keyframes(str(source))
        except Exception:  # noqa: BLE001 - fall back to requested start
            frames = []
        snapped = snap_to_keyframe(frames, start) if frames else start
        if abs(snapped - start) > 0.05:
            effective_start = snapped
            warning = (
                f"Lossless cut starts at the nearest keyframe "
                f"({snapped:.2f}s instead of {start:.2f}s). "
                "Use Precise mode for frame-accurate cuts."
            )

    duration = end - effective_start
    if subtitles == SubtitlesMode.srt:
        if not info.has_audio:
            raise HTTPException(status_code=422, detail="video has no audio track")
        if duration > settings.subtitle_max_duration_seconds:
            raise HTTPException(
                status_code=422,
                detail=f"clip too long for subtitles (max {settings.subtitle_max_duration_seconds:.0f}s)",
            )
        if not stt.is_configured():
            raise HTTPException(status_code=503, detail="subtitles are not available")
        if translate_to:
            if translate_to not in settings.subtitle_targets:
                raise HTTPException(status_code=422, detail="unsupported translation target")
            if not translate.is_configured():
                raise HTTPException(status_code=503, detail="translation is not available")

    output = settings.outputs_dir / f"{uuid.uuid4().hex}.mp4"
    try:
        return queue.submit(
            upload_id=upload_id,
            source=str(source),
            output=str(output),
            start=effective_start,
            duration=duration,
            mode=mode,
            scale_height=scale_height,
            source_name=get_upload_name(upload_id),
            requested_start=start,
            effective_start=effective_start,
            warning=warning,
            subtitles=subtitles,
            translate_to=translate_to,
            aspect=aspect,
            title=title,
        )
    except QueueFullError as exc:
        raise HTTPException(status_code=429, detail="server busy, try again later") from exc


@router.post("/clips", response_model=JobOut)
async def create_clip(request: Request, payload: ClipRequest) -> JobOut:
    try:
        source, info = resolve_upload(payload.upload_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="upload not found") from exc

    job = await _prepare_clip(
        _queue(request),
        upload_id=payload.upload_id,
        source=source,
        info=info,
        start=payload.start,
        end=payload.end,
        mode=payload.mode,
        scale_height=payload.scale_height,
        subtitles=payload.subtitles,
        translate_to=payload.translate_to,
        aspect=payload.aspect,
        title=payload.title,
    )
    return job.to_out()


@router.post("/clips/batch", response_model=list[JobOut])
async def create_clips_batch(request: Request, payload: BatchClipRequest) -> list[JobOut]:
    try:
        source, info = resolve_upload(payload.upload_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="upload not found") from exc

    jobs = []
    for item in payload.clips:
        job = await _prepare_clip(
            _queue(request),
            upload_id=payload.upload_id,
            source=source,
            info=info,
            start=item.start,
            end=item.end,
            mode=payload.mode,
            scale_height=None,
            subtitles=payload.subtitles,
            translate_to=payload.translate_to,
            aspect=payload.aspect,
            title=item.title,
        )
        jobs.append(job.to_out())
    return jobs


@router.post("/analyze", response_model=JobOut)
async def create_analyze(request: Request, payload: AnalyzeRequest) -> JobOut:
    try:
        source, info = resolve_upload(payload.upload_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="upload not found") from exc

    if not info.has_audio:
        raise HTTPException(status_code=422, detail="video has no audio track")
    if info.duration > settings.analyze_max_source_seconds:
        raise HTTPException(
            status_code=422,
            detail=f"video too long for analysis (max {settings.analyze_max_source_seconds:.0f}s)",
        )
    if payload.min_length >= payload.max_length:
        raise HTTPException(status_code=422, detail="min_length must be less than max_length")
    if not stt.is_configured():
        raise HTTPException(status_code=503, detail="analysis is not available")
    if not analyze.is_configured():
        raise HTTPException(status_code=503, detail="analysis is not available")

    try:
        job = _queue(request).submit(
            upload_id=payload.upload_id,
            source=str(source),
            output="",
            start=0.0,
            duration=info.duration,
            mode=ClipMode.copy,
            source_name=get_upload_name(payload.upload_id),
            kind=JobKind.analyze,
            source_duration=info.duration,
            analyze_min_length=payload.min_length,
            analyze_max_length=payload.max_length,
            analyze_max_clips=min(payload.max_clips, settings.analyze_max_clips),
            analyze_language=payload.language,
        )
    except QueueFullError as exc:
        raise HTTPException(status_code=429, detail="server busy, try again later") from exc
    return job.to_out()


@router.get("/uploads/{upload_id}/keyframes")
async def upload_keyframes(upload_id: str) -> dict[str, list[float]]:
    try:
        source, _ = resolve_upload(upload_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="upload not found") from exc
    frames = await get_keyframes(str(source))
    return {"keyframes": [round(f, 3) for f in frames]}


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


@router.get("/jobs/{job_id}/subtitles.srt")
async def download_job_subtitles(request: Request, job_id: str) -> FileResponse:
    job = _queue(request).get(job_id)
    if job is None or not job.subtitle_path:
        raise HTTPException(status_code=404, detail="subtitles not found")
    path = Path(job.subtitle_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="subtitles not found")
    return FileResponse(
        path, media_type="application/x-subrip", filename="subtitles.srt"
    )


@router.get("/resources")
async def list_resources() -> dict[str, list[dict]]:
    return {"items": resources.list_recent()}


@router.delete("/resources/{resource_id}", status_code=204)
async def delete_resource(resource_id: str) -> None:
    if not resources.delete(resource_id):
        raise HTTPException(status_code=404, detail="resource not found")


@router.get("/resources/{resource_id}/download")
async def download_resource(resource_id: str) -> FileResponse:
    record = resources.get(resource_id)
    if record is None:
        raise HTTPException(status_code=404, detail="resource not found")
    path = resources.file_path(resource_id)
    name = record.get("name") or "clip"
    stem = Path(name).stem[:60] or "clip"
    return FileResponse(path, media_type="video/mp4", filename=f"{stem}.mp4")


@router.get("/resources/{resource_id}/subtitles.srt")
async def download_resource_subtitles(resource_id: str) -> FileResponse:
    if resources.get(resource_id) is None:
        raise HTTPException(status_code=404, detail="resource not found")
    path = resources.subtitle_path(resource_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="subtitles not found")
    return FileResponse(
        path, media_type="application/x-subrip", filename="subtitles.srt"
    )


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
