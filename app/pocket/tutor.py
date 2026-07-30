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


logger = logging.getLogger(__name__)


class OllamaUnavailableError(Exception):
    """Raised when Ollama can't be reached, times out, or returns a 5xx.

    The router catches this and returns a clean JSON response so the iOS
    UI shows a helpful message instead of a 500.
    """

    def __init__(self, kind: str, detail: str = "") -> None:
        self.kind = kind  # "unreachable" | "timeout" | "http_5xx"
        self.detail = detail
        super().__init__(f"Ollama {kind}: {detail}".strip(": "))
from app.pocket.schemas import ChunkOut

log = logging.getLogger(__name__)


# ── Prompt template ────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a patient, expert teacher. The student has NEVER watched this video. "
    "You will split the video into teachable chunks. For each chunk, write a "
    "self-contained mini-lesson that teaches the actual content of that portion of "
    "the video — NOT a teaser, NOT a summary, NOT a '30-second version'. The student "
    "should be able to learn the material from your lesson alone, without ever "
    "watching the source video.\n\n"
    "Rules you must follow:\n"
    "1. Use ONLY the materials provided. Do not invent facts, names, numbers, or "
    "examples. If a chunk's source material is too thin to teach, set "
    "teach_text to a single sentence explaining the gap and skip the check question.\n"
    "2. Quote 1 to 2 short lines from the transcript per chunk (use the 'transcript_quote' "
    "field). The quote must be a verbatim substring of the provided transcript. This lets "
    "the student verify your lesson against the source.\n"
    "3. The check question must be answerable from THIS chunk's teach_text alone. "
    "Never ask about something the student has no way of knowing from your lesson.\n"
    "4. teach_text must be a full lesson (3-6 sentences), not a one-liner. Use plain "
    "language, give examples if the materials have them, and explain WHY not just WHAT.\n"
    "5. Citation/source discipline: only say things present in the transcript, summary, "
    "quiz, flashcards, or mindmap. If you would be tempted to say something not in the "
    "materials, don't say it.\n"
    "6. LANGUAGE: respond in the language explicitly named in the user prompt's "
    "LANGUAGE directive. That directive is the source of truth — NOT the language "
    "of this system prompt. Technical terms (API names, library names, code, "
    "identifiers) stay in their original form regardless of the response language."
)

