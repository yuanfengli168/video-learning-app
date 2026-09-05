"""Per-user LLM usage computation from the events table (2026-09-05).

Why events-table counting (not the in-memory quota trackers):
  The app runs gunicorn with 4 workers, and both LlmRateLimiter +
  OllamaQuotaTracker are module-level singletons — deliberately
  per-worker (see gunicorn.conf.py). A user's calls therefore land in
  4 different in-memory buckets, and whichever worker serves a usage
  page would show only its slice. The events table, by contrast,
  receives a row from EVERY worker via log_event() — so COUNT(*) on
  it is the worker-independent truth.

What counts as a "request" for the usage page:
  events.source == 'services.llm_providers'
  AND events.message LIKE 'LLM call succeeded via %'
  → one row per successful LLM completion, per user_id.

Window semantics (user decision 2026-09-05):
  - FREE: daily (UTC day) count vs the 15/day Groq-claim. Display-only
    plain words — the actual enforcement stays in the per-worker
    limiter (known 4x-loose gap, tracked for post-launch fix).
  - PAID: two bars.
      * 7-hour rolling window vs 50 requests.
      * FIXED calendar week (Mon 00:00:00 → Sun 23:59:59, inclusive)
        vs 100 requests. NOT a rolling 7 days — matches the product
        copy "Monday to Sunday (inclusive)".
  Both display-only for the 9/9 soft launch (no blocking).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.roles import UserRole
from app.config import settings

# The PAID per-user display limits (user decision 2026-09-05):
PAID_LIMIT_7H = 50
PAID_LIMIT_WEEK = 100

# FREE daily claim — from the configured per-day rate limit (Groq).
# The Groq free-tier wording shown to users comes from the frontend
# copy; the number here must stay in sync with
# settings.rate_limit_free_per_day.
_FREE_LIMIT_PER_DAY = settings.rate_limit_free_per_day


def _week_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Fixed Mon 00:00:00 → Sun 23:59:59 UTC window containing `now`.

    Monday=0 ... Sunday=6 in Python's weekday(). The week START is the
    Monday 00:00 of `now`'s week; the END is the following Monday
    00:00 minus 1 second (23:59:59 Sunday inclusive).
    """
    # Normalize to UTC naive (events.ts is naive UTC — log_event uses
    # CURRENT_TIMESTAMP, and sqlite's datetime('now') is UTC).
    now_utc = now.astimezone(timezone.utc).replace(tzinfo=None)
    days_since_monday = now_utc.weekday()  # Mon=0
    week_start = (now_utc - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_end = week_start + timedelta(days=7) - timedelta(seconds=1)
    return week_start, week_end


def _count_llm_calls(
    db: Session,
    uid: str,
    *,
    since: datetime,
    until: datetime | None = None,
) -> int:
    """Count successful LLM completions by `uid` in [since, until]."""
    sql = text(
        """
        SELECT COUNT(*) FROM events
        WHERE user_id = :uid
          AND source = 'services.llm_providers'
          AND message LIKE 'LLM call succeeded via %'
          AND ts >= :since
        """
    )
    params: dict[str, Any] = {"uid": uid, "since": since}
    if until is not None:
        sql = text(
            """
            SELECT COUNT(*) FROM events
            WHERE user_id = :uid
              AND source = 'services.llm_providers'
              AND message LIKE 'LLM call succeeded via %'
              AND ts >= :since
              AND ts <= :until
            """
        )
        params["until"] = until
    return int(db.execute(sql, params).scalar() or 0)


def get_user_usage(db: Session, uid: str, role: int) -> dict[str, Any]:
    """Compute the per-user usage snapshot for the usage page.

    Returns a tier-shaped dict:
      FREE:  {"tier": "free", "day": {"used": n, "limit": 15},
              "claims": "<Groq free-tier wording>"}
      PAID:  {"tier": "paid",
              "last7h": {"used": n, "limit": 50},
              "week":    {"used": n, "limit": 100, "starts": iso,
                         "ends": iso}}
      ADMIN: same shape as PAID (admins share the paid-style display;
              the shared Ollama pool health lives on /admin/llm/budget).
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if role == UserRole.FREE:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        used_day = _count_llm_calls(db, uid, since=day_start)
        return {
            "tier": "free",
            "day": {"used": used_day, "limit": _FREE_LIMIT_PER_DAY},
        }

    # PAID / ADMIN — the two bars.
    cutoff_7h = now - timedelta(hours=7)
    used_7h = _count_llm_calls(db, uid, since=cutoff_7h)

    week_start, week_end = _week_bounds(now)
    used_week = _count_llm_calls(db, uid, since=week_start, until=week_end)

    return {
        "tier": "paid" if role == UserRole.PAID else "admin",
        "last7h": {"used": used_7h, "limit": PAID_LIMIT_7H},
        "week": {
            "used": used_week,
            "limit": PAID_LIMIT_WEEK,
            "starts": week_start.isoformat(sep=" "),
            "ends": week_end.isoformat(sep=" "),
        },
    }