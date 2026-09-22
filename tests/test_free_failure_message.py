"""FREE-tier friendly failure message tests (2026-09-22).

Owner report: FREE users intermittently saw "All 1 provider(s) in
your tier's chain failed. See 'attempts' for details" — Groq's free
tier fails at capacity sometimes, works other times. The technical
text read like a bug report and gave nothing actionable.

The ratified contract (discriminator = ROLE, the product contract —
NOT len(chain), which sponsor scenarios would break):

  FREE     → friendly retry + the YouTube-Ask workaround
  PAID     → unchanged technical message (2-provider failure is a
             real outage worth the debug path)
  ADMIN    → unchanged technical message
  unknown/missing role → technical message (fail SAFE: never show
             free-tier wording to an unconfirmed tier)
"""

import os
from unittest.mock import patch

from app.services.llm_providers import call_llm_with_fallback


def _fail_all_providers(user_role: int, user_id: str = "uid-x"):
    """Call call_llm_with_fallback with every provider mocked to fail."""
    with patch(
        "app.services.llm_providers.litellm.completion",
        side_effect=RuntimeError("simulated outage"),
    ):
        return call_llm_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            user_role=user_role,
            user_id=user_id,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 1. FREE gets the friendly message
# ─────────────────────────────────────────────────────────────────────────────

def test_free_failure_message_is_friendly():
    """FREE (role=2): the message explains the intermittency, asks for
    a retry, and offers the YouTube-Ask workaround."""
    os.environ["GROQ_API_KEY"] = "test-key"
    result = _fail_all_providers(user_role=2, user_id="uid-free")

    assert result["status"] == "provider_unavailable"
    msg = result["message"]
    # No technical jargon
    assert "provider(s)" not in msg
    assert "attempts" not in msg
    # The three content pieces (owner-ratified wording)
    assert "The free AI service is temporarily unavailable" in msg
    assert "intermittent" in msg
    assert "try again in a minute" in msg
    assert '"Ask"' in msg          # YouTube's feature, quoted name
    assert "logged on the source page" in msg


def test_free_failure_keeps_attempts_debug_data():
    """The friendly message is display copy — the structured 'attempts'
    debug data still travels in the payload for the admin/debug path."""
    os.environ["GROQ_API_KEY"] = "test-key"
    result = _fail_all_providers(user_role=2, user_id="uid-free2")

    assert result["status"] == "provider_unavailable"
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["provider"] == "groq"
    assert result["attempts"][0]["status"] == "failed"


# ─────────────────────────────────────────────────────────────────────────────
# 2. PAID / ADMIN unchanged (pinned so it can never drift)
# ─────────────────────────────────────────────────────────────────────────────

def test_paid_failure_message_unchanged():
    """PAID (role=1): the technical message stays EXACTLY as it was —
    the 'don't change anything for paid' owner instruction, pinned."""
    os.environ["OLLAMA_API_KEY"] = "test-ollama"
    os.environ["OPENAI_API_KEY"] = "test-openai"
    result = _fail_all_providers(user_role=1, user_id="uid-paid")

    assert result["status"] == "provider_unavailable"
    assert result["message"] == (
        "All 2 provider(s) in your tier's chain failed. "
        "See 'attempts' for details."
    )
    # And never the friendly wording
    assert "free AI service" not in result["message"]


def test_admin_failure_message_unchanged():
    """ADMIN (role=0): same technical message as PAID."""
    os.environ["OLLAMA_API_KEY"] = "test-ollama"
    os.environ["OPENAI_API_KEY"] = "test-openai"
    result = _fail_all_providers(user_role=0, user_id="uid-admin")

    assert result["status"] == "provider_unavailable"
    assert result["message"] == (
        "All 2 provider(s) in your tier's chain failed. "
        "See 'attempts' for details."
    )
    assert "free AI service" not in result["message"]


def test_paid_never_sees_free_wording_even_if_chain_len_is_1():
    """The discriminator must be ROLE, not chain length: a PAID user
    whose chain is temporarily down to ONE provider (e.g. ollama
    skipped near cap → openai-only… mocked here to fail) still gets
    the technical message, never 'The free AI service…'."""
    # PAID chain normally = [ollama, openai]; simulate ollama being
    # near cap so the chain shrinks to [openai], then openai fails.
    os.environ["OPENAI_API_KEY"] = "test-openai"

    from app.services import llm_providers as lp
    with patch.object(
        lp.ollama_quota, "is_near_cap", return_value=True,
    ):
        with patch(
            "app.services.llm_providers.litellm.completion",
            side_effect=RuntimeError("simulated outage"),
        ):
            result = call_llm_with_fallback(
                messages=[{"role": "user", "content": "hi"}],
                user_role=1,
                user_id="uid-paid-shrunk",
            )

    assert result["status"] == "provider_unavailable"
    assert "free AI service" not in result["message"]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Fail-safe: unknown role → technical message
# ─────────────────────────────────────────────────────────────────────────────

def test_unknown_role_fails_safe_to_technical():
    """A role value that isn't FREE (e.g. a future role 3, or a weird
    sentinel) must NEVER get the free-tier friendly wording — fail
    safe to the technical message."""
    os.environ["GROQ_API_KEY"] = "test-key"
    result = _fail_all_providers(user_role=99, user_id="uid-future-role")

    assert result["status"] == "provider_unavailable"
    assert "free AI service" not in result["message"]
    assert "provider(s)" in result["message"]