USER_TEMPLATE = """Transcript (the full video, with [seconds] timestamps):
{transcript}

Materials (use ONLY these — do not invent):
- Summary: {summary}
- Quiz: {quiz}
- Flashcards: {flashcards}
- Mindmap: {mindmap}

The student will read your chunks in order. For each chunk:
- focus on the time range you specify in start_ts/end_ts
- but you have the FULL transcript above as context so each chunk can reference
  what came before naturally
- teach_text should be a mini-lesson, not a teaser
- transcript_quote must be a VERBATIM substring of the transcript (look it up
  in the [seconds] text above and copy it exactly, including the [seconds] prefix)

Return STRICT JSON (no prose, no markdown fence, no commentary):
[{{
  "start_ts": <seconds, float>,
  "end_ts":   <seconds, float>,
  "duration_label": "2min" | "5min" | "25min",
  "concept_title":   "<= 8 words",
  "transcript_quote": "<= 30 words, VERBATIM from the transcript, including [seconds] prefix>",
  "teach_text":      "3-6 sentences. A mini-lesson, not a teaser.",
  "check_question":  "<= 30 words, answerable from teach_text alone"
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
    transcript: str, summary: str, quiz: str, flashcards: str, mindmap: str,
    materials_section: str = "",
    language: str | None = None,
) -> str:
    base = USER_TEMPLATE.format(
        transcript=transcript[:60_000],   # cap transcript at 60k chars
        summary=summary[:20_000],
        quiz=quiz[:20_000],
        flashcards=flashcards[:20_000],
        mindmap=mindmap[:20_000],
    )
    # MVP0.2 followup #2 (anti-drift language): inject a strong
    # LANGUAGE directive at the TOP of the user prompt so the LLM
    # matches the transcript's language. Placed at the top because
    # prompt attention matters — late directives get ignored when the
    # model has already committed to the system prompt's language.
    # Defaults to "en" when video.language is None (legacy / unset).
    directive = _language_directive(language)
    return directive + "\n\n" + base + materials_section


def _language_directive(language: str | None) -> str:
    """Build a short LANGUAGE directive for the user prompt.

    Defaults to English when `language` is None or empty. Whisper
    language codes are passed through as-is (e.g. 'zh', 'ja', 'en');
    the model knows what to do with them. The directive is also
    strengthened with the look-and-feel of the transcript so the
    fallback `en` case is at least informed by the actual content.
    """
    code = (language or "").strip().lower() or "en"
    return (
        f"LANGUAGE: Respond in {code}. Match the language of the transcript "
        f"and the user's selected materials. Do NOT translate proper nouns, "
        f"API names, library names, code, or technical identifiers — keep "
        f"those in their original form. If the transcript is mostly English, "
        f"respond in English. If it's mostly Chinese, respond in Chinese. "
        f"This directive overrides the language of the system prompt."
    )


def _format_user_prompt_minimal(
    transcript: str, summary: str, materials_section: str = "",
    language: str | None = None,
) -> str:
    """Fallback: transcript + this video's summary only. No quiz/flashcards/mindmap.

    MVP0.2 followup: the previous version still attached the full
    materials_section, which on a 5-material video is 200K chars on its own
    and pushed the prompt past the timeout. Now we *also* size-cap the
    materials so the minimal fallback stays within ~80K chars total
    (transcript 60K + summary 20K + materials truncated to fit).

    MVP0.2 followup #2 (anti-drift language): also inject the LANGUAGE
    directive at the top so the minimal fallback respects the video's
    language too.
    """
    base = USER_TEMPLATE.format(
        transcript=transcript[:60_000],
        summary=summary[:20_000],
        quiz="(not provided)",
        flashcards="(not provided)",
        mindmap="(not provided)",
    )
    directive = _language_directive(language)
    # We have ~80K chars of headroom used by transcript+summary. If the
    # materials section is larger than 40K, truncate it with a warning.
    MAX_MATERIALS_CHARS_MINIMAL = 40_000
    if materials_section and len(materials_section) > MAX_MATERIALS_CHARS_MINIMAL:
        truncated = (
            materials_section[:MAX_MATERIALS_CHARS_MINIMAL]
            + f"\n\n[... materials truncated to {MAX_MATERIALS_CHARS_MINIMAL:,} chars "
            f"for the minimal fallback prompt; original was {len(materials_section):,} chars ...]"
        )
        return directive + "\n\n" + base + truncated
    return directive + "\n\n" + base + materials_section


def _ollama_url() -> str:
    """Ollama's default local endpoint. Configurable via env in v0.2."""
    base = getattr(settings, "ollama_base_url", None) or "http://localhost:11434"
    return base.rstrip("/")


def _ollama_model() -> str:
    """Default model. Configurable in v0.2."""
    return getattr(settings, "ollama_model", None) or "llama3.1"


def is_ollama_available(timeout_s: float = 2.0) -> tuple[bool, str]:
    """Lightweight ping to Ollama's `/api/tags` endpoint.

    Returns (ok, detail). Used by the iOS app on startup to decide whether
    to show a "Tutor offline" banner. Cheap (~50ms locally) so safe to
    call frequently if needed.

    ok=True: Ollama is reachable (any 2xx response counts).
    ok=False: connection refused / timeout / non-2xx.
    """
    url = f"{_ollama_url()}/api/tags"
    try:
        with httpx.Client(timeout=timeout_s) as client:
            r = client.get(url)
        if r.status_code >= 200 and r.status_code < 300:
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code}"
    except httpx.ConnectError as e:
        return False, f"unreachable: {e}"
    except httpx.TimeoutException as e:
        return False, f"timeout: {e}"
    except Exception as e:  # noqa: BLE001
        return False, f"error: {e}"


