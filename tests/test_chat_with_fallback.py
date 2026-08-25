"""Tests for chat_with_fallback() — the chat-specific LiteLLM wrapper.

Why a separate test file:
  test_llm_providers.py covers the generic call_llm_with_fallback(). This
  file covers the chat-shaped wrapper:
    - prepends system_prompt correctly
    - returns plain string on success
    - raises ChatCallError(429) with retry_after_seconds on rate-limit
    - raises ChatCallError(503) with attempts list when chain fails
    - never raises raw exceptions from underlying LiteLLM failures

Pattern:
  We patch app.services.llm_providers.call_llm_with_fallback (the function
  our wrapper delegates to) so the tests stay unit-level — no real LLM
  calls, no rate-limiter side effects.
"""

from unittest.mock import patch

import pytest

from app.auth.roles import UserRole
from app.services.llm_providers import (
    ChatCallError,
    chat_with_fallback,
)


# ─────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────


def test_chat_with_fallback_returns_string_on_success():
    """Happy path: assistant text comes back as a plain string."""
    fake_result = {
        "status": "ok",
        "content": "Hi! RAG stands for Retrieval-Augmented Generation.",
        "provider": "groq",
        "model": "groq/compound",
        "attempts": [{"provider": "groq", "model": "groq/compound", "status": "ok"}],
    }
    with patch(
        "app.services.llm_providers.call_llm_with_fallback",
        return_value=fake_result,
    ) as mock_call:
        result = chat_with_fallback(
            messages=[{"role": "user", "content": "What is RAG?"}],
            system_prompt="You are a helpful tutor.",
            user_role=UserRole.FREE,
            user_id="uid-free-x",
        )
    assert result == "Hi! RAG stands for Retrieval-Augmented Generation."
    # Verify delegation: messages had system_prompt prepended, json_mode=False
    _, kwargs = mock_call.call_args
    assert kwargs["json_mode"] is False
    sent_messages = kwargs["messages"]
    assert sent_messages[0] == {"role": "system", "content": "You are a helpful tutor."}
    assert sent_messages[1] == {"role": "user", "content": "What is RAG?"}


def test_chat_with_fallback_omits_system_prompt_when_empty():
    """When system_prompt is empty, no system message is prepended."""
    fake_result = {"status": "ok", "content": "ok", "provider": "groq", "model": "x", "attempts": []}
    with patch(
        "app.services.llm_providers.call_llm_with_fallback",
        return_value=fake_result,
    ) as mock_call:
        chat_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            system_prompt="",
            user_role=UserRole.PAID,
            user_id="uid-paid",
        )
    sent_messages = mock_call.call_args.kwargs["messages"]
    assert sent_messages == [{"role": "user", "content": "hi"}]


def test_chat_with_fallback_passes_user_role_and_id():
    """Rate-limiter + audit log need both user_role and user_id."""
    fake_result = {"status": "ok", "content": "ok", "provider": "groq", "model": "x", "attempts": []}
    with patch(
        "app.services.llm_providers.call_llm_with_fallback",
        return_value=fake_result,
    ) as mock_call:
        chat_with_fallback(
            messages=[],
            user_role=UserRole.PAID,  # 1
            user_id="uid-y",
            video_id="vid-z",
        )
    kwargs = mock_call.call_args.kwargs
    assert kwargs["user_role"] == UserRole.PAID
    assert kwargs["user_id"] == "uid-y"


def test_chat_with_fallback_passes_optional_video_id():
    """user_id is always forwarded (verified in test_passes_user_role_and_id);
    this test just confirms the call doesn't crash when video_id is set."""
    fake_result = {"status": "ok", "content": "ok", "provider": "groq", "model": "x", "attempts": []}
    with patch(
        "app.services.llm_providers.call_llm_with_fallback",
        return_value=fake_result,
    ):
        # No exception = success.
        chat_with_fallback(
            messages=[],
            user_role=UserRole.FREE,
            user_id="uid-z",
            video_id="vid-z",
        )


# ─────────────────────────────────────────────────────────────────────────
# Error paths — must translate to ChatCallError, not raw exceptions
# ─────────────────────────────────────────────────────────────────────────


def test_chat_with_fallback_raises_rate_limited_chat_error():
    """When call_llm_with_fallback returns rate_limited, raise 429."""
    fake_result = {
        "status": "rate_limited",
        "retry_after_seconds": 42,
        "message": "5 per-minute cap hit",
    }
    with patch(
        "app.services.llm_providers.call_llm_with_fallback",
        return_value=fake_result,
    ):
        with pytest.raises(ChatCallError) as excinfo:
            chat_with_fallback(
                messages=[],
                user_role=UserRole.FREE,
                user_id="uid-x",
            )
    assert excinfo.value.status_code == 429
    assert excinfo.value.detail["error"] == "rate_limited"
    assert excinfo.value.detail["retry_after_seconds"] == 42
    assert "5 per-minute cap" in excinfo.value.detail["message"]


def test_chat_with_fallback_raises_provider_unavailable_chat_error():
    """When every provider failed, raise 503 with attempts attached."""
    fake_result = {
        "status": "provider_unavailable",
        "message": "All 1 provider(s) in your tier's chain failed.",
        "attempts": [
            {"provider": "groq", "model": "groq/compound", "status": "failed",
             "error": "NotFoundError: model not found"},
        ],
    }
    with patch(
        "app.services.llm_providers.call_llm_with_fallback",
        return_value=fake_result,
    ):
        with pytest.raises(ChatCallError) as excinfo:
            chat_with_fallback(
                messages=[],
                user_role=UserRole.FREE,
                user_id="uid-x",
            )
    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["error"] == "provider_unavailable"
    assert len(excinfo.value.detail["attempts"]) == 1
    assert excinfo.value.detail["attempts"][0]["provider"] == "groq"


def test_chat_with_fallback_unknown_status_treated_as_unavailable():
    """Defensive: if the wrapper gets an unrecognized status, treat it as
    provider_unavailable (503) rather than crashing. This protects against
    future status strings added to call_llm_with_fallback that this wrapper
    doesn't yet know about."""
    with patch(
        "app.services.llm_providers.call_llm_with_fallback",
        return_value={"status": "WAT", "content": "???"},
    ):
        with pytest.raises(ChatCallError) as excinfo:
            chat_with_fallback(
                messages=[],
                user_role=UserRole.FREE,
                user_id="uid-x",
            )
    assert excinfo.value.status_code == 503
