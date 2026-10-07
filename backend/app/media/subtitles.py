from __future__ import annotations

from dataclasses import dataclass, field

LANGUAGE_NAMES = {
    "en": "English",
    "id": "Indonesian",
}

MAX_LINE_CHARS = 42
MAX_LINES = 2


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    language: str
    segments: list[Segment] = field(default_factory=list)


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code.lower(), code)


def parse_verbose_json(data: dict) -> Transcript:
    language = str(data.get("language", "") or "")
    segments: list[Segment] = []
    for item in data.get("segments", []) or []:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        segments.append(
            Segment(
                start=float(item.get("start", 0.0)),
                end=float(item.get("end", item.get("start", 0.0))),
                text=text,
            )
        )
    return Transcript(language=language, segments=segments)


def _timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def wrap_text(text: str, max_chars: int = MAX_LINE_CHARS, max_lines: int = MAX_LINES) -> str:
    """Greedy word-wrap to at most `max_lines` lines of ~max_chars."""
    words = text.split()
    if not words:
        return ""
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines[:max_lines])


def build_srt(segments: list[Segment]) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        text = wrap_text(segment.text)
        if not text:
            continue
        blocks.append(
            f"{index}\n{_timestamp(segment.start)} --> {_timestamp(segment.end)}\n{text}\n"
        )
    return "\n".join(blocks)