def _call_ollama(prompt: str, timeout_s: float | None = None) -> str:
    """POST to /api/generate, return the raw text response.

    Uses non-streaming mode for simplicity. v0.2 can switch to streaming.

    Raises OllamaUnavailableError on connection failure, timeout, or 5xx.

    The default timeout is `settings.ollama_pocket_tutor_timeout_seconds`
    (600s). The chunk-generation prompt can be 200K chars (≈100K tokens)
    because of the materials section; on Apple Silicon with
    `glm-5.2:cloud` that takes ~120-180s to prefill + generate.
    Callers can override `timeout_s` for unit tests.
    """
    if timeout_s is None:
        from app.config import settings
        timeout_s = settings.ollama_pocket_tutor_timeout_seconds
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
    try:
        with httpx.Client(timeout=timeout_s) as client:
            r = client.post(url, json=payload)
    except httpx.ConnectError as e:
        logger.warning("Ollama unreachable at %s: %s", url, e)
        raise OllamaUnavailableError("unreachable", str(e)) from e
    except httpx.TimeoutException as e:
        logger.warning("Ollama timed out after %ss: %s", timeout_s, e)
        raise OllamaUnavailableError("timeout", str(e)) from e

    if r.status_code >= 500:
        logger.warning("Ollama returned %s: %s", r.status_code, r.text[:200])
        raise OllamaUnavailableError("http_5xx", f"HTTP {r.status_code}")

    # 4xx (other than our bad prompt) also indicates a problem with Ollama
    if r.status_code >= 400:
        logger.warning("Ollama returned %s: %s", r.status_code, r.text[:200])
        raise OllamaUnavailableError("http_5xx", f"HTTP {r.status_code}")

    try:
        data = r.json()
    except json.JSONDecodeError as e:
        raise OllamaUnavailableError("http_5xx", f"non-JSON response: {e}") from e

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
        "concept_title": str(raw.get("concept_title", ""))[:255],        "transcript_quote": str(raw.get("transcript_quote", ""))[:500],        "teach_text": str(raw.get("teach_text", "")),
        "check_question": str(raw.get("check_question", "")),
    }


def generate_chunks(
    transcript: str,
    summary: str,
    quiz: str,
    flashcards: str,
    mindmap: str,
    materials_section: str = "",
    language: str | None = None,
) -> TutorResult:
    """Generate teachable chunks for a video. Synchronous; called via to_thread.

    Args:
        language: Whisper-style language code (e.g. 'zh', 'en') from
            `videos.language`. When None we default to 'en' (per
            user instruction 2026-07-30). The directive is injected at
            the top of the user prompt so the tutor matches the
            transcript's language.

    Auto-fallback: if the full-context prompt exceeds PROMPT_CHAR_LIMIT, the
    quiz/flashcards/mindmap slots are dropped and only transcript+summary are sent.
    """
    start = time.monotonic()
    full_prompt = SYSTEM_PROMPT + "\n\n" + _format_user_prompt(
        transcript, summary, quiz, flashcards, mindmap, materials_section,
        language=language,
    )
    used_fallback = False

    if len(full_prompt) > PROMPT_CHAR_LIMIT:
        log.info("pocket.tutor: prompt too large (%d chars), using minimal fallback", len(full_prompt))
        prompt = SYSTEM_PROMPT + "\n\n" + _format_user_prompt_minimal(
            transcript, summary, materials_section, language=language,
        )
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


# ── Grading (v0.1.3) ─────────────────────────────────────

# Verdict taxonomy — the only three values the AI is allowed to return.
# Single source of truth for both single + batch grading endpoints.
VERDICT_GOT_IT = "got_it"
VERDICT_PARTIAL = "partial"
VERDICT_MISSED = "missed"
VALID_VERDICTS = {VERDICT_GOT_IT, VERDICT_PARTIAL, VERDICT_MISSED}


