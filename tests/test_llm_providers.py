"""Tests for app/services/llm_providers.py — the fallback wrapper.

Patches `litellm.completion` (the single function the wrapper calls)
to return canned responses or raise canned exceptions. Tests verify
the wrapper's chain / fallback / rate-limit / quota logic without
any real API I/O.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.services import llm_quota
from app.services.llm_providers import call_llm_with_fallback


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_state():
    """Each test starts with empty state + restore env vars on exit."""
    saved_env = {}
    for k in ("OPENAI_API_KEY", "GROQ_API_KEY", "OLLAMA_API_KEY"):
        if k in os.environ:
            saved_env[k] = os.environ[k]
            del os.environ[k]
    llm_quota.rate_limiter.reset_all()
    llm_quota.ollama_quota.reset()
    yield
    # Restore env
    for k in ("OPENAI_API_KEY", "GROQ_API_KEY", "OLLAMA_API_KEY"):
        if k in os.environ:
            del os.environ[k]
    for k, v in saved_env.items():
        os.environ[k] = v
    llm_quota.rate_limiter.reset_all()
    llm_quota.ollama_quota.reset()


def _mock_response(content: str = '{"answer": 42}') -> MagicMock:
    """Build a mock that looks like litellm's ModelResponse."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = content
    return mock_resp


# ─────────────────────────────────────────────────────────────────────────
# Happy path: single provider success
# ─────────────────────────────────────────────────────────────────────────


def test_free_tier_uses_groq_only():
    """FREE chain = [groq]. First provider tried must be groq."""
    os.environ["GROQ_API_KEY"] = "test-key"
    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_response('{"ok": true}'),
    ) as mock:
        result = call_llm_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            user_role=2,  # FREE
            user_id="uid-free",
        )
    assert result["status"] == "ok"
    assert result["provider"] == "groq"
    # Confirm we called litellm.completion with provider=groq
    call_args = mock.call_args
    assert "groq/" in call_args.kwargs["model"]


def test_paid_tier_tries_ollama_first():
    """PAID chain = [ollama, openai]. First try is ollama."""
    os.environ["OLLAMA_API_KEY"] = "test-ollama"
    os.environ["OPENAI_API_KEY"] = "test-openai"
    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_response(),
    ) as mock:
        result = call_llm_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            user_role=1,  # PAID
            user_id="uid-paid",
        )
    assert result["status"] == "ok"
    assert result["provider"] == "ollama"
    assert "ollama/" in mock.call_args.kwargs["model"]


# ─────────────────────────────────────────────────────────────────────────
# Fallback chain
# ─────────────────────────────────────────────────────────────────────────


def test_paid_tier_falls_back_to_openai_when_ollama_fails():
    """If ollama raises, paid chain falls back to openai."""
    os.environ["OLLAMA_API_KEY"] = "test-ollama"
    os.environ["OPENAI_API_KEY"] = "test-openai"

    call_count = {"ollama": 0, "openai": 0}

    def side_effect(*args, **kwargs):
        model = kwargs.get("model", "")
        if model.startswith("ollama/"):
            call_count["ollama"] += 1
            raise RuntimeError("ollama unavailable")
        elif model.startswith("openai/"):
            call_count["openai"] += 1
            return _mock_response('{"via": "openai"}')
        raise AssertionError(f"unexpected model {model!r}")

    with patch(
        "app.services.llm_providers.litellm.completion",
        side_effect=side_effect,
    ):
        result = call_llm_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            user_role=1,
            user_id="uid-paid",
        )

    assert result["status"] == "ok"
    assert result["provider"] == "openai"
    assert call_count == {"ollama": 1, "openai": 1}
    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["provider"] == "ollama"
    assert result["attempts"][0]["status"] == "failed"
    assert result["attempts"][1]["provider"] == "openai"
    assert result["attempts"][1]["status"] == "ok"


def test_free_tier_does_not_fall_back_to_ollama_or_openai():
    """FREE chain = [groq] only. If groq fails, no fallback."""
    os.environ["GROQ_API_KEY"] = "test-key"

    with patch(
        "app.services.llm_providers.litellm.completion",
        side_effect=RuntimeError("groq down"),
    ):
        result = call_llm_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            user_role=2,
            user_id="uid-free",
        )

    assert result["status"] == "provider_unavailable"
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["provider"] == "groq"


def test_all_providers_failed_returns_structured_dict():
    """Both ollama and openai fail → structured provider_unavailable."""
    os.environ["OLLAMA_API_KEY"] = "test-ollama"
    os.environ["OPENAI_API_KEY"] = "test-openai"

    with patch(
        "app.services.llm_providers.litellm.completion",
        side_effect=RuntimeError("network down"),
    ):
        result = call_llm_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            user_role=1,
            user_id="uid-paid",
        )

    assert result["status"] == "provider_unavailable"
    assert "All 2 provider(s)" in result["message"]
    assert len(result["attempts"]) == 2
    # Both attempts show 'error
    for attempt in result["attempts"]:
        assert attempt["status"] == "failed"


# ─────────────────────────────────────────────────────────────────────────
# Rate limit interception
# ─────────────────────────────────────────────────────────────────────────


