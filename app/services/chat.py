"""Chat service — Ollama chat for real-world usage learning.

Two kinds of chat:

1. **Flashcard-scope** (default): triggered by a flashcard's "Teach me
   real-world usage" button. The system prompt focuses on a single
   concept.

2. **Video-scope**: triggered by the "💬 Discuss" tab on the video
   page. The system prompt is built from the video's transcript +
   summary + mindmap + quiz so the user can ask questions about any
   part of the materials.
"""

import json

import httpx

from app.config import settings

# System prompt for real-world usage teaching
CHAT_SYSTEM_PROMPT = """You are an expert tutor specializing in real-world applications.

The user is learning about: "{concept}"

Your job is to teach the user how this concept is used in the real world.
- Give concrete, practical examples
- Relate it to well-known companies, products, or scenarios
- Be encouraging and conversational
- Keep responses concise (2-3 paragraphs max)
- If the user asks follow-up questions, continue the conversation naturally

Do not just repeat the definition — the user already knows that.
Focus on HOW and WHERE this concept is applied in practice.
"""


# System prompt for video-scope chats (whole-video discussion).
# The actual transcript / materials are appended dynamically so the
# LLM can answer questions about them.
#
# MVP3.0 Part B — citation format. The previous prompt asked the LLM
# to cite as `[12:34]` (M:SS) but the transcript we actually feed in
# uses the same `[M:SS]` format. The problem is the LLM often
# paraphrases ("around the 1:20 mark") or invents ranges ("[00:30-
# 01:30]") that don't survive a strict regex match.
#
# New rules (2026-07-14, manualTodo [jul14] #6):
#   1. When you reference a specific moment in the video, ALWAYS use
#      the exact format `[M:SS]` (e.g. `[1:23]`, `[12:34]`, `[1:02:45]`
#      for > 1h). One timestamp per citation, on its own.
#   2. Use the same `M:SS` shape you see in the transcript lines
#      below — the UI's regex picks up exactly that.
#   3. Prefer citing the START of the relevant segment. If the user
#      asks about a concept that's covered from `[4:12]` to `[5:48]`,
#      cite `[4:12]` and mention the range in prose.
#   4. Quote a short verbatim snippet from the transcript alongside
#      the timestamp so the user can confirm the LLM is right.
#   5. If a question cannot be answered from the transcript (the topic
#      is not covered, or the transcript is missing), say so honestly
#      — do not invent timestamps.
#
# Example good response:
#   "Claude Code 并不是免费使用 —— 视频在 [3:45] 明确提到它需要付费并
#    消耗大量 Token。这与 Trae 的免费策略形成对比 (see [8:12])."
#
# Example bad response (the OLD behaviour the user complained about):
#   "很抱歉，虽然视频的字幕文件存在，但无法被正确解析..."
#   (this happened because the backend swallowed the parse error and
#    told the LLM the transcript didn't exist)
VIDEO_CHAT_SYSTEM_PROMPT = """You are a tutor who has just watched this entire video with the student.

The user is learning from a video titled: "{title}"

You have access to:
- The video's full transcript (below), with each line prefixed by `[M:SS]` markers
- A summary of the key points
- A mindmap of the topics covered
- The quiz questions and answers

Your job:
- Answer questions about ANY part of the video's content
- When you cite something from the transcript, include the timestamp
  in the format `[M:SS]` (minutes:seconds, e.g. `[1:23]`, `[12:34]`,
  or `[1:02:45]` for videos over 1 hour). ALWAYS use this exact format
  with no extra characters, no en-dash, no range. The UI converts
  every `[M:SS]` it finds into a clickable link that jumps the video
  to that moment, so consistent format matters.
- After the timestamp, quote a short verbatim snippet from the
  transcript (one sentence or phrase) so the user can verify your
  citation against the original audio.
- If the user asks about a quiz answer, explain WHY the correct
  answer is right using the transcript as evidence, and cite the
  relevant `[M:SS]` markers.
- If the user asks something not covered in the video, say so
  honestly — don't make things up.
- Be concise (2-3 paragraphs max) and conversational.
- Default to the same language as the user's question (English or
  Chinese) — match their tone.

Citation format example:
  "Claude Code 并不是免费使用 —— 视频在 [3:45] 明确提到它需要付费并
   消耗大量 Token。这与 Trae 的免费策略形成对比 (see [8:12])."

---

# VIDEO MATERIALS

## Summary
{summary}

## Mindmap (markdown outline)
{mindmap}

## Quiz (questions + correct answers)
{quiz}

## Transcript (with timestamps)
The transcript below has each line prefixed with `[M:SS]` markers
(e.g. `[0:30] Hello world`). Quote from these lines directly and
cite the corresponding `[M:SS]` marker in your answer:
{transcript}

{materials_section}
"""


