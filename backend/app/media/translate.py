from __future__ import annotations

import asyncio
import json
import logging

import httpx

from app.config import settings
from app.media.subtitles import Segment, language_name

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class TranslateError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(settings.deepseek_api_key)


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def translate_segments(
    segments: list[Segment],
    target: str,
    source: str | None = None,
) -> list[Segment]:
    """Translate segment texts into `target`, preserving timestamps."""
    if not segments:
        return []
    if not is_configured():
        raise TranslateError("translation is not configured")

    texts = [segment.text for segment in segments]
    translated: list[str] = []
    for batch in _chunks(texts, settings.translate_batch_size):
        translated.extend(await _translate_batch(batch, target, source))

    if len(translated) != len(segments):
        raise TranslateError("translation returned a mismatched number of segments")
    return [
        Segment(start=segment.start, end=segment.end, text=text)
        for segment, text in zip(segments, translated)
    ]


async def _translate_batch(texts: list[str], target: str, source: str | None) -> list[str]:
    try:
        result = await _request(texts, target, source)
        if len(result) == len(texts):
            return result
        logger.warning("translation length mismatch (%d != %d)", len(result), len(texts))
    except TranslateError:
        if len(texts) == 1:
            raise
    if len(texts) == 1:
        raise TranslateError("could not translate segment")
    mid = len(texts) // 2
    return await _translate_batch(texts[:mid], target, source) + await _translate_batch(
        texts[mid:], target, source
    )


async def _request(texts: list[str], target: str, source: str | None) -> list[str]:
    target_name = language_name(target)
    source_hint = f" from {language_name(source)}" if source else ""
    system = (
        f"You are a professional subtitle translator. Translate each string in the input "
        f"JSON array{source_hint} into {target_name}. Keep the same order and count. "
        f"Translate naturally and concisely for subtitles. "
        f'Reply with ONLY JSON of the form {{"translations": ["...", "..."]}}.'
    )
    payload = {
        "model": settings.translate_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(texts, ensure_ascii=False)},
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
                raise TranslateError(f"translation service returned {response.status_code}")
            if response.status_code != 200:
                raise TranslateError(
                    f"translation failed ({response.status_code}): {response.text[:300]}"
                )
            content = response.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            result = data.get("translations")
            if not isinstance(result, list):
                raise TranslateError("translation response missing 'translations' array")
            return [str(item).strip() for item in result]
        except TranslateError as exc:
            last_error = exc
            if "returned" not in str(exc):
                raise
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            last_error = exc
        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(2 ** (attempt - 1))
            logger.warning("translation attempt %d failed, retrying", attempt)

    raise TranslateError(f"translation failed: {last_error}")
