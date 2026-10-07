from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import settings
from app.core.queue import JobQueue
from app.maintenance.cleanup import cleanup_loop
from app.media.ffmpeg import verify_tools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    versions = await verify_tools()
    for tool, version in versions.items():
        logger.info("%s: %s", tool, version)

    queue = JobQueue()
    await queue.start()
    app.state.queue = queue

    cleanup_task = asyncio.create_task(cleanup_loop(queue=queue), name="cleanup")
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        await queue.stop()


app = FastAPI(title="Video Clipper API", lifespan=lifespan)
app.include_router(router)

if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
else:
    logger.info("frontend build not found at %s; API-only mode", FRONTEND_DIST)