def build_system_prompt(concept: str) -> str:
    """Build the system prompt for a flashcard-scope chat session."""
    return CHAT_SYSTEM_PROMPT.format(concept=concept)


def build_video_system_prompt(
    video_title: str,
    summary: str = "",
    mindmap: str = "",
    quiz: str = "",
    transcript: str = "",
    materials_section: str = "",
) -> str:
    """Build the system prompt for a video-scope chat session.

    Args:
        video_title: the video's display title (used as the topic
            header in the prompt)
        summary: pre-rendered summary text (may be empty if the
            video hasn't been processed yet)
        mindmap: pre-rendered mindmap markdown (may be empty)
        quiz: pre-rendered quiz Q&A text (may be empty)
        transcript: full transcript with timestamps in `[mm:ss] text`
            format. If very long, consider truncating before calling.
        materials_section: MVP0.2 — pre-formatted block of the user's
            selected materials (PDF / .md / .txt / .zip extracted text).
            Empty string if no materials are selected. When non-empty
            it's appended after the transcript so the LLM treats
            uploaded materials as additional authoritative context.

    Returns:
        A formatted system prompt string ready to send to Ollama.
    """
    # Provide a friendly default for empty sections so the prompt
    # still reads naturally when the video has no materials yet.
    summary = summary or "(No summary generated yet — ask the user to click 'Generate Materials' on the video page.)"
    mindmap = mindmap or "(No mindmap generated yet.)"
    quiz = quiz or "(No quiz generated yet.)"
    transcript = transcript or "(No transcript available — the video may still be processing.)"

    return VIDEO_CHAT_SYSTEM_PROMPT.format(
        title=video_title,
        summary=summary,
        mindmap=mindmap,
        quiz=quiz,
        transcript=transcript,
        materials_section=materials_section or "",
    )


def transcript_to_chat_text(segments: list[dict]) -> str:
    """Format transcript segments as `[mm:ss] text` lines for the chat.

    Used to put the transcript into the LLM context in a compact
    but timestamped form. Truncates very long videos to keep the
    prompt under ~20K tokens.

    Args:
        segments: list of {start, end, text} dicts (as stored in
            the Asset.content JSON)

    Returns:
        A newline-joined string of `[mm:ss] text` lines.
    """
    if not segments:
        return ""

    # Cap the transcript at MAX_SEGMENTS so a 2-hour video
    # doesn't blow up the prompt. We keep the first half and the
    # last half so the user can ask about the intro or the conclusion.
    MAX_SEGMENTS = 600  # ~10K tokens of transcript
    if len(segments) > MAX_SEGMENTS:
        half = MAX_SEGMENTS // 2
        head = segments[:half]
        tail = segments[-half:]
        omitted = len(segments) - 2 * half
        lines = [_format_ts_line(s) for s in head]
        lines.append(f"\n... [{omitted} segments omitted for length] ...\n")
        lines.extend(_format_ts_line(s) for s in tail)
    else:
        lines = [_format_ts_line(s) for s in segments]
    return "\n".join(lines)


