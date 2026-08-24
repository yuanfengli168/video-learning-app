"""LLM service — generate learning materials from video transcripts.

Day 4 (mvp2-production-patches): the underlying LLM call goes through
the LiteLLM fallback wrapper (`app.services.llm_providers`). Per-tier
provider chains (FREE -> groq, PAID/ADMIN -> ollama+openai) with
per-user rate limiting and Ollama quota tracking.

The public function signature is preserved so callers (the generate
worker in `app/routers/generation.py`) don't have to know about the
wrapper. Two new optional kwargs were added:
  - user_role (int, UserRole enum value)
  - user_id (str, Firebase uid)
If both are omitted, the call uses Ollama directly with no rate limit
check (legacy behavior). All callers updated in this commit pass both.

Sends a transcript to the configured LLM and returns structured JSON:
- Markdown summary
- Mindmap data (Markmap-compatible markdown)
- Quiz questions and answers
- Flashcard terms and definitions
"""

import json
import re
from typing import Any

from app.config import settings

# System prompt for generating learning materials
GENERATION_SYSTEM_PROMPT = """You are an expert educational content generator.
Given a video transcript with timestamps, generate learning materials in JSON format.

You MUST respond with ONLY a valid JSON object, no markdown, no explanation.
The JSON must have exactly these keys:

{
  "summary": "A concise markdown summary of the key points (200-500 words)",
  "mindmap": "Markmap-compatible markdown for a mindmap. Use # for root, ## for branches, ### for sub-branches",
  "flashcards": [
    {"term": "Concept name", "definition": "Clear explanation of the concept"}
  ],
  "quiz": [
    {
      "question": "A clear question",
      "options": ["A", "B", "C", "D"],
      "answer": "Correct option text",
      "answer_index": 0
    }
  ],
  "topic_timestamps": [
    {"topic": "Topic name exactly as it appears in mindmap", "start": 60, "end": 120}
  ]
}

Rules:
- Generate 5-10 flashcards covering the most important concepts
- Generate 3-5 quiz questions with 4 options each
- The summary should be in markdown with headers and bullet points
- The mindmap should be hierarchical markdown that Markmap can render
- answer_index is 0-based (0=A, 1=B, 2=C, 3=D)
- TOPIC TIMESTAMPS: For each major topic in the mindmap, identify which part of the video discusses it
  - Use the timestamps from the transcript to determine start and end (in seconds)
  - Each topic must match EXACTLY (case-sensitive) a node name from the mindmap
  - Generate 5-15 topic_timestamps entries covering the most important topics
  - Topics should not overlap in time ranges
  - Skip very short topics (less than 10 seconds)
  - For parent topics in the mindmap, you can include them too if they cover a clear time range
"""


