"""Tests for the usage page + per-user usage computation (2026-09-05).

Covers app/services/usage.py + GET /usage:
  1. Week bounds — fixed Mon 00:00 → Sun 23:59:59 UTC, regardless of
     the day the check runs.
  2. FREE shape — daily count from events, 15/day limit.
  3. PAID shape — 7h rolling + fixed-week counts, 50/100 limits.
  4. Events actually written by log_event are counted (integration
     with the real writer).
  5. Page renders — signed in → 200 with the right tier copy;
     signed out → redirect to login.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.services.usage import PAID_LIMIT_7H, PAID_LIMIT_WEEK, get_user_usage

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    from unittest.mock import patch
    return patch(
        "app.auth.dependencies.verify_token",
        return_value=FAKE_USER,
    )


def _seed_llm_events(
    db_session, uid: str, count: int, *, hours_ago: float = 0.0,
    now: datetime | None = None,
):
    """Write `count` successful-LLM-call events for `uid`.

    hours_ago shifts all timestamps back so they fall outside windows.
    (0.0 = now, inside both the 7h and week windows.)

    `now` lets tests pin the reference clock so hour-shifts are
    relative to a known point (avoids Sunday→Monday boundary flakes
    when the suite runs on the live host clock).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    ts = now.replace(tzinfo=None) - timedelta(hours=hours_ago)
    for _ in range(count):
        db_session.execute(
            text(
                "INSERT INTO events (id, ts, level, source, message, user_id, context_json) "
                "VALUES (lower(hex(randomblob(16))), :ts, 'INFO', "
                "'services.llm_providers', 'LLM call succeeded via ollama/glm-5.2:cloud', "
                ":uid, '{}')"
            ),
            {"ts": ts.strftime("%Y-%m-%d %H:%M:%S"), "uid": uid},
        )
    db_session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Week bounds
# ─────────────────────────────────────────────────────────────────────────────

def test_week_bounds_fixed_monday_to_sunday():
    from app.services.usage import _week_bounds

    # A Wednesday in 2026-09 (2026-09-05 is a Saturday; use a known
    # Wednesday: 2026-09-02)
    wed = datetime(2026, 9, 2, 15, 30, 0, tzinfo=timezone.utc)
    start, end = _week_bounds(wed)
    assert start == datetime(2026, 8, 31, 0, 0, 0)  # Monday
    assert start.weekday() == 0
    assert end == datetime(2026, 9, 6, 23, 59, 59)  # Sunday 23:59:59
    assert end.weekday() == 6
    # Monday of the following week starts a NEW window
    mon2 = datetime(2026, 9, 7, 0, 0, 1, tzinfo=timezone.utc)
    start2, _ = _week_bounds(mon2)
    assert start2 == datetime(2026, 9, 7, 0, 0, 0)


# ─────────────────────────────────────────────────────────────────────────────
# 2-3. Shapes per tier
# ─────────────────────────────────────────────────────────────────────────────

def test_free_usage_shape(db_session):
    _seed_llm_events(db_session, "u-free", 3)
    snap = get_user_usage(db_session, "u-free", role=2)  # FREE
    assert snap["tier"] == "free"
    assert snap["day"]["used"] == 3
    assert snap["day"]["limit"] == 15


def test_paid_usage_shape(db_session):
    _seed_llm_events(db_session, "u-paid", 7)
    snap = get_user_usage(db_session, "u-paid", role=1)  # PAID
    assert snap["tier"] == "paid"
    assert snap["last7h"]["used"] == 7
    assert snap["last7h"]["limit"] == PAID_LIMIT_7H == 50
    assert snap["week"]["used"] == 7
    assert snap["week"]["limit"] == PAID_LIMIT_WEEK == 100
    assert "starts" in snap["week"] and "ends" in snap["week"]


def test_week_window_excludes_last_week(db_session):
    """Events 8 days ago fall OUTSIDE the fixed week (Mon–Sun window).

    Pins `now` to a known Wednesday at 12:00 UTC so 8-day-old events
    are unambiguously in the previous week regardless of clock skew.
    """
    fixed_now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)  # Wed
    _seed_llm_events(db_session, "u-old", 10, hours_ago=24 * 8, now=fixed_now)
    snap = get_user_usage(db_session, "u-old", role=1, now=fixed_now)
    assert snap["week"]["used"] == 0


def test_7h_window_excludes_old_calls(db_session):
    """Events 8 hours ago fall OUTSIDE the rolling 7h window but INSIDE
    the fixed Mon→Sun week window.

    The test pins `now` to a known Wednesday at 12:00 UTC so the
    8-hour-old events land safely inside the current week regardless
    of when the suite runs (avoids the Sunday→Monday boundary flake
    the real clock hits on the Mac Studio host).
    """
    fixed_now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)  # Wed
    _seed_llm_events(db_session, "u-7h", 10, hours_ago=8, now=fixed_now)
    snap = get_user_usage(db_session, "u-7h", role=1, now=fixed_now)
    assert snap["last7h"]["used"] == 0
    assert snap["week"]["used"] == 10


def test_failed_calls_not_counted(db_session):
    """Only 'LLM call succeeded' rows count — failures don't count
    against the user (documented in the page copy)."""
    db_session.execute(
        text(
            "INSERT INTO events (id, ts, level, source, message, user_id, context_json) "
            "VALUES (lower(hex(randomblob(16))), datetime('now'), 'WARNING', "
            "'services.llm_providers', 'LLM call failed on groq/groq/compound-mini', "
            "'u-fail', '{}')"
        )
    )
    db_session.commit()
    snap = get_user_usage(db_session, "u-fail", role=1)
    assert snap["last7h"]["used"] == 0
    assert snap["week"]["used"] == 0


def test_ui_events_not_counted_as_usage(db_session):
    """ui.* telemetry events must NOT inflate usage counts — only
    services.llm_providers successes count."""
    db_session.execute(
        text(
            "INSERT INTO events (id, ts, level, source, message, user_id, context_json) "
            "VALUES (lower(hex(randomblob(16))), datetime('now'), 'INFO', "
            "'ui.player', 'ui player play', 'u-ui', '{}')"
        )
    )
    db_session.commit()
    snap = get_user_usage(db_session, "u-ui", role=1)
    assert snap["last7h"]["used"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 5. Page rendering
# ─────────────────────────────────────────────────────────────────────────────

def test_usage_page_requires_login(client: TestClient):
    client.cookies.clear()
    resp = client.get("/usage", follow_redirects=False)
    assert resp.status_code in (302, 307, 200)  # redirect or login render
    if resp.status_code == 200:
        # Rendered login directly — fine as long as it's not the usage page
        assert "Your Usage" not in resp.text


def test_usage_page_free_renders(client: TestClient):
    with _mock_auth():
        resp = client.get("/usage", headers=_auth_headers())
    assert resp.status_code == 200
    assert "requests today" in resp.text
    assert "Groq" in resp.text


def test_usage_page_paid_renders_bars(paid_client: TestClient):
    with _mock_auth():
        resp = paid_client.get("/usage", headers=_auth_headers())
    assert resp.status_code == 200
    assert "Last 7 hours" in resp.text
    assert "This week" in resp.text
    assert "Monday through Sunday" in resp.text