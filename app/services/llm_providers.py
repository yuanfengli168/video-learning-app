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
from app.services.llm_quota import (
    ollama_quota,
    rate_limiter,
)

logger = logging.getLogger(__name__)


# Internal exception (callers see the structured dict, not this).
class _AllProvidersFailed(Exception):
    """Raised internally when every provider in the chain failed."""

    def __init__(self, attempts: list[dict[str, Any]]):
        self.attempts = attempts
        super().__init__(f"All {len(attempts)} providers failed")


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
            continue

    # Step 5: every provider in chain failed
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