"""Tests for app/services/llm_quota.py — rate limiter + Ollama quota tracker.

Both modules are pure in-memory state machines. Tests use monkeypatch on
time.time() to simulate window passage without sleeping.
"""

import time
from unittest.mock import patch

import pytest

from app.services.llm_quota import (
    LlmRateLimiter,
    OllamaQuotaTracker,
    ollama_quota,
    rate_limiter,
)


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_state():
    """Each test starts with empty state."""
    rate_limiter.reset_all()
    ollama_quota.reset()
    yield
    rate_limiter.reset_all()
    ollama_quota.reset()


# ─────────────────────────────────────────────────────────────────────────
# LlmRateLimiter — happy path
# ─────────────────────────────────────────────────────────────────────────


def test_rate_limiter_allows_first_call():
    """First call from any user is always allowed."""
    result = rate_limiter.check_and_record("uid-A", role=2)
    assert result.allowed
    assert result.retry_after_seconds == 0
    assert result.reason == ""


def test_rate_limiter_counts_calls():
    """After 3 calls, usage shows 3."""
    for _ in range(3):
        rate_limiter.check_and_record("uid-A", role=2)
    usage = rate_limiter.current_usage("uid-A", role=2)
    assert usage["calls_in_last_min"] == 3
    assert usage["calls_today"] == 3
    assert usage["limit_per_min"] == 5   # FREE
    assert usage["limit_per_day"] == 15  # FREE (Day 5 hotfix: was 30, lowered so 10 users × 15 < Groq's 250/day cap)


# ─────────────────────────────────────────────────────────────────────────
# LlmRateLimiter — per-minute cap
# ─────────────────────────────────────────────────────────────────────────


def test_rate_limiter_blocks_after_minute_cap_reached():
    """FREE tier (5/min): 6th call in same minute → blocked."""
    for _ in range(5):
        assert rate_limiter.check_and_record("uid-A", role=2).allowed
    result = rate_limiter.check_and_record("uid-A", role=2)
    assert not result.allowed
    assert result.retry_after_seconds > 0
    assert "limit reached" in result.reason.lower()
    assert "5/5" in result.reason


def test_rate_limiter_does_not_record_rejected_call():
    """A rejected call doesn't push us further over the limit."""
    for _ in range(5):
        rate_limiter.check_and_record("uid-A", role=2)
    rate_limiter.check_and_record("uid-A", role=2)  # rejected
    rate_limiter.check_and_record("uid-A", role=2)  # also rejected
    usage = rate_limiter.current_usage("uid-A", role=2)
    # Should still show 5, not 7
    assert usage["calls_in_last_min"] == 5


def test_rate_limiter_paid_tier_has_higher_minute_cap():
    """PAID (15/min): 14 calls OK; 15th OK; 16th blocked."""
    for _ in range(15):
        assert rate_limiter.check_and_record("uid-paid", role=1).allowed
    result = rate_limiter.check_and_record("uid-paid", role=1)
    assert not result.allowed
    assert "15/15" in result.reason


def test_rate_limiter_admin_tier_most_permissive():
    """ADMIN (60/min): 60 calls OK."""
    for _ in range(60):
        assert rate_limiter.check_and_record("uid-admin", role=0).allowed


# ─────────────────────────────────────────────────────────────────────────
# LlmRateLimiter — window reset
# ─────────────────────────────────────────────────────────────────────────


def test_rate_limiter_resets_minute_window_after_60s():
    """After 60s, the per-minute window resets (old timestamps fall off)."""
    with patch("app.services.llm_quota.time.time") as mock_time:
        mock_time.return_value = 1000.0
        for _ in range(5):
            assert rate_limiter.check_and_record("uid-A", role=2).allowed
        # 6th call in the same minute → blocked
        assert not rate_limiter.check_and_record("uid-A", role=2).allowed

        # Jump forward 61 seconds
        mock_time.return_value = 1061.0
        # Now allowed again
        assert rate_limiter.check_and_record("uid-A", role=2).allowed


def test_rate_limiter_resets_day_window_after_24h():
    """After 24h, the per-day window resets.

    Uses ADMIN tier (60/min, 1000/day) so we don't trip the per-minute
    cap before reaching the per-day cap.
    """
    with patch("app.services.llm_quota.time.time") as mock_time:
        mock_time.return_value = 1000.0
        # ADMIN = 1000/day. Make 1000 calls — well under per-min (60/min
        # would limit us if we tried >60 in the same minute).
        # Spread the 1000 calls over 100 minutes to avoid the per-minute cap.
        for i in range(1000):
            mock_time.return_value = 1000.0 + i * 6  # 6s apart, max 10/min
            assert rate_limiter.check_and_record("uid-A", role=0).allowed
        # Now we're at 1000/day, 1000/1000 → 1001st blocked
        mock_time.return_value = 1000.0 + 1000 * 6 + 1
        assert not rate_limiter.check_and_record("uid-A", role=0).allowed

        # Jump forward 24h + 1s
        mock_time.return_value = 1000.0 + 86401
        # Allowed again
        assert rate_limiter.check_and_record("uid-A", role=0).allowed