GRADING_SYSTEM_PROMPT = (
    "You are a fair, concise teacher grading a student's answer. "
    "Compare the student's answer to the canonical answer (which is what the "
    "video actually taught). Return a verdict and a 1-2 sentence explanation.\n\n"
    "Verdicts (return EXACTLY one of these three strings, no synonyms):\n"
    '  - "got_it":   student captured the key idea(s) of the canonical answer\n'
    '  - "partial":  student got the gist but missed a key part\n'
    '  - "missed":   student is wrong or off-topic\n\n'
    "Explanation: 1-2 sentences, plain language, no markdown. Be specific about "
    "what was right and what was missing. No hedging, no apologies, no 'great "
    "question' filler. If the student wrote nothing, return verdict=missed with "
    "explanation='No answer provided.'\n"
    "NEVER invent information not in the canonical answer. If the canonical "
    "answer itself is thin, say so honestly in the explanation."
)


GRADING_USER_TEMPLATE = """Canonical answer (what the video actually said):
{canonical}

Student's answer:
{user}

Return STRICT JSON (no prose, no markdown fence):
{{
  "verdict": "got_it" | "partial" | "missed",
  "explanation": "<= 2 sentences"
}}
"""


def _call_ollama_grading(prompt: str) -> dict:
    """One Ollama call for grading. Returns parsed JSON dict.

    Raises OllamaUnavailableError on connection failure, timeout, or 5xx.
    Returns {} on parse failure (Ollama reachable but bad output).
    """
    url = f"{_ollama_url()}/api/generate"
    payload = {
        "model": _ollama_model(),
        "prompt": prompt,
        "system": GRADING_SYSTEM_PROMPT,
        "stream": False,
        "options": {
            "temperature": 0.1,    # very low — grading should be deterministic
            "num_predict": 256,    # short — we only need verdict + 1-2 sentences
        },
    }
    try:
        # Grading prompt is short (the question + a chunk) so a 60s
        # timeout is plenty. Configurable via settings.ollama_pocket_grading_timeout_seconds.
        from app.config import settings
        with httpx.Client(timeout=settings.ollama_pocket_grading_timeout_seconds) as client:
            r = client.post(url, json=payload)
    except httpx.ConnectError as e:
        logger.warning("Ollama unreachable for grading: %s", e)
        raise OllamaUnavailableError("unreachable", str(e)) from e
    except httpx.TimeoutException as e:
        logger.warning("Ollama timed out for grading: %s", e)
        raise OllamaUnavailableError("timeout", str(e)) from e

    if r.status_code >= 400:
        logger.warning("Ollama grading returned %s: %s", r.status_code, r.text[:200])
        raise OllamaUnavailableError("http_5xx", f"HTTP {r.status_code}")

    try:
        data = r.json()
    except json.JSONDecodeError as e:
        raise OllamaUnavailableError("http_5xx", f"non-JSON response: {e}") from e

    text = data.get("response", "").strip()
    # Strip markdown fence if present
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}


def grade_single(user_answer: str, canonical_answer: str) -> dict:
    """Grade one student answer. Returns {verdict, explanation} or {error}.

    If the student didn't write anything, short-circuit with `missed` and
    a clear "no answer" explanation (no need to call Ollama). If Ollama
    returns an empty explanation for a non-trivial answer, fall back to a
    verdict-specific stock message so the UI never shows a blank box.
    """
    user_clean = (user_answer or "").strip()
    if not user_clean:
        return {
            "verdict": VERDICT_MISSED,
            "explanation": "No answer provided. Type what you remember, then try again.",
        }

    prompt = GRADING_USER_TEMPLATE.format(
        canonical=canonical_answer or "(no canonical answer provided)",
        user=user_clean,
    )
    try:
        out = _call_ollama_grading(prompt)
        verdict = str(out.get("verdict", "")).strip().lower()
        if verdict not in VALID_VERDICTS:
            verdict = VERDICT_MISSED
        explanation = str(out.get("explanation", "")).strip()[:500]
        if not explanation:
            # Fallback explanations so the UI never shows a blank box.
            explanation = _FALLBACK_EXPLANATION.get(verdict, _FALLBACK_EXPLANATION[VERDICT_MISSED])
        return {"verdict": verdict, "explanation": explanation}
    except OllamaUnavailableError:
        # Let the router handle this — it returns a 200 with a helpful
        # explanation + ollama_unavailable=true flag.
        raise
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "verdict": VERDICT_MISSED, "explanation": "Grading failed."}


