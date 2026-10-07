from __future__ import annotations

import asyncio
import json
import logging

import httpx

from app.config import settings
from app.media.subtitles import Transcript
from app.models import Candidate

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class AnalyzeError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(settings.deepseek_api_key)


def _transcript_text(transcript: Transcript, max_chars: int = 24000) -> str:
    lines = [f"{seg.start:.1f}|{seg.text}" for seg in transcript.segments]
    text = "\n".join(lines)
    return text[:max_chars]


def sanitize(
    raw: list[dict],
    duration: float,
    min_length: float,
    max_length: float,
    max_clips: int,
) -> list[Candidate]:
    """Clamp length, drop invalid/overlapping picks, cap the count."""
    candidates: list[Candidate] = []
    for item in raw:
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        if start < 0 or end <= start:
            continue
        if start >= duration:
            continue
        end = min(end, duration)
        if end - start < min_length:
            end = min(start + min_length, duration)
            if end <= start:
                continue
        if end - start > max_length:
            end = start + max_length
        candidates.append(
            Candidate(
                start=round(start, 2),
                end=round(end, 2),
                title=str(item.get("title", "") or "")[:120],
                reason=str(item.get("reason", "") or "")[:300],
                score=float(item.get("score", 0) or 0),
            )
        )

    candidates.sort(key=lambda c: c.start)
    selected: list[Candidate] = []
    for candidate in candidates:
        if selected and candidate.start < selected[-1].end - 0.01:
            continue
        selected.append(candidate)
        if len(selected) >= max_clips:
            break
    return selected


async def extract_highlights(
    transcript: Transcript,
    duration: float,
    min_length: float,
    max_length: float,
    max_clips: int,
) -> list[Candidate]:
    if not is_configured():
        raise AnalyzeError("highlight analysis is not configured")
    if not transcript.segments:
        return []

    system = (
        "You are an expert short-form video editor. Given a transcript where each line is "
        "`start_seconds|text`, choose the most engaging, self-contained moments suitable as "
        f"standalone clips between {min_length:.0f} and {max_length:.0f} seconds long. "
        f"Return at most {max_clips} clips, non-overlapping, in chronological order. "
        "Prefer complete ideas with a hook. Reply with ONLY JSON: "
        '{"clips":[{"start":<sec>,"end":<sec>,"title":"<short title>",'
        '"reason":"<why>","score":<0-1>}]}'
    )
    payload = {
        "model": settings.translate_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": _transcript_text(transcript)},
        ],
    }
    url = f"{settings.translate_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=settings.subtitle_timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
            if response.status_code in RETRYABLE_STATUS:
                raise AnalyzeError(f"analysis service returned {response.status_code}")
            if response.status_code != 200:
                raise AnalyzeError(f"analysis failed ({response.status_code}): {response.text[:300]}")
            content = response.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            raw = data.get("clips")
            if not isinstance(raw, list):
                raise AnalyzeError("analysis response missing 'clips' array")
            return sanitize(raw, duration, min_length, max_length, max_clips)
        except AnalyzeError as exc:
            last_error = exc
            if "returned" not in str(exc):
                raise
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            last_error = exc
        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(2 ** (attempt - 1))
            logger.warning("analysis attempt %d failed, retrying", attempt)

    raise AnalyzeError(f"analysis failed: {last_error}")
