from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.media.ffmpeg import FFmpegError, FFmpegRunner, Progress, cancel_process
from app.media.command_builder import copy_cut_argv, reencode_cut_argv
from app.models import ClipMode, JobOut, JobStatus

logger = logging.getLogger(__name__)


@dataclass
class Job:
    upload_id: str
    source: str
    output: str
    start: float
    duration: float
    mode: ClipMode
    scale_height: int | None = None
    requested_start: float = 0.0
    effective_start: float = 0.0
    warning: str | None = None
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
        return JobOut(
            id=self.id,
            status=self.status,
            percent=round(self.percent, 2),
            error=self.error,
            download_url=download_url,
            requested_start=self.requested_start,
            effective_start=self.effective_start,
            warning=self.warning,
        )


class JobQueue:
    """Single-worker queue: exactly one FFmpeg job runs at a time."""

    def __init__(self, runner: FFmpegRunner | None = None) -> None:
        self._runner = runner or FFmpegRunner()
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
        requested_start: float | None = None,
        effective_start: float | None = None,
        warning: str | None = None,
    ) -> Job:
        job = Job(
            upload_id=upload_id,
            source=source,
            output=output,
            start=start,
            duration=duration,
            mode=mode,
            scale_height=scale_height,
            requested_start=start if requested_start is None else requested_start,
            effective_start=start if effective_start is None else effective_start,
            warning=warning,
        )
        self._jobs[job.id] = job
        self._queue.put_nowait(job)
        return job

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

        if job.mode == ClipMode.reencode:
            argv = reencode_cut_argv(
                job.source,
                job.output,
                job.start,
                job.duration,
                scale_height=job.scale_height,
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
                job.status = JobStatus.done
                job.percent = 100.0
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
