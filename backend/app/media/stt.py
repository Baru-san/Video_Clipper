from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from app.config import settings
from app.media.subtitles import Segment, Transcript, parse_verbose_json

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3


class STTError(RuntimeError):
    pass


def merge_transcripts(parts: list[tuple[float, Transcript]]) -> Transcript:
    """Merge (offset, transcript) chunks into one timeline."""
    language = ""
    segments: list[Segment] = []
    for offset, transcript in parts:
        if not language and transcript.language:
            language = transcript.language
        for segment in transcript.segments:
            segments.append(
                Segment(
                    start=segment.start + offset,
                    end=segment.end + offset,
                    text=segment.text,
                )
            )
    segments.sort(key=lambda item: item.start)
    return Transcript(language=language, segments=segments)


def is_configured() -> bool:
    return bool(settings.groq_api_key)


async def transcribe(audio_path: str, language: str | None = None) -> Transcript:
    """Transcribe audio via an OpenAI-compatible Whisper endpoint (Groq)."""
    if not is_configured():
        raise STTError("speech-to-text is not configured")

    size = Path(audio_path).stat().st_size
    if size > settings.stt_max_bytes:
        raise STTError(
            f"audio too large for transcription ({size} bytes); shorten the clip"
        )

    url = f"{settings.stt_base_url.rstrip('/')}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
    data = {"model": settings.stt_model, "response_format": "verbose_json"}
    if language:
        data["language"] = language

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with open(audio_path, "rb") as handle:
                files = {"file": (Path(audio_path).name, handle, "audio/flac")}
                async with httpx.AsyncClient(timeout=settings.subtitle_timeout_seconds) as client:
                    response = await client.post(url, headers=headers, data=data, files=files)
            if response.status_code in RETRYABLE_STATUS:
                raise STTError(f"transcription service returned {response.status_code}")
            if response.status_code != 200:
                raise STTError(f"transcription failed ({response.status_code}): {response.text[:300]}")
            return parse_verbose_json(response.json())
        except STTError as exc:
            last_error = exc
            if "returned" not in str(exc):  # non-retryable
                raise
        except (httpx.HTTPError, OSError) as exc:
            last_error = exc
        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(2 ** (attempt - 1))
            logger.warning("transcription attempt %d failed, retrying", attempt)

    raise STTError(f"transcription failed: {last_error}")