def _format_ts_line(segment: dict) -> str:
    """Format a single transcript segment as `[mm:ss] text`."""
    start = float(segment.get("start", 0))
    text = (segment.get("text") or "").strip()
    mm, ss = divmod(int(start), 60)
    return f"[{mm:02d}:{ss:02d}] {text}"


def render_quiz_for_chat(quiz_json_str: str) -> str:
    """Format the stored quiz JSON as a compact `Q: ... A: ...` list.

    The Asset stores quiz as a JSON string of
    `[{"question": "...", "options": [...], "correct_index": N}, ...]`.
    For the LLM context we just want question + correct answer text
    so the AI can explain "why this answer is correct" without
    wasting tokens on the wrong options.

    Args:
        quiz_json_str: the raw JSON string from the Asset row

    Returns:
        A human-readable multiline string. Empty string if the
        input is empty or unparseable.
    """
    if not quiz_json_str:
        return ""
    try:
        items = json.loads(quiz_json_str)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not items:
        return ""
    lines = []
    for i, q in enumerate(items, 1):
        question = (q.get("question") or "").strip()
        options = q.get("options") or []
        correct_idx = q.get("correct_index")
        if not question:
            continue
        correct = ""
        if isinstance(correct_idx, int) and 0 <= correct_idx < len(options):
            correct = options[correct_idx]
        elif options:
            correct = options[0]
        lines.append(f"Q{i}: {question}")
        if correct:
            lines.append(f"  ✓ {correct}")
    return "\n".join(lines)


def chat_with_ollama(
    messages: list[dict[str, str]],
    system_prompt: str = "",
    model: str | None = None,
) -> str:
    """Send a chat request to Ollama and return the assistant's response.

    Args:
        messages: List of {role, content} dicts (user/assistant history).
        system_prompt: System prompt for this conversation.
        model: Ollama model name (defaults to settings.ollama_model).

    Returns:
        The assistant's response text.
    """
    model = model or settings.ollama_model

    full_messages = []
    if system_prompt:
        full_messages.append({"role": "system", "content": system_prompt})
    full_messages.extend(messages)

    response = httpx.post(
        f"{settings.ollama_base_url}/api/chat",
        json={
            "model": model,
            "stream": False,
            "messages": full_messages,
        },
        # MVP0.2 followup: bumped from hardcoded 120s to a
        # configurable setting. For a video-scope chat with 5
        # selected materials the prompt can be 220K chars (≈110K
        # Chinese tokens); prefill alone takes ~50s on Apple Silicon
        # with `glm-5.2:cloud`.
        timeout=settings.ollama_chat_timeout_seconds,
    )
    response.raise_for_status()

    result = response.json()
    return result.get("message", {}).get("content", "")


# ── Citation parsing (MVP3.0 Part B, manualTodo [jul14] #6) ────────────────
#
# When the AI response contains `[M:SS]` (or `[H:MM:SS]`) markers, the UI
# converts each one to a clickable link that seeks the video to that time
# (same UX as clicking a mindmap node). The backend parses the response and
# returns a structured `citations` list alongside the text so the frontend
# doesn't have to re-parse the regex.
#
# Why a parser here and not just in the frontend?
# - One source of truth — the same regex the system prompt documents is the
#   one we parse. If we change the format later, only this file changes.
# - The parser is unit-testable in pure Python (no browser required) so the
#   citation format can evolve without breaking the UI.
# - The frontend can also re-parse on the client (defense in depth) but
#   having the structured list saves it work and gives us a clean API.
#
# Accepted formats (all of these match because the LLM is inconsistent):
#   [3:45]      → 225 seconds
#   [12:34]     → 754 seconds
#   [1:02:45]   → 3765 seconds  (H:MM:SS for videos > 1 hour)
#   [03:45]     → 225 seconds  (leading zero tolerated)
#   [3:45.5]    → 225.5 seconds (fractional — rounded down to int seconds)
#
# Rejected formats (intentionally):
#   [1:23-1:45]  → range — too easy to mis-parse, frontend would have to
#                   decide which endpoint to seek to. We do NOT match ranges.
#   [~1:23]      → approximation marker — `~` is not in the regex, ignored.
#   [1:23s]      → trailing "s" — not in the regex, ignored.
#   [00:03:45]   → HH:MM:SS with leading zeros — accepted as H:MM:SS
#                   (zero hours is treated as 0).
#
# Implementation note: we use TWO regexes (M:SS and H:MM:SS) and merge
# the results, sorted by character offset. A single regex with an optional
# hours group is ambiguous when fed real LLM output (does `[1:23]` mean
# 1 min 23 sec or 1 hr 23 min?) — two patterns are clearer and easier
# to reason about. `_CITATION_MSS_RE` matches `[M:SS]` (and `[MM:SS]`);
# `_CITATION_HHMMSS_RE` matches `[H:MM:SS]` only.