# Fallback explanations used when Ollama returns a verdict but no explanation
# (or returns an empty string). Keeps the iOS feedback box from showing blank.
_FALLBACK_EXPLANATION: dict[str, str] = {
    VERDICT_GOT_IT: "You got it — your answer captures the key idea.",
    VERDICT_PARTIAL: "Partially — you got the gist but missed a key part. Read the teach text again.",
    VERDICT_MISSED: "Missed — your answer doesn't match what the video taught. Read the chunk again and try once more.",
}


def grade_batch(items: list[dict]) -> list[dict]:
    """Grade multiple (user, canonical) pairs in one Ollama call.

    items: [{"user_answer": str, "canonical_answer": str}, ...]
    returns: list of {"verdict", "explanation"} aligned with input.
    """
    # Build a single prompt with a numbered list
    parts = []
    for i, it in enumerate(items):
        parts.append(
            f"[{i}]\nCanonical: {it.get('canonical_answer', '') or '(none)'}\n"
            f"Student:   {it.get('user_answer', '')}\n"
        )
    user_prompt = (
        "Grade each of the following student answers. Return STRICT JSON array, "
        "one verdict object per student, IN THE SAME ORDER.\n\n"
        + "\n".join(parts) + "\n\n"
        "Return:\n"
        '[{"verdict": "got_it"|"partial"|"missed", "explanation": "<= 2 sentences"}, ...]'
    )
    try:
        out_text = _call_ollama_grading(user_prompt)
        # _call_ollama_grading returns a dict for single; for batch we need raw text
        # Re-fetch raw response for batch — the helper above parses single only.
        url = f"{_ollama_url()}/api/generate"
        payload = {
            "model": _ollama_model(),
            "prompt": user_prompt,
            "system": GRADING_SYSTEM_PROMPT,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 1024},
        }
        with httpx.Client(timeout=120.0) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            raw = r.json().get("response", "").strip()
        # Strip fence
        if raw.startswith("```"):
            first_nl = raw.find("\n")
            if first_nl != -1:
                raw = raw[first_nl + 1 :]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        # Find JSON array
        try:
            arr = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("[")
            end = raw.rfind("]")
            arr = json.loads(raw[start:end + 1]) if (start != -1 and end != -1) else []
        # Align results with input length
        results = []
        for i in range(len(items)):
            if i < len(arr) and isinstance(arr[i], dict):
                v = str(arr[i].get("verdict", "")).strip().lower()
                if v not in VALID_VERDICTS:
                    v = VERDICT_MISSED
                e = str(arr[i].get("explanation", "")).strip()[:500]
                if not e:
                    e = _FALLBACK_EXPLANATION.get(v, _FALLBACK_EXPLANATION[VERDICT_MISSED])
                results.append({"verdict": v, "explanation": e})
            else:
                results.append({"verdict": VERDICT_MISSED, "explanation": "Grading failed for this item."})
        return results
    except OllamaUnavailableError as e:
        # Whole batch fails the same way — return the specific message.
        logger.warning("grade_batch: Ollama %s: %s", e.kind, e.detail)
        msg = _OLLAMA_DOWN_BATCH_EXPLANATION.get(
            e.kind,
            "AI tutor is currently unavailable. Your answers are saved — try again later.",
        )
        return [{"verdict": VERDICT_MISSED, "explanation": msg} for _ in items]
    except Exception as e:  # noqa: BLE001
        return [{"verdict": VERDICT_MISSED, "explanation": f"Batch grading failed: {e}"} for _ in items]


_OLLAMA_DOWN_BATCH_EXPLANATION: dict[str, str] = {
    "unreachable": "AI tutor offline. Make sure Ollama is running, then retry.",
    "timeout":     "AI tutor timed out. Retry in a moment.",
    "http_5xx":    "AI tutor error. Retry in a moment.",
}
