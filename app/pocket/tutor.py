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
    "identifiers) stay in their original form regardless of the response language.\n"
    "7. STRUCTURE teach_text with two sections, in this order, using these exact "
    "Markdown H2 headings (one per line, no extra characters):\n"
    "     ## From Transcript\n"
    "     <3-6 sentences teaching what the speaker SAID in this chunk's time "
    "range. Cite real examples and quotes that appear in the transcript.>\n"
    "     ## From Uploaded Files\n"
    "     <2-4 sentences adding context from the user's selected materials — "
    "definitions, equations, code references, external sources, related concepts. "
    "If no materials are relevant for this chunk, omit this entire heading + its "
    "content.>\n"
    "   The iOS app parses on these two headings and renders each as a separate "
    "card, so the headings are part of the contract, not decoration. Do NOT use "
    "## From Video (same thing as From Transcript — use Transcript). Do NOT use "
    "bullets or other Markdown inside teach_text — keep it plain prose under each "
    "heading so the parser doesn't get confused."
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
  "teach_text":      "## From Transcript\n<3-6 sentences>\n\n## From Uploaded Files\n<2-4 sentences, omit if no materials apply>",
  "check_question":  "<= 30 words, answerable from teach_text alone"
}}]

TEACH_TEXT FORMAT REMINDER (mandatory — iOS parses on these headings):
  teach_text MUST start with "## From Transcript" on the first line, followed
  by one blank line, then the teaching prose. If the user's selected materials
  add relevant context for this chunk, add an empty line, then "## From Uploaded
  Files" on its own line, followed by one blank line, then the materials-sourced
  prose. If no materials are relevant for this chunk, OMIT the "## From Uploaded
  Files" section entirely. Do NOT use "## From Video" — Transcript is the
  canonical name. Do NOT use bullets or other Markdown inside the sections —
  keep it plain prose under each heading so the iOS parser doesn't get confused.
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

    Tolerant against common LLM output quirks:
      - Markdown fences: ```json ... ```, ``` ... ```, with/without
        language tag, sometimes with stray leading/trailing whitespace
      - Prose before/after the JSON array (the LLM occasionally adds
        "Here's the JSON:" or "Here you go:" prefixes)
      - Nested arrays (the LLM occasionally wraps the array in another
        array, e.g. `[[{...}], [{...}]]`)
      - Trailing commas (rare but happens with glm-5.2:cloud)
      - Single-element arrays returned as `{}` (rare)

    Strategy: try multiple parse strategies in order of strictness.
    Fallback to a regex-based chunk-object extraction if structural
    parsing fails — we accept partial results rather than failing.
    """
    text = raw.strip()

    # Strategy 1: direct parse (text already has no fence)
    if arr := _try_parse_array(text):
        return _to_chunks(arr)

    # Strategy 2: strip markdown fences (multiple variants)
    for stripped in _strip_fences(text):
        if arr := _try_parse_array(stripped):
            return _to_chunks(arr)

    # Strategy 3: find first '[' and matching ']' (ignoring prose)
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        if arr := _try_parse_array(candidate):
            return _to_chunks(arr)
        # Try with trailing-comma cleanup
        candidate_clean = _strip_trailing_commas(candidate)
        if arr := _try_parse_array(candidate_clean):
            return _to_chunks(candidate_clean)

    # Strategy 4: regex extract JSON objects one-by-one
    objects = _extract_json_objects(text)
    if objects:
        return _to_chunks(objects)

    # All strategies failed — log the raw response so we can debug
    log.error(
        "pocket.tutor: _parse_chunks failed. Raw response (first 800 chars): %s",
        raw[:800],
    )
    raise ValueError(
        f"Could not parse Ollama response as JSON chunk array. "
        f"Response began with: {raw[:200]!r}"
    )


def _try_parse_array(text: str) -> list | None:
    """Try to parse `text` as a JSON array. Returns None on any failure."""
    try:
        result = json.loads(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if isinstance(result, list):
        # If the LLM returned a nested array, flatten one level
        if len(result) == 1 and isinstance(result[0], list):
            return result[0]
        return result
    return None


def _strip_fences(text: str) -> list[str]:
    """Return variants of `text` with common markdown fences stripped.

    Handles ```json, ```, with/without language tag, and various
    trailing positions. Returns a list because the LLM sometimes
    emits multiple nested fences (e.g. ```json\n```\n[...]\n```\n```).
    """
    variants = [text]
    if not text.startswith("```"):
        return variants

    # Try common patterns: ```json\n...\n```, ```JSON\n...\n```,
    # ```\n...\n```, ```[...]\n``` (no newline after opening fence)
    for prefix in ("```json", "```JSON", "```Json", "```"):
        if text.startswith(prefix):
            rest = text[len(prefix):]
            # Strip leading newline if present
            if rest.startswith("\n"):
                rest = rest[1:]
            # Strip trailing fence
            if rest.endswith("```"):
                rest = rest[:-3]
            variants.append(rest.strip())
            # Also try without the trailing fence stripped (in case the
            # LLM double-fenced and the inner one is the real one)
            if rest.endswith("```\n```"):
                rest = rest[:-7]
                variants.append(rest.strip())
    return variants


def _strip_trailing_commas(text: str) -> str:
    """Remove trailing commas before ] or } — invalid JSON but the LLM
    occasionally emits them in long outputs."""
    # Match comma followed by optional whitespace then ] or }
    import re
    return re.sub(r",(\s*[\]}])", r"\1", text)


def _extract_json_objects(text: str) -> list[dict]:
    """Last-resort: regex out individual JSON objects from the text.

    Used when the LLM wraps chunks in a story ("Here's each chunk:
    { ... } { ... }") instead of a JSON array. We use a brace-counting
    strategy rather than a regex (regex can't handle nested braces).
    """
    out = []
    i = 0
    while i < len(text):
        if text[i] == "{":
            depth = 0
            in_string = False
            escape = False
            start = i
            for j in range(i, len(text)):
                ch = text[j]
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"' and not escape:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : j + 1]
                        try:
                            obj = json.loads(candidate)
                            if isinstance(obj, dict):
                                out.append(obj)
                        except (json.JSONDecodeError, ValueError, TypeError):
                            pass
                        i = j
                        break
        i += 1
    return out


def _to_chunks(items: list) -> list[ChunkOut]:
    """Coerce a list of dict-or-list items into ChunkOut objects.

    Defensive: skips items that can't be coerced (logs a warning) rather
    than failing the whole batch. Usually this happens when the LLM
    mixes dicts with stray strings or lists.
    """
    chunks = []
    for i, item in enumerate(items):
        # If a nested list leaked through, dig one level
        while isinstance(item, list) and len(item) == 1:
            item = item[0]
        if not isinstance(item, dict):
            log.warning(
                "pocket.tutor: skipping non-dict chunk at index %d: %r",
                i, type(item).__name__,
            )
            continue
        try:
            chunks.append(ChunkOut.model_validate(_coerce_chunk(item, i)))
        except Exception as exc:
            log.warning(
                "pocket.tutor: failed to coerce chunk at index %d: %s. "
                "Item: %r", i, exc, item,
            )
    return chunks


def _coerce_chunk(raw: dict[str, Any], index: int) -> dict[str, Any]:
    """Coerce Ollama's loose field names / types to what ChunkOut expects."""
    label = str(raw.get("duration_label", "5min")).strip()
    if label not in ("2min", "5min", "25min"):
        label = "5min"
    teach_text = str(raw.get("teach_text", ""))
    # MVP0.2 followup #3: parse the structured teach_text into two
    # sections (## From Transcript / ## From Uploaded Files). The LLM
    # is now required to emit these headings (see SYSTEM_PROMPT rule 7).
    # Parser is lenient: if the headings are missing for any reason,
    # both fields end up None and the iOS app falls back to the raw
    # `teach_text` blob. Defensive against old chunks still in the DB
    # from before this rule shipped.
    sections = _parse_teach_text_sections(teach_text)
    return {
        "id": raw.get("id") or f"chunk-{index}",
        "video_id": raw.get("video_id", ""),
        "index": int(raw.get("index", index)),
        "start_ts": float(raw.get("start_ts", 0.0)),
        "end_ts": float(raw.get("end_ts", 0.0)),
        "duration_label": label,
        "concept_title": str(raw.get("concept_title", ""))[:255],
        "transcript_quote": str(raw.get("transcript_quote", ""))[:500],
        "teach_text": teach_text,
        "teach_text_transcript": sections["transcript"],
        "teach_text_materials": sections["materials"],
        "check_question": str(raw.get("check_question", "")),
    }


# Canonical heading names — the LLM should emit exactly these. We
# also accept a couple of obvious variants so the parser stays lenient
# against typos (e.g. "## From the Transcript" common in early runs).
_TRANSCRIPT_HEADINGS = (
    "## From Transcript",
    "## From the Transcript",
    "## From Video",          # legacy / common mistranslation
    "## From the Video",
)
_MATERIALS_HEADINGS = (
    "## From Uploaded Files",
    "## From Materials",
    "## From the Uploaded Files",
    "## From the Materials",
)


def _parse_teach_text_sections(teach_text: str) -> dict[str, str | None]:
    """Split a teach_text into two sections by Markdown H2 headings.

    Returns:
        {"transcript": str | None, "materials": str | None}

    Each value is the prose under the matching heading, with leading
    and trailing whitespace stripped. If a heading isn't present, the
    corresponding value is None.

    The parser is line-based: it finds the first line that matches a
    known heading, then collects all subsequent lines until either the
    next known heading or the end of the text. Heading match is
    case-insensitive and ignores leading/trailing whitespace. The two
    known headings are mutually exclusive in unmatched text (text
    before the first heading is dropped — the LLM shouldn't put prose
    there).
    """
    if not teach_text:
        return {"transcript": None, "materials": None}

    lines = teach_text.split("\n")
    # Build a list of (line_index, section_name) for every heading.
    headings: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        lower = stripped.lower()
        if lower in (h.lower() for h in _TRANSCRIPT_HEADINGS):
            headings.append((i, "transcript"))
        elif lower in (h.lower() for h in _MATERIALS_HEADINGS):
            headings.append((i, "materials"))

    if not headings:
        return {"transcript": None, "materials": None}

    result: dict[str, str | None] = {"transcript": None, "materials": None}
    for idx, (line_idx, section_name) in enumerate(headings):
        # Content starts after the heading line; ends at the next
        # heading (or end of text).
        next_heading_idx = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
        content_lines = lines[line_idx + 1: next_heading_idx]
        # Strip the leading blank line if present and trim trailing blanks.
        while content_lines and not content_lines[0].strip():
            content_lines.pop(0)
        while content_lines and not content_lines[-1].strip():
            content_lines.pop()
        content = "\n".join(content_lines).strip()
        if content:
            # First wins: if the LLM emits both ## From Transcript and
            # ## From the Transcript, keep the first one.
            if result[section_name] is None:
                result[section_name] = content

    return result


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
