from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.core import resources
from app.media import analyze, stt, translate
from app.media.command_builder import (
    copy_cut_argv,
    extract_audio_argv,
    reencode_cut_argv,
    slice_audio_argv,
)
from app.media.ffmpeg import FFmpegError, FFmpegRunner, Progress, cancel_process
from app.media.subtitles import Transcript, build_srt
from app.models import (
    Aspect,
    Candidate,
    ClipMode,
    JobKind,
    JobOut,
    JobStatus,
    SubtitlesMode,
)

logger = logging.getLogger(__name__)


class QueueFullError(RuntimeError):
    pass


@dataclass
class Job:
    upload_id: str
    source: str
    output: str
    start: float
    duration: float
    mode: ClipMode
    scale_height: int | None = None
    source_name: str = ""
    requested_start: float = 0.0
    effective_start: float = 0.0
    warning: str | None = None
    subtitles: SubtitlesMode = SubtitlesMode.none
    translate_to: str | None = None
    aspect: Aspect = Aspect.original
    title: str | None = None
    kind: JobKind = JobKind.clip
    source_duration: float = 0.0
    analyze_min_length: float = 15.0
    analyze_max_length: float = 60.0
    analyze_max_clips: int = 10
    analyze_language: str | None = None
    candidates: list[Candidate] | None = None
    phase: str | None = None
    subtitle_status: str | None = None
    subtitle_path: str | None = None
    detected_language: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: JobStatus = JobStatus.queued
    percent: float = 0.0
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    proc: asyncio.subprocess.Process | None = None
    cancel_requested: bool = False
    subscribers: set[asyncio.Queue] = field(default_factory=set)

    def to_out(self) -> JobOut:
        download_url = f"/api/jobs/{self.id}/download" if self.status == JobStatus.done else None
        subtitle_url = (
            f"/api/jobs/{self.id}/subtitles.srt"
            if self.subtitle_status == "done"
            else None
        )
        return JobOut(
            id=self.id,
            status=self.status,
            percent=round(self.percent, 2),
            error=self.error,
            download_url=download_url,
            requested_start=self.requested_start,
            effective_start=self.effective_start,
            warning=self.warning,
            phase=self.phase,
            subtitle_status=self.subtitle_status,
            subtitle_url=subtitle_url,
            detected_language=self.detected_language,
            kind=self.kind,
            aspect=self.aspect,
            title=self.title,
            candidates=self.candidates,
        )


