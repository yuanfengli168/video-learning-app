"""Input validation and sanitization helpers.

This module centralizes security-relevant validation so it's applied
consistently across all routers. It addresses three classes of risk
that we identified during the mvp2-production-patches security review:

1. **Prompt injection** (HIGH severity) — user chat messages are sent to an
   LLM. Without length caps or basic sanity filtering, an attacker could:
   - Inject system-prompt-style directives to override the model's behavior
   - Stuff absurdly long messages to burn tokens / OOM the server
   - Send messages with control characters that break terminal logs

   Mitigations:
   - Hard cap on message length (32 KB by, 64 KB by)
   - Strip null bytes / control chars
   - Reject messages that look like prompt-injection attempts (heuristic)
   - Wrap user content in a clear delimiter in the LLM prompt (defense in depth)

2. **Filename injection** (MEDIUM) — uploaded file names are user-controlled
   and used for display + filesystem paths. Without validation:
   - Path traversal (`../../../etc/passwd`) — already prevented by UUID rename,
     but the *display title* (`Path(filename).stem`) can still contain HTML/script
   - XSS via rendered titles in Jinja2 templates (Jinja2 autoescape handles it
     by default, but we belt-and-braces)

   Mitigations:
   - Strip control chars + null bytes from filenames
   - Cap filename length (256 chars by)
   - Reject filenames with non-printable chars

3. **Length DoS** (MEDIUM) — any string field with no cap lets a user
   send 10 MB of strings and lock the process.

   Mitigations:
   - Pydantic-level Field(max_length=...) on every input schema (caller's job)
   - This module provides runtime helpers for non-Pydantic paths

Heuristics are intentionally simple. False positives are acceptable (the
chat just fails) — false negatives are the security risk.
"""
from __future__ import annotations

import re
from typing import Final

# ── Limits ────────────────────────────────────────────────────────────────────
MAX_CHAT_MESSAGE_BYTES: Final[int] = 32 * 1024  # 32 KB per chat message
MAX_TITLE_BYTES: Final[int] = 256
MAX_FILENAME_BYTES: Final[int] = 256
MAX_CONCEPT_BYTES: Final[int] = 200

# ── Compiled patterns (created once, used many times) ────────────────────────
# Match control chars except common whitespace (\t, \n, \r)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Heuristics for prompt-injection attempts. These are NEVER going to be perfect;
# they're a first line of defense.
#
# We look for phrases that try to impersonate system / assistant turns, or that
# try to override the model's instructions. If a tester ever hits a false
# positive, the easy fix is to rephrase.
_INJECTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # "ignore previous instructions" and variants
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|prompts?)", re.I),
    # "you are now ..." impersonation
    re.compile(r"you\s+are\s+now\s+(?:a|an|the|my)", re.I),
    # "system:" or "assistant:" at line start (role impersonation)
    re.compile(r"^[\s>]*system\s*:", re.I | re.M),
    re.compile(r"^[\s>]*assistant\s*:", re.I | re.M),
    # "new instructions:" or "updated prompt:"
    re.compile(r"(?:new|updated)\s+instructions?\s*:", re.I),
    # Direct "act as" / "pretend to be"
    re.compile(r"(?:act|behave)\s+as\s+(?:if\s+you\s+are\s+)?(?:a|an)\s+", re.I),
    # "disregard your" / "forget your"
    re.compile(r"(?:disregard|forget|abandon)\s+your\s+(?:rules|instructions|guidelines)", re.I),
)

# ── Validation result ────────────────────────────────────────────────────────


class ValidationError(ValueError):
    """Raised when user input fails a validation check.

    The chat router / upload router catches this and returns a 400 response
    with the message as the detail.
    """


# ── Public helpers ───────────────────────────────────────────────────────────


