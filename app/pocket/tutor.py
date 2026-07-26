"""Tutor service — proxy in front of Ollama.

Responsibilities:
- Build the prompt from current video's materials.
- Call Ollama via its HTTP API.
- Parse the JSON response into ChunkOut objects.
- Handle malformed responses gracefully (return [] with an error flag).
- Auto-fallback to current-video-only context if full context is too big.

NOTE: This is intentionally synchronous (called from a thread via
asyncio.to_thread) because Ollama's HTTP API doesn't speak asyncio natively
in v0.1. The async wrapper lives in jobs.py.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.pocket.schemas import ChunkOut

log = logging.getLogger(__name__)


# ── Prompt template ────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a patient tutor. Teach the following video to a busy adult who only "
    "has fragmented time slots (2 min, 5 min, 25 min). Split the video into teachable "
    "chunks, each ending in a check-for-understanding moment. Use ONLY the materials "
    "provided — do not invent facts. Cite no external sources. If the materials are "
    "insufficient for a chunk, skip it rather than guess."
)

USER_TEMPLATE = """Transcript:
{transcript}

Materials (use ONLY these — do not invent):
- Summary: {summary}
- Quiz: {quiz}
- Flashcards: {flashcards}
- Mindmap: {mindmap}

Return STRICT JSON (no prose, no markdown fence, no commentary):
[{{
  "start_ts": <seconds, float>,
  "end_ts":   <seconds, float>,
  "duration_label": "2min" | "5min" | "25min",
  "concept_title":   "<= 8 words",
  "teach_text":      "<= 80 words, plain text, no markdown",
  "check_question":  "<= 30 words"
}}]
"""

# If the full prompt exceeds this many chars, fall back to current-video-only.
# 200k tokens ≈ 800k chars; we stay well under so Ollama has headroom.
PROMPT_CHAR_LIMIT = 200_000


@dataclass
class TutorResult:
    chunks: list[ChunkOut]
    used_fallback: bool
    elapsed_s: float
    error: str | None = None


def _format_user_prompt(
    transcript: str, summary: str, quiz: str, flashcards: str, mindmap: str
) -> str:
    return USER_TEMPLATE.format(
        transcript=transcript[:60_000],   # cap transcript at 60k chars
        summary=summary[:20_000],
        quiz=quiz[:20_000],
        flashcards=flashcards[:20_000],
        mindmap=mindmap[:20_000],
    )


def _format_user_prompt_minimal(transcript: str, summary: str) -> str:
    """Fallback: transcript + this video's summary only. No quiz/flashcards/mindmap."""
    return USER_TEMPLATE.format(
        transcript=transcript[:60_000],
        summary=summary[:20_000],
        quiz="(not provided)",
        flashcards="(not provided)",
        mindmap="(not provided)",
    )


def _ollama_url() -> str:
    """Ollama's default local endpoint. Configurable via env in v0.2."""
    base = getattr(settings, "ollama_base_url", None) or "http://localhost:11434"
    return base.rstrip("/")


def _ollama_model() -> str:
    """Default model. Configurable in v0.2."""
    return getattr(settings, "ollama_model", None) or "llama3.1"


def _call_ollama(prompt: str, timeout_s: float = 120.0) -> str:
    """POST to /api/generate, return the raw text response.

    Uses non-streaming mode for simplicity. v0.2 can switch to streaming.
    """
    url = f"{_ollama_url()}/api/generate"
    payload = {
        "model": _ollama_model(),
        "prompt": prompt,
        "system": SYSTEM_PROMPT,
        "stream": False,
        "options": {
            "temperature": 0.3,    # low — we want deterministic, faithful chunks
            "num_predict": 4096,
        },
    }
    with httpx.Client(timeout=timeout_s) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    return data.get("response", "")


def _parse_chunks(raw: str) -> list[ChunkOut]:
    """Parse Ollama's text response into ChunkOut objects.

    Tolerant: strips markdown fences, finds the first JSON array, falls back
    to scanning for JSON objects if the whole-array parse fails.
    """
    text = raw.strip()
    # Strip ```json ... ``` fences if present
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    # Try whole-array parse first
    try:
        arr = json.loads(text)
        if isinstance(arr, list):
            return [ChunkOut.model_validate(_coerce_chunk(c, i)) for i, c in enumerate(arr)]
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Fallback: find first '[' and matching ']'
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            arr = json.loads(text[start : end + 1])
            if isinstance(arr, list):
                return [ChunkOut.model_validate(_coerce_chunk(c, i)) for i, c in enumerate(arr)]
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    raise ValueError("Could not parse Ollama response as JSON chunk array")


def _coerce_chunk(raw: dict[str, Any], index: int) -> dict[str, Any]:
    """Coerce Ollama's loose field names / types to what ChunkOut expects."""
    label = str(raw.get("duration_label", "5min")).strip()
    if label not in ("2min", "5min", "25min"):
        label = "5min"
    return {
        "id": raw.get("id") or f"chunk-{index}",
        "video_id": raw.get("video_id", ""),
        "index": int(raw.get("index", index)),
        "start_ts": float(raw.get("start_ts", 0.0)),
        "end_ts": float(raw.get("end_ts", 0.0)),
        "duration_label": label,
        "concept_title": str(raw.get("concept_title", ""))[:255],
        "teach_text": str(raw.get("teach_text", "")),
        "check_question": str(raw.get("check_question", "")),
    }


def generate_chunks(
    transcript: str,
    summary: str,
    quiz: str,
    flashcards: str,
    mindmap: str,
) -> TutorResult:
    """Generate teachable chunks for a video. Synchronous; called via to_thread.

    Auto-fallback: if the full-context prompt exceeds PROMPT_CHAR_LIMIT, the
    quiz/flashcards/mindmap slots are dropped and only transcript+summary are sent.
    """
    start = time.monotonic()
    full_prompt = SYSTEM_PROMPT + "\n\n" + _format_user_prompt(transcript, summary, quiz, flashcards, mindmap)
    used_fallback = False

    if len(full_prompt) > PROMPT_CHAR_LIMIT:
        log.info("pocket.tutor: prompt too large (%d chars), using minimal fallback", len(full_prompt))
        prompt = SYSTEM_PROMPT + "\n\n" + _format_user_prompt_minimal(transcript, summary)
        used_fallback = True
    else:
        prompt = full_prompt

    try:
        raw = _call_ollama(prompt)
        chunks = _parse_chunks(raw)
    except Exception as e:  # noqa: BLE001 — we want any error to be reported, not crash
        log.exception("pocket.tutor: Ollama call failed")
        return TutorResult(
            chunks=[],
            used_fallback=used_fallback,
            elapsed_s=time.monotonic() - start,
            error=str(e),
        )

    return TutorResult(
        chunks=chunks,
        used_fallback=used_fallback,
        elapsed_s=time.monotonic() - start,
    )
