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
VIDEO_CHAT_SYSTEM_PROMPT = """You are a tutor who has just watched this entire video with the student.

The user is learning from a video titled: "{title}"

You have access to:
- The video's full transcript (below)
- A summary of the key points
- A mindmap of the topics covered
- The quiz questions and answers

Your job:
- Answer questions about ANY part of the video's content
- When you cite something from the transcript, include the timestamp
  in the format `[12:34]` so the user can jump to that point
- If the user asks about a quiz answer, explain WHY the correct
  answer is right using the transcript as evidence
- If the user asks something not covered in the video, say so
  honestly — don't make things up
- Be concise (2-3 paragraphs max) and conversational
- Default to the same language as the user's question (English or
  Chinese) — match their tone

---

# VIDEO MATERIALS

## Summary
{summary}

## Mindmap (markdown outline)
{mindmap}

## Quiz (questions + correct answers)
{quiz}

## Transcript (with timestamps)
The transcript is long, so use the timestamps to find what the
user is asking about:
{transcript}
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
        timeout=120.0,
    )
    response.raise_for_status()

    result = response.json()
    return result.get("message", {}).get("content", "")