# ─────────────────────────────────────────────────────────────────────────
# LlmRateLimiter — isolation
# ─────────────────────────────────────────────────────────────────────────


def test_rate_limiter_isolates_users():
    """User A hitting limit doesn't affect User B."""
    for _ in range(5):
        rate_limiter.check_and_record("uid-A", role=2)
    assert not rate_limiter.check_and_record("uid-A", role=2).allowed

    # User B is unaffected
    assert rate_limiter.check_and_record("uid-B", role=2).allowed


def test_rate_limiter_reset_user():
    """reset_user(uid) clears that one user's state."""
    for _ in range(5):
        rate_limiter.check_and_record("uid-A", role=2)
    rate_limiter.reset_user("uid-A")
    assert rate_limiter.check_and_record("uid-A", role=2).allowed


# ─────────────────────────────────────────────────────────────────────────
# OllamaQuotaTracker
# ─────────────────────────────────────────────────────────────────────────


def test_ollama_quota_starts_below_cap():
    """Brand-new tracker: not near cap."""
    assert not ollama_quota.is_near_cap()
    usage = ollama_quota.current_usage()
    assert usage["calls_5h"] == 0
    assert usage["calls_week"] == 0
    assert usage["near_cap"] is False


def test_ollama_quota_record_call_increments_counters():
    """record_call() bumps both counters."""
    for _ in range(3):
        ollama_quota.record_call()
    usage = ollama_quota.current_usage()
    assert usage["calls_5h"] == 3
    assert usage["calls_week"] == 3


def test_ollama_quota_near_cap_when_5h_window_at_90_percent():
    """When 5h window hits 90% (720 calls), near_cap=True."""
    with patch("app.services.llm_quota.time.time") as mock_time:
        mock_time.return_value = 10000.0
        # Default limit 5h = 800, alert_pct = 0.9 → 720 triggers alert
        for _ in range(720):
            ollama_quota.record_call()
        assert ollama_quota.is_near_cap()


def test_ollama_quota_near_cap_when_weekly_window_at_90_percent():
    """When weekly window hits 90% (2700 calls), near_cap=True.

    Even if the 5h window is well below 800.
    """
    with patch("app.services.llm_quota.time.time") as mock_time:
        # Spread 2700 calls over 6 days, each in its own 5h slot
        # (spread more than 5h apart so they don't pile up in 5h)
        for i in range(2700):
            mock_time.return_value = 10000.0 + i * 200  # 200s apart = ~3.3 min
            ollama_quota.record_call()
        assert ollama_quota.is_near_cap()


def test_ollama_quota_not_near_cap_below_threshold():
    """At 50% of limits, not near cap."""
    for _ in range(400):  # half of 800
        ollama_quota.record_call()
    assert not ollama_quota.is_near_cap()


def test_ollama_quota_drops_old_timestamps_outside_7_days():
    """Calls older than 7 days are pruned (don't count toward weekly)."""
    with patch("app.services.llm_quota.time.time") as mock_time:
        # 100 calls 8 days ago
        mock_time.return_value = 10000.0
        for _ in range(100):
            ollama_quota.record_call()

        # Jump forward 8 days
        mock_time.return_value = 10000.0 + 8 * 86400
        # is_near_cap calls _prune internally
        assert not ollama_quota.is_near_cap()
        usage = ollama_quota.current_usage()
        # Old calls are out of the 7d window
        assert usage["calls_week"] == 0


def test_ollama_quota_drops_5h_old_timestamps_for_5h_count():
    """Calls older than 5h don't count toward calls_5h (but still count for week)."""
    with patch("app.services.llm_quota.time.time") as mock_time:
        mock_time.return_value = 10000.0
        for _ in range(100):
            ollama_quota.record_call()
        # Jump 6 hours
        mock_time.return_value = 10000.0 + 6 * 3600
        usage = ollama_quota.current_usage()
        assert usage["calls_5h"] == 0  # all out of 5h window
        assert usage["calls_week"] == 100  # still in 7d window


def test_ollama_quota_next_reset_seconds_is_reasonable():
    """next_reset_seconds = how long until the oldest call drops out of 5h."""
    with patch("app.services.llm_quota.time.time") as mock_time:
        mock_time.return_value = 10000.0
        ollama_quota.record_call()
        # 30 minutes later
        mock_time.return_value = 10000.0 + 1800
        usage = ollama_quota.current_usage()
        # Oldest call needs 18000 - 1800 = 16200 more seconds to drop out of 5h
        assert usage["next_reset_seconds"] == pytest.approx(16200, abs=2)


# ─────────────────────────────────────────────────────────────────────────
# Module-level singletons
# ─────────────────────────────────────────────────────────────────────────


def test_singletons_are_stable_across_imports():
    """rate_limiter and ollama_quota are the same instance every time."""
    from app.services import llm_quota
    assert llm_quota.rate_limiter is rate_limiter
    assert llm_quota.ollama_quota is ollama_quota