import re as _re  # local alias — keeps the symbol from colliding with anything
from typing import Any

_CITATION_MSS_RE = _re.compile(r"\[(\d{1,2}):(\d{1,2}(?:\.\d+)?)\]")
_CITATION_HHMMSS_RE = _re.compile(r"\[(\d{1,3}):(\d{2}):(\d{2}(?:\.\d+)?)\]")


def parse_citations(text: str) -> list[dict[str, Any]]:
    """Extract `[M:SS]` / `[H:MM:SS]` citation markers from `text`.

    Returns a list of `{start_seconds, display, offset, raw}` dicts,
    in the order they appear in the text. Each citation corresponds
    to one seek target the user can click.

    Args:
        text: The AI's response text (or any string — empty / None safe).

    Returns:
        List of citations. Empty list if none found. Each entry:
            - start_seconds (float): the seek time in seconds, e.g. 225.0
            - display (str): the original `[M:SS]` string as written,
              e.g. "[3:45]" — useful for rendering the link label
            - offset (int): character offset in `text` where the
              citation starts. Used by the frontend to splice in a
              link without losing position info.
            - raw (str): the full matched substring (same as `display`
              for now, but kept separate so future formats can carry
              metadata without breaking the schema).
    """
    if not text:
        return []
    out: list[dict[str, Any]] = []

    # Match M:SS form first. This is the common case and it's important
    # to match it BEFORE H:MM:SS so we don't double-count e.g. `[1:23]`
    # as both M:SS and H:MM:SS.
    for m in _CITATION_MSS_RE.finditer(text):
        mm_str, ss_str = m.group(1), m.group(2)
        # Sanity: minutes must be < 60, seconds < 60. A malformed
        # `[99:99]` from the LLM is ignored.
        mm = int(mm_str)
        # Keep fractional seconds (e.g. `[1:23.5]`) — the video
        # player's currentTime accepts floats natively, so 0.5s
        # precision gives the user a smooth seek. Round to 2
        # decimals to avoid FP noise.
        ss = round(float(ss_str), 2)
        if mm >= 60 or int(ss) >= 60:
            continue
        start = mm * 60 + ss
        out.append({
            "start_seconds": float(start),
            "display": m.group(0),
            "offset": m.start(),
            "raw": m.group(0),
        })

    # Now H:MM:SS form. Only matches when there's an extra hour
    # segment, so it doesn't collide with the M:SS form.
    for m in _CITATION_HHMMSS_RE.finditer(text):
        h_str, mm_str, ss_str = m.group(1), m.group(2), m.group(3)
        h = int(h_str)
        mm = int(mm_str)
        ss = round(float(ss_str), 2)
        if h >= 100 or mm >= 60 or int(ss) >= 60:
            continue
        start = h * 3600 + mm * 60 + ss
        out.append({
            "start_seconds": float(start),
            "display": m.group(0),
            "offset": m.start(),
            "raw": m.group(0),
        })

    # Sort by character offset so the citations are in the same
    # order the LLM wrote them in the response. Stable enough for
    # the frontend to iterate top-to-bottom.
    out.sort(key=lambda c: c["offset"])
    return out