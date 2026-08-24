"""LLM rate limiting + Ollama quota tracking (Day 4).

Two pieces of state, both process-local (in-memory):

1. LlmRateLimiter
   Per-user sliding-window rate limiter for LLM calls. Resets on
   server restart (acceptable for MVP — see Day 4 plan note).
   Tier-based limits come from settings.get_rate_limit_per_{min,day}(role).

2. OllamaQuotaTracker
   Records timestamps of every Ollama call. Computes calls in
   last 5 hours + last 7 days. When EITHER window hits
   `settings.ollama_quota_alert_pct` of its limit, returns near_cap=True.
   The fallback wrapper (llm_providers.py) uses this to auto-skip Ollama
   and go straight to the next provider in the chain.

Why in-memory
-------------
Both pieces of state are intentionally non-persistent:
  - Rate limiter: a server restart "resets" the count. If a user
    was rate-limited and the server restarts, they get a fresh slate.
    Acceptable for MVP (single admin user, LaunchDaemon-restart is rare).
  - Quota tracker: same — worst case is we briefly exceed Ollama's
    5h cap before the tracker catches up. Ollama's own 5h window is
    also rolling, so we naturally sync.

Future: persist to a small SQLite table (~20 lines). Day 5+.

Public API
----------
LlmRateLimiter:
  check_and_record(uid: str, role: int) -> RateLimitResult
    Returns (allowed: bool, retry_after_seconds: int, reason: str).
    If allowed, records the call. If denied, doesn't record (the
    rejected call doesn't count toward the next window).

  reset_user(uid: str) -> None
    For tests + admin manual override.

  current_usage(uid: str, role: int) -> dict
    Returns {'calls_in_last_min': int, 'calls_today': int,
            'limit_min': int, 'limit_day': int} for diagnostics.

OllamaQuotaTracker:
  record_call() -> None
    Append now() to the deque.

  is_near_cap() -> bool
    True if 5h or weekly window is at >= alert_pct of limit.

  current_usage() -> dict
    Returns {'calls_5h': int, 'calls_week': int,
            'limit_5h': int, 'limit_week': int,
            'near_cap': bool, 'next_reset_seconds': int}
    for the admin observability endpoint.

  reset() -> None
    For tests only.

Both classes are module-level singletons (one per process).
Importing this module gets you the same instance every time —
matches the pattern used elsewhere in the app (app.jobs).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Any, Deque

from app.config import settings


# ─────────────────────────────────────────────────────────────────────────
# Rate limiter
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class RateLimitResult:
    """Outcome of a rate-limit check."""

    allowed: bool
    retry_after_seconds: int
    reason: str  # "" when allowed; human-readable string when denied


class LlmRateLimiter:
    """Per-user sliding-window rate limiter for LLM calls.

    Stores a deque of timestamps per user. Two windows: per-minute
    and per-day. A call is allowed iff both windows are under their
    respective tier-based limits.

    Thread-safe via a single Lock. The lock is held only during the
    brief prune + append + check, so contention is negligible.
    """

    # _buckets[uid] = deque[float] (epoch seconds of recent calls)
    _buckets: dict[str, Deque[float]]
    _lock: Lock

    def __init__(self) -> None:
        self._buckets = {}
        self._lock = Lock()

    # ── Public API ────────────────────────────────────────────────────

    def check_and_record(self, uid: str, role: int) -> RateLimitResult:
        """Check the user's per-minute and per-day limits, record if allowed.

        Returns:
            RateLimitResult with allowed=True and retry_after=0 if both
            windows have capacity. Otherwise allowed=False + retry_after
            hinting how long until the window has space again.
        """
        per_min = settings.get_rate_limit_per_min(role)
        per_day = settings.get_rate_limit_per_day(role)
        now = time.time()

        with self._lock:
            bucket = self._buckets.setdefault(uid, deque())
            # Prune anything outside the day window (we keep day as the
            # longest window — both minute and day buckets use the same
            # deque; minute is just a slice of it).
            cutoff_day = now - 86400  # 24h
            while bucket and bucket[0] < cutoff_day:
                bucket.popleft()

            cutoff_min = now - 60
            calls_last_min = sum(1 for t in bucket if t >= cutoff_min)
            calls_today = len(bucket)

            if calls_today >= per_day:
                # When in the day window is the next slot free?
                # = (oldest_in_day) + 86400
                next_free = int(bucket[0] + 86400 - now) if bucket else 60
                return RateLimitResult(
                    allowed=False,
                    retry_after_seconds=max(1, next_free),
                    reason=(
                        f"Daily LLM-call limit reached "
                        f"({calls_today}/{per_day}). "
                        f"Resets in ~{next_free // 60} minutes."
                    ),
                )

            if calls_last_min >= per_min:
                # When in the minute window is the next slot free?
                # Find the (per_min)th newest timestamp; everything newer
                # than (that - 60) is "in the last minute".
                recent = [t for t in bucket if t >= cutoff_min]
                if len(recent) >= per_min:
                    oldest_in_min = recent[0]
                    next_free = int(oldest_in_min + 60 - now)
                    return RateLimitResult(
                        allowed=False,
                        retry_after_seconds=max(1, next_free),
                        reason=(
                            f"Per-minute LLM-call limit reached "
                            f"({len(recent)}/{per_min}). "
                            f"Resets in ~{next_free}s."
                        ),
                    )

            # Both windows have room. Record.
            bucket.append(now)
            return RateLimitResult(
                allowed=True,
                retry_after_seconds=0,
                reason="",
            )

    def reset_user(self, uid: str) -> None:
        """Clear all timestamps for one user (admin override / tests)."""
        with self._lock:
            self._buckets.pop(uid, None)

    def reset_all(self) -> None:
        """Clear everything. Tests only."""
        with self._lock:
            self._buckets.clear()

    def current_usage(self, uid: str, role: int) -> dict[str, Any]:
        """Diagnostic snapshot for the admin observability endpoint."""
        now = time.time()
        cutoff_min = now - 60
        cutoff_day = now - 86400
        with self._lock:
            bucket = self._buckets.get(uid, deque())
            calls_last_min = sum(1 for t in bucket if t >= cutoff_min)
            calls_today = sum(1 for t in bucket if t >= cutoff_day)
        return {
            "calls_in_last_min": calls_last_min,
            "calls_today": calls_today,
            "limit_per_min": settings.get_rate_limit_per_min(role),
            "limit_per_day": settings.get_rate_limit_per_day(role),
        }


# ─────────────────────────────────────────────────────────────────────────
# Ollama quota tracker
# ─────────────────────────────────────────────────────────────────────────


class OllamaQuotaTracker:
    """Records every Ollama call; reports when we're at 90% of either cap.

    Two rolling windows:
      - 5 hours (Ollama Pro per-5h cap)
      - 7 days / 604800s (Ollama Pro weekly cap)

    When EITHER is at >= settings.ollama_quota_alert_pct of its limit,
    `is_near_cap()` returns True and `call_llm_with_fallback()` will
    skip Ollama entirely (going to the next provider in the chain).
    """

    # _timestamps: deque[float] of epoch seconds for every Ollama call
    _timestamps: Deque[float]
    _lock: Lock

    def __init__(self) -> None:
        self._timestamps = deque()
        self._lock = Lock()

    # ── Public API ────────────────────────────────────────────────────

    def record_call(self) -> None:
        """Append now() to the deque. Called after a successful Ollama call."""
        with self._lock:
            self._timestamps.append(time.time())

    def is_near_cap(self) -> bool:
        """True if either window is at or above the alert threshold."""
        with self._lock:
            return self._is_near_cap_locked()

    def current_usage(self) -> dict[str, Any]:
        """Snapshot for admin observability endpoint."""
        with self._lock:
            return self._current_usage_locked()

    def reset(self) -> None:
        """Clear everything. Tests only."""
        with self._lock:
            self._timestamps.clear()

    # ── Private (must hold lock) ────────────────────────────────────────

    def _is_near_cap_locked(self) -> bool:
        now = time.time()
        alert_pct = settings.ollama_quota_alert_pct
        # Prune to 7 days (the larger window)
        cutoff_week = now - 604800
        while self._timestamps and self._timestamps[0] < cutoff_week:
            self._timestamps.popleft()
        calls_5h = sum(1 for t in self._timestamps if t >= now - 18000)
        calls_week = len(self._timestamps)
        limit_5h = settings.ollama_5h_request_limit
        limit_week = settings.ollama_weekly_request_limit
        return (
            calls_5h >= int(limit_5h * alert_pct)
            or calls_week >= int(limit_week * alert_pct)
        )

    def _current_usage_locked(self) -> dict[str, Any]:
        now = time.time()
        cutoff_week = now - 604800
        while self._timestamps and self._timestamps[0] < cutoff_week:
            self._timestamps.popleft()
        calls_5h = sum(1 for t in self._timestamps if t >= now - 18000)
        calls_week = len(self._timestamps)
        limit_5h = settings.ollama_5h_request_limit
        limit_week = settings.ollama_weekly_request_limit
        alert_pct = settings.ollama_quota_alert_pct
        # "next reset" is when the OLDEST call drops out of the window
        # — that's when the user gets back 1 slot.
        next_reset = 0
        if self._timestamps:
            next_reset = int(self._timestamps[0] + 18000 - now)
        return {
            "calls_5h": calls_5h,
            "calls_week": calls_week,
            "limit_5h": limit_5h,
            "limit_week": limit_week,
            "near_cap": (
                calls_5h >= int(limit_5h * alert_pct)
                or calls_week >= int(limit_week * alert_pct)
            ),
            "next_reset_seconds": max(0, next_reset),
        }


# ─────────────────────────────────────────────────────────────────────────
# Module-level singletons
# ─────────────────────────────────────────────────────────────────────────

rate_limiter = LlmRateLimiter()
ollama_quota = OllamaQuotaTracker()