def test_per_user_rate_limit_blocks_before_provider_call():
    """FREE tier (5/min): 6th call returns rate_limited WITHOUT calling provider."""
    os.environ["GROQ_API_KEY"] = "test-key"

    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_response(),
    ) as mock:
        for _ in range(5):
            r = call_llm_with_fallback(
                messages=[{"role": "user", "content": "x"}],
                user_role=2,
                user_id="uid-flooding",
            )
            assert r["status"] == "ok"
        # 6th call: should be blocked
        r = call_llm_with_fallback(
            messages=[{"role": "user", "content": "x"}],
            user_role=2,
            user_id="uid-flooding",
        )

    assert r["status"] == "rate_limited"
    assert r["retry_after_seconds"] > 0
    # Critical: provider was NOT called for the rejected attempt
    assert mock.call_count == 5  # 5 successful + 0 rejected


def test_rate_limit_message_includes_user_friendly_text():
    """The reason text is suitable to show the user."""
    os.environ["GROQ_API_KEY"] = "test-key"

    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_response(),
    ):
        for _ in range(5):
            call_llm_with_fallback(
                messages=[{"role": "user", "content": "x"}],
                user_role=2,
                user_id="uid",
            )
        r = call_llm_with_fallback(
            messages=[{"role": "user", "content": "x"}],
            user_role=2,
            user_id="uid",
        )

    assert "limit reached" in r["message"].lower()
    assert "5/5" in r["message"]


# ─────────────────────────────────────────────────────────────────────────
# Ollama quota integration
# ─────────────────────────────────────────────────────────────────────────


def test_ollama_near_cap_skipped_in_paid_chain():
    """PAID chain starts with ollama, but if quota says near_cap, skip to openai."""
    os.environ["OLLAMA_API_KEY"] = "test-ollama"
    os.environ["OPENAI_API_KEY"] = "test-openai"

    # Pre-fill ollama quota tracker to near-cap
    for _ in range(720):  # 90% of 800 (5h cap)
        llm_quota.ollama_quota.record_call()

    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_response(),
    ) as mock:
        result = call_llm_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            user_role=1,  # PAID
            user_id="uid",
        )

    assert result["status"] == "ok"
    # Should have skipped ollama
    assert result["provider"] == "openai"
    # The mock should only have been called once (for openai), not for ollama
    call_models = [c.kwargs["model"] for c in mock.call_args_list]
    assert all(m.startswith("openai/") for m in call_models)


def test_ollama_near_cap_with_no_fallback_returns_warning():
    """If ollama is near_cap AND chain has no other provider → warning."""
    # Patch settings to make a chain that's only ollama
    original_chain = settings.llm_provider_chain_paid
    settings.llm_provider_chain_paid = "ollama"

    os.environ["OLLAMA_API_KEY"] = "test-ollama"

    for _ in range(720):
        llm_quota.ollama_quota.record_call()

    try:
        with patch(
            "app.services.llm_providers.litellm.completion",
            return_value=_mock_response(),
        ) as mock:
            result = call_llm_with_fallback(
                messages=[{"role": "user", "content": "hi"}],
                user_role=1,
                user_id="uid",
            )
    finally:
        settings.llm_provider_chain_paid = original_chain

    assert result["status"] == "provider_unavailable"
    assert "near cap" in result["message"].lower()
    # Provider was NOT called
    assert mock.call_count == 0


def test_successful_ollama_call_records_in_quota_tracker():
    """After an ollama success, ollama_quota should have +1 call."""
    os.environ["OLLAMA_API_KEY"] = "test-ollama"

    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_response(),
    ):
        call_llm_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            user_role=0,  # ADMIN — uses ollama
            user_id="uid",
        )

    usage = llm_quota.ollama_quota.current_usage()
    assert usage["calls_5h"] == 1
    assert usage["calls_week"] == 1


def test_failed_ollama_call_does_not_record_in_quota():
    """If ollama raises, don't count it in the quota tracker."""
    os.environ["OLLAMA_API_KEY"] = "test-ollama"
    os.environ["OPENAI_API_KEY"] = "test-openai"

    def side_effect(*args, **kwargs):
        if kwargs["model"].startswith("ollama/"):
            raise RuntimeError("boom")
        return _mock_response()

    with patch(
        "app.services.llm_providers.litellm.completion",
        side_effect=side_effect,
    ):
        call_llm_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            user_role=1,
            user_id="uid",
        )

    usage = llm_quota.ollama_quota.current_usage()
    assert usage["calls_5h"] == 0  # failed ollama didn't count


# ─────────────────────────────────────────────────────────────────────────
# API key filtering
# ─────────────────────────────────────────────────────────────────────────


def test_provider_with_no_api_key_is_filtered_from_chain():
    """If GROQ_API_KEY isn't set, groq is filtered out before trying."""
    # Don't set GROQ_API_KEY. FREE chain = [groq] only → after filter,
    # chain is empty → return provider_unavailable without calling.
    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_response(),
    ) as mock:
        result = call_llm_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            user_role=2,
            user_id="uid",
        )

    assert result["status"] == "provider_unavailable"
    assert "No providers" in result["message"]
    assert mock.call_count == 0  # never tried


def test_paid_chain_with_ollama_key_only_falls_back_correctly():
    """PAID chain with only OLLAMA_API_KEY set: openai is filtered out,
    but ollama still works."""
    os.environ["OLLAMA_API_KEY"] = "test-ollama"
    # No OPENAI_API_KEY

    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_response(),
    ) as mock:
        result = call_llm_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            user_role=1,
            user_id="uid",
        )

    assert result["status"] == "ok"
    assert result["provider"] == "ollama"
    # Only one provider call (no fallback because openai was filtered)
    assert mock.call_count == 1