def sanitize_text(
    text: str,
    *,
    max_bytes: int,
    field_name: str = "input",
) -> str:
    """Sanitize a free-text string from a user.

    - Strips null bytes + control characters (keeps \\t, \\n, \\r)
    - Enforces a max-bytes length cap (post-strip)
    - Returns the cleaned string

    Raises ValidationError if the input exceeds the cap or is empty after
    stripping.
    """
    if not isinstance(text, str):
        raise ValidationError(f"{field_name}: must be a string")

    # Remove control chars (keep common whitespace)
    cleaned = _CONTROL_CHARS.sub("", text)

    # Length cap — measure as bytes to prevent UTF-8 surprise
    encoded_len = len(cleaned.encode("utf-8"))
    if encoded_len > max_bytes:
        raise ValidationError(
            f"{field_name}: too long ({encoded_len} bytes, max {max_bytes})"
        )

    if not cleaned.strip():
        raise ValidationError(f"{field_name}: cannot be empty or whitespace-only")

    return cleaned


def check_for_prompt_injection(text: str) -> None:
    """Raise ValidationError if the text looks like a prompt-injection attempt.

    Heuristic only. False positives are OK; false negatives are the risk.
    Apply this to user-typed text BEFORE sending to an LLM.
    """
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            raise ValidationError(
                "Message blocked: looks like a prompt-injection attempt. "
                "If you weren't trying to manipulate the model, please rephrase."
            )


def validate_chat_message(content: str) -> str:
    """Validate + sanitize a chat message before it goes to the LLM.

    This is the ONE function to call from chat.py for every user message.
    Combines sanitize_text + check_for_prompt_injection.

    Returns the sanitized content (safe to embed in an LLM prompt).
    """
    cleaned = sanitize_text(
        content,
        max_bytes=MAX_CHAT_MESSAGE_BYTES,
        field_name="message",
    )
    check_for_prompt_injection(cleaned)
    return cleaned


def validate_concept(concept: str) -> str:
    """Validate a chat-session concept (flashcard topic). Shorter than messages."""
    return sanitize_text(
        concept,
        max_bytes=MAX_CONCEPT_BYTES,
        field_name="concept",
    )


def sanitize_filename(filename: str) -> str:
    """Sanitize an uploaded filename for *display* purposes.

    NOTE: This does NOT change where the file is stored on disk — that's
    already a UUID (safe from path traversal). This only cleans the
    string used for DB rows + UI rendering.

    - Strips directory components (`../` and `/`)
    - Strips control chars
    - Caps length
    - Rejects empty results
    """
    if not isinstance(filename, str):
        return "upload"

    # Strip path components (works on POSIX and Windows-style)
    basename = filename.replace("\\", "/").split("/")[-1]

    # Strip control chars
    cleaned = _CONTROL_CHARS.sub("", basename).strip()

    # Length cap (bytes)
    if len(cleaned.encode("utf-8")) > MAX_FILENAME_BYTES:
        # Truncate at a UTF-8 safe boundary
        cleaned = cleaned.encode("utf-8")[:MAX_FILENAME_BYTES].decode("utf-8", errors="ignore")

    if not cleaned:
        return "upload"

    return cleaned


def llm_safe_wrap_user_content(content: str) -> str:
    """Wrap user content in clear delimiters for LLM prompts (defense in depth).

    Even if check_for_prompt_injection misses something, the delimiters make
    it clear to the model which part is user data and which part is the
    actual instruction.

    Example:
        system: "You are a tutor. Answer based on the user's question wrapped
                 in <user_question>...</user_question> tags."
        user:    "<user_question>What is photosynthesis?</user_question>"
    """
    return (
        "<user_question>\n"
        f"{content}\n"
        "</user_question>"
    )


# ── Constants exported for callers ───────────────────────────────────────────

# Quick sanity-check on the limits (so a typo doesn't accidentally set 32 MB)
assert MAX_CHAT_MESSAGE_BYTES <= 64 * 1024, (
    "Chat message cap seems too high — DoS risk"
)
assert MAX_TITLE_BYTES <= 1024
assert MAX_FILENAME_BYTES <= 1024