def _extract_json(text: str) -> dict[str, Any]:
    """Extract JSON from a text response, handling markdown code fences
    and the LLM's habit of adding prose around the JSON object.

    Tries four strategies in order, returning the first one that produces
    a valid dict:

    1. Direct parse — response is pure JSON
    2. Code fence — response is wrapped in ```json ... ```
    3. Strip preamble — strip a leading "Sure! Here is the JSON:" / etc.,
       then direct parse (LLM sometimes adds a sentence before the JSON)
    4. Brace match — find the outermost { ... } block

    If all four fail, raises ValueError with the raw response included in
    the message so the failure is debuggable from the job log alone
    (no need to re-run with debug logging).
    """
    # Strategy 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: code fence
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 3: strip a leading prose preamble
    # Common LLM preambles: "Sure!", "Here is the JSON:", "Of course!",
    # "Certainly!", sometimes followed by a newline. Strip them and retry
    # the direct parse. This handles responses like:
    #   "Sure! Here is the JSON:\n\n{ ... }"
    stripped = re.sub(
        r"^\s*(sure|here is|here's|certainly|of course|okay|ok)[\s!,.]*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    if stripped != text:
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    # Strategy 4: outermost { ... } block
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    # All strategies failed. Raise with the raw response (truncated to
    # 500 chars) included in the error so the job log is self-explanatory.
    preview = text[:500] + ("..." if len(text) > 500 else "")
    raise ValueError(
        f"Could not extract valid JSON from LLM response "
        f"(len={len(text)}). Raw response preview: {preview!r}"
    )


def generate_materials(
    transcript: dict[str, Any],
    model: str | None = None,
    on_progress: "callable | None" = None,
    *,
    user_role: int | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Generate learning materials from a transcript using the configured
    provider chain (Ollama / Groq / OpenAI via LiteLLM).

    Args:
        transcript: Dict with 'segments' (list of {start, end, text}).
        model: Provider-specific model name. If None, picks the right
            default for the user's tier from settings. (Legacy callers
            passing the Ollama model name are silently ignored; the
            provider chain picks per-user.)
        on_progress: Optional callback `fn(done: int, total: int, message: str)`
            called at key milestones so the background worker can update
            the UI's progress bar. Currently called at:
              - 5/100 "Building prompt..."
              - 15/100 "Calling LLM..."
              - 90/100 "Parsing response..."
            Anything past 90% is "saving to database" which the caller
            does outside this function.
        user_role: UserRole enum value (0=ADMIN, 1=PAID, 2=FREE).
            Used to pick the provider chain + apply rate limit thresholds.
            Required (Day 4) — callers that omit it get FREE defaults
            (groq only) so legacy code stays safe but is rate-limited
            appropriately.
        user_id: Firebase uid. Required for rate limit tracking.

    Returns:
        Dict with keys: summary, mindmap, flashcards, quiz.
        On rate limit or all-providers-failed, raises LlmCallError with
        a structured dict in .detail (HTTP-friendly error format).
    """
    # Default to FREE if caller didn't pass user_role (legacy safety).
    # The router in app/routers/generation.py is the only real caller and
    # was updated in this commit to always pass both.
    if user_role is None:
        from app.auth.roles import UserRole
        user_role = UserRole.FREE
    if user_id is None:
        # Synthetic uid for unauthenticated tests. In production, the
        # router always provides user_id from the Firebase claims.
        user_id = "anonymous"

    # Build transcript text from segments
    if on_progress:
        on_progress(5, 100, "Building prompt from transcript...")
    transcript_text = "\n".join(
        f"[{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text']}"
        for seg in transcript.get("segments", [])
    )

    if not transcript_text.strip():
        raise ValueError("Transcript is empty — cannot generate materials")

    if on_progress:
        on_progress(15, 100, "Calling LLM (tier-aware provider chain)...")

    # Delegate to the LiteLLM wrapper. It handles provider selection,
    # rate limiting, Ollama quota tracking, and JSON parsing hints.
    from app.services.llm_providers import call_llm_with_fallback

    result = call_llm_with_fallback(
        messages=[
            {"role": "system", "content": GENERATION_SYSTEM_PROMPT},
            {"role": "user", "content": f"Transcript:\n\n{transcript_text}"},
        ],
        user_role=user_role,
        user_id=user_id,
        json_mode=True,
    )

    # Map the structured response to the historical exception interface
    # so the router in app/routers/generation.py can still detect
    # rate-limit / quota errors and surface them to the user.
    if result["status"] == "rate_limited":
        if on_progress:
            on_progress(95, 100, "Rate limit hit")
        raise LlmCallError(
            status_code=429,
            detail={
                "error": "rate_limited",
                "message": result["message"],
                "retry_after_seconds": result["retry_after_seconds"],
            },
        )
    if result["status"] == "provider_unavailable":
        if on_progress:
            on_progress(95, 100, "All providers failed")
        raise LlmCallError(
            status_code=503,
            detail={
                "error": "provider_unavailable",
                "message": result["message"],
                "attempts": result["attempts"],
            },
        )

    content = result["content"]
    if on_progress:
        provider = result.get("provider", "unknown")
        on_progress(90, 100, f"Parsing {provider} response...")

    return _extract_json(content)


class LlmCallError(Exception):
    """Raised when generate_materials() fails due to rate limit or
    all-providers-failed. .status_code is HTTP-friendly (429 or 503)
    and .detail is a structured dict the router surfaces to the UI.
    """

    def __init__(self, status_code: int, detail: dict[str, Any]):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail.get("message", "LLM call failed"))


def generate_summary(
    transcript: dict[str, Any],
    model: str | None = None,
    *,
    user_role: int | None = None,
    user_id: str | None = None,
) -> str:
    """Generate just the markdown summary."""
    materials = generate_materials(
        transcript, model,
        user_role=user_role, user_id=user_id,
    )
    return materials.get("summary", "")


def generate_mindmap(
    transcript: dict[str, Any],
    model: str | None = None,
    *,
    user_role: int | None = None,
    user_id: str | None = None,
) -> str:
    """Generate just the mindmap markdown."""
    materials = generate_materials(
        transcript, model,
        user_role=user_role, user_id=user_id,
    )
    return materials.get("mindmap", "")


def generate_flashcards(
    transcript: dict[str, Any],
    model: str | None = None,
    *,
    user_role: int | None = None,
    user_id: str | None = None,
) -> list[dict[str, str]]:
    """Generate just the flashcards."""
    materials = generate_materials(
        transcript, model,
        user_role=user_role, user_id=user_id,
    )
    return materials.get("flashcards", [])


def generate_quiz(
    transcript: dict[str, Any],
    model: str | None = None,
    *,
    user_role: int | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Generate just the quiz."""
    materials = generate_materials(
        transcript, model,
        user_role=user_role, user_id=user_id,
    )
    return materials.get("quiz", [])