class JobQueue:
    """Single-worker queue: exactly one FFmpeg job runs at a time."""

    def __init__(self, runner: FFmpegRunner | None = None, max_size: int | None = None) -> None:
        self._runner = runner or FFmpegRunner()
        self._max_size = max_size if max_size is not None else settings.max_queue_size
        self._jobs: dict[str, Job] = {}
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    async def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker(), name="ffmpeg-worker")

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
        for job in self._jobs.values():
            if job.proc is not None and job.proc.returncode is None:
                await cancel_process(job.proc)

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def submit(
        self,
        *,
        upload_id: str,
        source: str,
        output: str,
        start: float,
        duration: float,
        mode: ClipMode,
        scale_height: int | None = None,
        source_name: str = "",
        requested_start: float | None = None,
        effective_start: float | None = None,
        warning: str | None = None,
        subtitles: SubtitlesMode = SubtitlesMode.none,
        translate_to: str | None = None,
        aspect: Aspect = Aspect.original,
        title: str | None = None,
        kind: JobKind = JobKind.clip,
        source_duration: float = 0.0,
        analyze_min_length: float = 15.0,
        analyze_max_length: float = 60.0,
        analyze_max_clips: int = 10,
        analyze_language: str | None = None,
    ) -> Job:
        if self.active_count() >= self._max_size:
            raise QueueFullError("too many pending jobs")
        job = Job(
            upload_id=upload_id,
            source=source,
            output=output,
            start=start,
            duration=duration,
            mode=mode,
            scale_height=scale_height,
            source_name=source_name,
            requested_start=start if requested_start is None else requested_start,
            effective_start=start if effective_start is None else effective_start,
            warning=warning,
            subtitles=subtitles,
            translate_to=translate_to,
            aspect=aspect,
            title=title,
            kind=kind,
            source_duration=source_duration,
            analyze_min_length=analyze_min_length,
            analyze_max_length=analyze_max_length,
            analyze_max_clips=analyze_max_clips,
            analyze_language=analyze_language,
        )
        self._jobs[job.id] = job
        self._queue.put_nowait(job)
        return job

    def active_count(self) -> int:
        return sum(
            1
            for job in self._jobs.values()
            if job.status in (JobStatus.queued, JobStatus.running)
        )

    def active_paths(self) -> set:
        """Files that must not be swept while their job is queued or running."""
        paths = set()
        for job in self._jobs.values():
            if job.status in (JobStatus.queued, JobStatus.running):
                paths.add(Path(job.source).resolve())
                paths.add(Path(job.output).resolve())
        return paths

    def prune(self, keep_seconds: int = 3600) -> int:
        """Drop finished job records older than `keep_seconds` to bound memory."""
        cutoff = datetime.now(timezone.utc).timestamp() - keep_seconds
        stale = [
            job_id
            for job_id, job in self._jobs.items()
            if job.finished_at is not None
            and job.finished_at.timestamp() < cutoff
            and not job.subscribers
        ]
        for job_id in stale:
            del self._jobs[job_id]
        return len(stale)

    def subscribe(self, job_id: str) -> asyncio.Queue | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        queue: asyncio.Queue = asyncio.Queue(maxsize=16)
        job.subscribers.add(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue) -> None:
        job = self._jobs.get(job_id)
        if job is not None:
            job.subscribers.discard(queue)

    async def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.status in (
            JobStatus.done,
            JobStatus.failed,
            JobStatus.cancelled,
        ):
            return False
        job.cancel_requested = True
        if job.status == JobStatus.running and job.proc is not None:
            await cancel_process(job.proc)
        return True

    def _broadcast(self, job: Job) -> None:
        payload = job.to_out().model_dump(mode="json")
        for queue in list(job.subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def _record_resource(self, job: Job) -> None:
        resource_id = Path(job.output).stem
        try:
            resources.save(
                {
                    "id": resource_id,
                    "upload_id": job.upload_id,
                    "name": job.source_name,
                    "start": job.effective_start,
                    "end": job.effective_start + job.duration,
                    "duration": job.duration,
                    "mode": job.mode.value,
                    "created": job.created_at.timestamp(),
                    "detected_language": job.detected_language,
                    "translated_to": job.translate_to
                    if (job.subtitles == SubtitlesMode.srt and job.subtitle_status == "done")
                    else None,
                }
            )
        except OSError:
            logger.warning("failed to record resource for job %s", job.id, exc_info=True)

    async def _run_subtitles(self, job: Job) -> None:
        resource_id = Path(job.output).stem
        srt_path = settings.outputs_dir / f"{resource_id}.srt"
        audio_path = settings.tmp_dir / f"{job.id}.flac"
        job.subtitle_status = "running"
        try:
            job.phase = "audio"
            self._broadcast(job)
            await self._runner.run(extract_audio_argv(job.output, str(audio_path)))

            job.phase = "transcribe"
            self._broadcast(job)
            transcript = await stt.transcribe(str(audio_path))
            job.detected_language = transcript.language or None
            segments = transcript.segments

            if job.translate_to and job.translate_to != transcript.language:
                job.phase = "translate"
                self._broadcast(job)
                segments = await translate.translate_segments(
                    segments, job.translate_to, transcript.language
                )

            job.phase = "write"
            self._broadcast(job)
            srt_path.write_text(build_srt(segments), encoding="utf-8")
            job.subtitle_path = str(srt_path)
            job.subtitle_status = "done"
        except Exception as exc:  # noqa: BLE001 - clip stays valid without subtitles
            logger.warning("subtitle generation failed for job %s: %s", job.id, exc)
            job.subtitle_status = "failed"
        finally:
            job.phase = None
            try:
                audio_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._broadcast(job)

    async def _transcribe_source(
        self, job: Job, audio_path: str, duration: float
    ) -> Transcript:
        language = job.analyze_language
        if duration <= settings.stt_chunk_seconds:
            return await stt.transcribe(audio_path, language)
        parts: list[tuple[float, Transcript]] = []
        start = 0.0
        index = 0
        while start < duration:
            length = min(settings.stt_chunk_seconds, duration - start)
            chunk = settings.tmp_dir / f"{job.id}_{index}.flac"
            try:
                await self._runner.run(
                    slice_audio_argv(audio_path, str(chunk), start, length)
                )
                parts.append((start, await stt.transcribe(str(chunk), language)))
            finally:
                chunk.unlink(missing_ok=True)
            start += length
            index += 1
        return stt.merge_transcripts(parts)

    async def _run_analyze(self, job: Job) -> None:
        audio_path = settings.tmp_dir / f"{job.id}_full.flac"
        try:
            job.phase = "audio"
            self._broadcast(job)
            await self._runner.run(extract_audio_argv(job.source, str(audio_path)))

            duration = job.source_duration
            job.phase = "transcribe"
            self._broadcast(job)
            transcript = await self._transcribe_source(job, str(audio_path), duration)
            job.detected_language = transcript.language or None

            job.phase = "analyze"
            self._broadcast(job)
            job.candidates = await analyze.extract_highlights(
                transcript,
                duration,
                job.analyze_min_length,
                job.analyze_max_length,
                job.analyze_max_clips,
            )
            job.status = JobStatus.done
            job.percent = 100.0
        except Exception as exc:  # noqa: BLE001
            logger.warning("analysis failed for job %s: %s", job.id, exc)
            job.status = JobStatus.failed
            job.error = str(exc)[-2000:]
        finally:
            job.phase = None
            job.finished_at = datetime.now(timezone.utc)
            try:
                audio_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._broadcast(job)

    async def _run_worker(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._run_job(job)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - worker must never die
                logger.exception("unexpected error running job %s", job.id)
                job.status = JobStatus.failed
                job.error = "internal error"
                job.finished_at = datetime.now(timezone.utc)
                self._broadcast(job)
            finally:
                self._queue.task_done()

    async def _run_job(self, job: Job) -> None:
        if job.cancel_requested:
            job.status = JobStatus.cancelled
            job.finished_at = datetime.now(timezone.utc)
            self._broadcast(job)
            return

        job.status = JobStatus.running
        self._broadcast(job)

        if job.kind == JobKind.analyze:
            await self._run_analyze(job)
            return

        if job.mode == ClipMode.reencode or job.aspect == Aspect.vertical:
            argv = reencode_cut_argv(
                job.source,
                job.output,
                job.start,
                job.duration,
                scale_height=job.scale_height,
                vertical=(job.aspect == Aspect.vertical),
            )
        else:
            argv = copy_cut_argv(job.source, job.output, job.start, job.duration)

        def on_progress(progress: Progress) -> None:
            job.percent = progress.percent
            self._broadcast(job)

        def on_start(proc: asyncio.subprocess.Process) -> None:
            job.proc = proc

        try:
            await self._runner.run(
                argv,
                total_duration=job.duration,
                on_progress=on_progress,
                on_start=on_start,
            )
        except FFmpegError as exc:
            if job.cancel_requested:
                job.status = JobStatus.cancelled
            else:
                job.status = JobStatus.failed
                job.error = str(exc)[-2000:]
        else:
            if job.cancel_requested:
                job.status = JobStatus.cancelled
            else:
                job.percent = 100.0
                if job.subtitles == SubtitlesMode.srt:
                    await self._run_subtitles(job)
                job.status = JobStatus.done
                self._record_resource(job)
        finally:
            job.proc = None
            job.finished_at = datetime.now(timezone.utc)
            if job.status != JobStatus.done:
                try:
                    import os

                    os.unlink(job.output)
                except OSError:
                    pass
            self._broadcast(job)
