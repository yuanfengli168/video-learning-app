"""LLM provider fallback wrapper (Day 4, Commit 4).

Single entry point for all LLM calls in the app. Decides:
  1. Which provider chain to use based on user role (tier-based)
  2. Whether to skip Ollama (quota tracker near-cap)
  3. Calls each provider in order until one succeeds
  4. Records each successful Ollama call in the quota tracker

This wrapper is NOT yet wired into the existing app/services/llm.py
call site (that's Commit 5). It exists standalone so we can unit-test
the fallback logic without touching the live generate_materials()
flow.

Public API
----------
call_llm_with_fallback(messages, *, user_role, user_id, json_mode=True)
    Make an LLM call with tier-based provider fallback.

    Returns one of:
      {"status": "ok", "content": ..., "provider": "ollama", ...}
      {"status": "rate_limited", "retry_after_seconds": 60,
       "message": "..."}  # per-user rate limit hit, before any provider call
      {"status": "provider_unavailable",
       "message": "All providers in chain failed.",
       "attempts": [...]}  # every provider raised

    Caller never gets a Python exception (errors become structured dicts).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import litellm

from app.config import settings
from app.database import SessionLocal
from app.services.llm_quota import (
    ollama_quota,
    rate_limiter,
)
from app.utils.events import log_event

logger = logging.getLogger(__name__)


# Internal exception (callers see the structured dict, not this).
class _AllProvidersFailed(Exception):
    """Raised internally when every provider in the chain failed."""

    def __init__(self, attempts: list[dict[str, Any]]):
        self.attempts = attempts
        super().__init__(f"All {len(attempts)} providers failed")


def _audit(level: str, message: str, *, user_id: str | None = None,
           video_id: str | None = None, context: dict[str, Any] | None = None) -> None:
    """Short-lived audit-log write. Opens its own session so we don't have to
    thread `db` through every call site. log_event() never raises, so this
    is safe to call from any hot path."""
    db = SessionLocal()
    try:
        log_event(
            db, level=level, source="services.llm_providers",
            message=message, user_id=user_id, video_id=video_id,
            context=context,
        )
        db.commit()
    except Exception as exc:
        logger.warning("audit log failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def call_llm_with_fallback(
    messages: list[dict[str, Any]],
    *,
    user_role: int,
    user_id: str,
    json_mode: bool = True,
) -> dict[str, Any]:
    """Call an LLM with provider fallback. See module docstring.

    Args:
        messages: OpenAI-style chat messages
            e.g. [{"role": "system", "content": "..."},
                  {"role": "user", "content": "transcript..."}]
        user_role: UserRole enum value (0=ADMIN, 1=PAID, 2=FREE).
            Used to pick the provider chain + rate limit thresholds.
        user_id: Firebase uid (for rate limiter).
        json_mode: If True, asks the model to return JSON. Most
            providers honor this (Groq, OpenAI, Ollama with
            response_format).

    Returns:
        Dict with one of the shapes documented in the module docstring.
        Never raises (defense in depth; caller doesn't have to
        try/except).

    Side effects:
        - Records the call in rate_limiter (counts toward user's quota)
        - Records successful Ollama calls in ollama_quota
    """
    # Step 1: per-user rate limit check (applies to all providers)
    rl = rate_limiter.check_and_record(user_id, user_role)
    if not rl.allowed:
        logger.warning(
            "Rate limit hit for user %s (role=%d): %s",
            user_id, user_role, rl.reason,
        )
        _audit("WARNING", f"rate limit hit for user {user_id}",
               user_id=user_id, context={
                   "role": user_role,
                   "reason": rl.reason,
                   "retry_after_seconds": rl.retry_after_seconds,
               })
        return {
            "status": "rate_limited",
            "retry_after_seconds": rl.retry_after_seconds,
            "message": rl.reason,
        }

    # Step 2: figure out which providers to try
    chain = settings.get_provider_chain(user_role)
    # Filter out providers whose API key isn't set (so we don't even
    # try them and waste a 401). Keys come from environment via LiteLLM
    # conventions.
    chain = [p for p in chain if _provider_key_available(p)]
    if not chain:
        return {
            "status": "provider_unavailable",
            "message": (
                f"No providers in your tier's chain have API keys "
                f"configured. Chain was {settings.get_provider_chain(user_role)}. "
                f"Check OPENAI_API_KEY / GROQ_API_KEY / OLLAMA_API_KEY in .env."
            ),
            "attempts": [],
        }

    # Step 3: skip Ollama if near cap
    if "ollama" in chain and ollama_quota.is_near_cap():
        logger.warning(
            "Ollama near cap (5h or weekly >= 90%%). Skipping to next provider."
        )
        _audit("WARNING", "Ollama near cap, skipping in chain",
               user_id=user_id, context={
                   "remaining_chain": chain,
                   "ollama_5h": ollama_quota.current_usage().get("calls_5h"),
                   "ollama_week": ollama_quota.current_usage().get("calls_week"),
               })
        chain = [p for p in chain if p != "ollama"]
        if not chain:
            return {
                "status": "provider_unavailable",
                "message": (
                    "Ollama quota near cap and no fallback provider configured. "
                    "Try again later or add OpenAI key as fallback."
                ),
                "attempts": [],
            }

    # Step 4: try each provider in order
    attempts: list[dict[str, Any]] = []
    for provider in chain:
        model = settings.get_model_for_provider(provider)
        attempt: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "status": "pending",
        }
        try:
            response = litellm.completion(
                model=f"{provider}/{model}",
                messages=messages,
                response_format={"type": "json_object"} if json_mode else None,
                # Don't retry; let the caller decide. We want fast
                # fallback to next provider, not long waits.
                num_retries=0,
                timeout=60,
            )
            content = response.choices[0].message.content
            # On Ollama success, record the call in the quota tracker.
            if provider == "ollama":
                ollama_quota.record_call()
            attempt["status"] = "ok"
            attempts.append(attempt)
            logger.info(
                "LLM call succeeded via %s/%s for user %s",
                provider, model, user_id,
            )
            _audit("INFO", f"LLM call succeeded via {provider}/{model}",
                   user_id=user_id, context={
                       "provider": provider,
                       "model": model,
                       "role": user_role,
                       "json_mode": json_mode,
                       "attempts_before_success": len(attempts),
                   })
            return {
                "status": "ok",
                "content": content,
                "provider": provider,
                "model": model,
                "attempts": attempts,
            }
        except Exception as exc:
            attempt["status"] = "failed"
            attempt["error"] = _summarize_exception(exc)
            attempts.append(attempt)
            logger.warning(
                "LLM call failed on %s/%s for user %s: %s",
                provider, model, user_id, attempt["error"],
            )
            _audit("WARNING", f"LLM call failed on {provider}/{model}",
                   user_id=user_id, context={
                       "provider": provider,
                       "model": model,
                       "role": user_role,
                       "error": attempt["error"],
                   })
            continue

    # Step 5: every provider in chain failed
    _audit("ERROR", f"all {len(attempts)} provider(s) failed",
           user_id=user_id, context={
               "role": user_role,
               "attempts": attempts,
           })
    return {
        "status": "provider_unavailable",
        "message": (
            f"All {len(attempts)} provider(s) in your tier's chain failed. "
            f"See 'attempts' for details."
        ),
        "attempts": attempts,
    }


def _provider_key_available(provider: str) -> bool:
    """Check if the API key env var is set for this provider.

    Uses LiteLLM's naming convention (uppercase, with suffix).
    Returns True for 'ollama' since it's a local service with no key
    requirement (the OLLAMA_API_KEY check is for Ollama Cloud, which
    has it; if not set, Ollama is still callable for local instances).
    """
    env_keys = {
        "openai": "OPENAI_API_KEY",
        "groq": "GROQ_API_KEY",
        "ollama": "OLLAMA_API_KEY",  # optional; only required for cloud
    }
    key_name = env_keys.get(provider)
    if key_name is None:
        # Unknown provider; assume available; let LiteLLM error out
        # with a clearer message.
        return True
    return bool(os.environ.get(key_name) or os.environ.get(key_name.lower()))


def _summarize_exception(exc: Exception) -> str:
    """Truncate long exception messages (esp. nested HTTPError chains).

    LiteLLM exceptions can be 5+ levels deep with full request/response
    dumps. The admin doesn't need all that; just the cause.
    """
    msg = str(exc).strip()
    if len(msg) > 200:
        msg = msg[:200] + "..."
    return f"{type(exc).__name__}: {msg}"

# ─────────────────────────────────────────────────────────────────────────
# Chat wrapper (Day 5 hotfix)
# ─────────────────────────────────────────────────────────────────────────
#
# Why this exists:
#   Pre-Day-5, app/routers/chat.py::send_message called chat_with_ollama()
#   directly, bypassing the LiteLLM wrapper. That meant:
#     - Free users hit Ollama (regardless of tier chain)
#     - Chat had no rate limit
#     - Chat had no audit log
#     - Chat had no provider fallback
#     - Chat broke entirely when Ollama was down (no free fallback)
#
#   This wrapper routes chat through call_llm_with_fallback(), so:
#     - FREE users go through Groq (matches the rest of the app)
#     - PAID/ADMIN users go through Ollama (then OpenAI if near cap)
#     - Rate limits + audit log + fallback now apply uniformly
#
# Return type: str (the assistant's reply text).
# On failure: raises ChatCallError with structured .detail (matches the
# pattern in app/services/llm.py::LlmCallError so the router can map it
# to an HTTPException with a sensible status code).


class ChatCallError(Exception):
    """Raised by chat_with_fallback on rate-limit or provider failure.

    Attributes:
        status_code: HTTP-style code (429 for rate-limit, 503 for unavailable)
        detail: dict suitable for HTTPException(detail=...)
    """

    def __init__(self, status_code: int, detail: dict[str, Any]):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"chat failed ({status_code}): {detail}")


def chat_with_fallback(
    messages: list[dict[str, str]],
    system_prompt: str = "",
    *,
    user_role: int,
    user_id: str,
    video_id: str | None = None,
) -> str:
    """Send a chat request through the tier-aware provider chain.

    Args:
        messages: List of {role, content} dicts (user/assistant history).
        system_prompt: System prompt prepended to the conversation.
        user_role: UserRole enum value (0=ADMIN, 1=PAID, 2=FREE).
            Picks the provider chain + rate limit thresholds.
        user_id: Firebase uid (for rate limiter + audit log).
        video_id: Optional video_id for audit log linkage.

    Returns:
        The assistant's response text (string).

    Raises:
        ChatCallError 429: User is rate-limited (per-minute or per-day cap).
        ChatCallError 503: All providers in the chain failed.
    """
    full_messages: list[dict[str, Any]] = []
    if system_prompt:
        full_messages.append({"role": "system", "content": system_prompt})
    full_messages.extend(messages)

    result = call_llm_with_fallback(
        messages=full_messages,
        user_role=user_role,
        user_id=user_id,
        json_mode=False,  # chat is free-form prose, not JSON
    )

    if result["status"] == "ok":
        return result["content"]

    if result["status"] == "rate_limited":
        raise ChatCallError(
            status_code=429,
            detail={
                "error": "rate_limited",
                "message": result["message"],
                "retry_after_seconds": result["retry_after_seconds"],
            },
        )

    # provider_unavailable — every provider in chain failed,
    # OR an unrecognized status string from a future version of
    # call_llm_with_fallback (defensive: don't crash on unknown keys).
    raise ChatCallError(
        status_code=503,
        detail={
            "error": "provider_unavailable",
            "message": result.get(
                "message", f"Unrecognized status from LLM wrapper: {result.get('status')!r}"
            ),
            "attempts": result.get("attempts", []),
        },
    )
