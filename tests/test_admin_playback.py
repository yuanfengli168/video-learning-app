"""Tests for /admin/playback page + get_playback_analytics() math (2026-09-06).

The playback page derives real WATCH TIME from the play/pause/ended/seek
events emitted by the telemetry beacon. Watch-time math has several
edge cases that deserve explicit coverage:

  - A play → pause pair contributes (pause_ts - play_ts)
  - A play → ended pair contributes (ended_ts - play_ts)
  - A seek splits an active play (close the prior segment)
  - Sessions split on > 5 min gaps (close the active play)
  - An unclosed play at end-of-window caps at video.duration
  - Completion = watch_sec / duration_sec (only when duration known)

These tests use deterministic timestamp deltas so the assertions don't
depend on wall-clock time. They follow the existing test_admin_analytics
pattern (ORM models for setup, raw SQL for event inserts so we control
timestamps exactly).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


FAKE_ADMIN = {"uid": "test-user-uid", "email": "admin@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_admin():
    return patch(
        "app.auth.dependencies.verify_token",
        return_value=FAKE_ADMIN,
    )


def _insert_player_event(
    db_session, *, user_id: str, video_id: str,
    ts: datetime, action: str, position_ms: int = 0,
    to_ms: int = 0,
):
    ctx = json.dumps({
        "action": action,
        "position_ms": position_ms,
        "to_ms": to_ms,
    })
    db_session.execute(
        text(
            "INSERT INTO events (id, ts, level, source, message, user_id, video_id, context_json) "
            "VALUES (lower(hex(randomblob(16))), :ts, 'INFO', "
            "'ui.player', :message, :user_id, :video_id, :ctx)"
        ),
        {
            "ts": ts.isoformat(" "),
            "message": f"ui player {action}",
            "user_id": user_id,
            "video_id": video_id,
            "ctx": ctx,
        },
    )


def _make_user_and_video(
    db_session, *, email: str = "tester@example.com",
    duration: int | None = 600,
) -> tuple[str, str]:
    """Create a user + a video with a known duration. Returns (uid, vid).

    Uses the ORM models (matches production schema exactly — see the
    existing test_admin_analytics.py pattern).
    """
    from app.models import Course, Section, Video
    from app.models.user import User

    uid = "test-uid-" + email.split("@")[0]
    db_session.add(User(user_id=uid, email=email, role=2))

    course = Course(title="test", user_id=uid, description="test")
    db_session.add(course)
    db_session.flush()
    section = Section(title="S", course_id=course.id, order_index=0)
    db_session.add(section)
    db_session.flush()

    video = Video(
        title="Test Video",
        filename="a.mp4",
        file_path="/tmp/a.mp4",
        file_size=1,
        duration=duration,
        order_index=0,
        section_id=section.id,
        status="ready",
        visibility=0,
        caption_languages="[]",
    )
    db_session.add(video)
    db_session.flush()
    db_session.commit()
    return uid, video.id


# ─────────────────────────────────────────────────────────────────────────────
# 1. Watch-time math
# ─────────────────────────────────────────────────────────────────────────────


def test_watch_time_basic_play_pause(db_session):
    """A play at T=0 followed by pause at T=120 → watch_sec=120."""
    from app.services.analytics import get_playback_analytics

    uid, vid = _make_user_and_video(db_session)
    t0 = datetime(2026, 9, 6, 10, 0, 0)
    _insert_player_event(
        db_session, user_id=uid, video_id=vid,
        ts=t0, action="play", position_ms=0,
    )
    _insert_player_event(
        db_session, user_id=uid, video_id=vid,
        ts=t0 + timedelta(seconds=120), action="pause", position_ms=120_000,
    )
    db_session.commit()

    stats = get_playback_analytics(db_session, days=30)
    assert len(stats["per_user_video"]) == 1
    row = stats["per_user_video"][0]
    assert row["watch_sec"] == 120
    assert row["plays"] == 1
    assert row["pauses"] == 1
    assert row["ended_count"] == 0


def test_watch_time_play_ended_uses_full_duration(db_session):
    """An 'ended' closes the segment with the full play→ended span."""
    from app.services.analytics import get_playback_analytics

    uid, vid = _make_user_and_video(db_session)
    t0 = datetime(2026, 9, 6, 10, 0, 0)
    _insert_player_event(
        db_session, user_id=uid, video_id=vid,
        ts=t0, action="play", position_ms=0,
    )
    _insert_player_event(
        db_session, user_id=uid, video_id=vid,
        ts=t0 + timedelta(seconds=600), action="ended", position_ms=600_000,
    )
    db_session.commit()

    stats = get_playback_analytics(db_session, days=30)
    row = stats["per_user_video"][0]
    assert row["watch_sec"] == 600
    assert row["ended_count"] == 1
    # Completion = 600 / 600 = 100%
    assert row["completion_pct"] == 100


def test_seek_splits_active_play(db_session):
    """A seek inside an active play closes the prior segment."""
    from app.services.analytics import get_playback_analytics

    uid, vid = _make_user_and_video(db_session)
    t0 = datetime(2026, 9, 6, 10, 0, 0)
    _insert_player_event(
        db_session, user_id=uid, video_id=vid,
        ts=t0, action="play", position_ms=0,
    )
    _insert_player_event(
        db_session, user_id=uid, video_id=vid,
        ts=t0 + timedelta(seconds=60), action="seek", to_ms=180_000,
    )
    _insert_player_event(
        db_session, user_id=uid, video_id=vid,
        ts=t0 + timedelta(seconds=90), action="pause", position_ms=180_000,
    )
    db_session.commit()

    stats = get_playback_analytics(db_session, days=30)
    row = stats["per_user_video"][0]
    # First play segment closed by seek: 60s.
    # The pause at T+90 closes a non-existent active play (zero contribution).
    # So total = 60s.
    assert row["watch_sec"] == 60
    assert row["seeks"] == 1


def test_session_boundary_at_five_minute_gap(db_session):
    """A > 5 min gap between play and pause → counts as two sessions."""
    from app.services.analytics import get_playback_analytics

    uid, vid = _make_user_and_video(db_session)
    t0 = datetime(2026, 9, 6, 10, 0, 0)
    _insert_player_event(
        db_session, user_id=uid, video_id=vid,
        ts=t0, action="play", position_ms=0,
    )
    # 6 min later, the user comes back. The gap closes the first
    # session before counting the next event.
    _insert_player_event(
        db_session, user_id=uid, video_id=vid,
        ts=t0 + timedelta(minutes=6), action="play", position_ms=0,
    )
    _insert_player_event(
        db_session, user_id=uid, video_id=vid,
        ts=t0 + timedelta(minutes=6, seconds=90), action="pause",
        position_ms=90_000,
    )
    db_session.commit()

    stats = get_playback_analytics(db_session, days=30)
    row = stats["per_user_video"][0]
    # First play: never closed (active_play_start cleared when we
    # crossed the 5-min gap — no contribution).
    # Second play: 90s before pause.
    assert row["watch_sec"] == 90


def test_unclosed_play_caps_at_video_duration(db_session):
    """A play event with no closing event caps at video.duration."""
    from app.services.analytics import get_playback_analytics

    uid, vid = _make_user_and_video(db_session, duration=600)
    # A play event 2 hours ago, never closed.
    t0 = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    _insert_player_event(
        db_session, user_id=uid, video_id=vid,
        ts=t0, action="play", position_ms=0,
    )
    db_session.commit()

    stats = get_playback_analytics(db_session, days=30)
    row = stats["per_user_video"][0]
    # Without the cap, watch_sec would be ~7200 (2 hours). Capped at 600.
    assert row["watch_sec"] == 600


def test_completion_pct_none_when_duration_unknown(db_session):
    """A YouTube video without a duration row → completion_pct is None."""
    from app.services.analytics import get_playback_analytics

    uid, vid = _make_user_and_video(db_session, duration=None)
    t0 = datetime(2026, 9, 6, 10, 0, 0)
    _insert_player_event(
        db_session, user_id=uid, video_id=vid,
        ts=t0, action="play", position_ms=0,
    )
    _insert_player_event(
        db_session, user_id=uid, video_id=vid,
        ts=t0 + timedelta(seconds=120), action="pause", position_ms=120_000,
    )
    db_session.commit()

    stats = get_playback_analytics(db_session, days=30)
    row = stats["per_user_video"][0]
    assert row["watch_sec"] == 120
    assert row["completion_pct"] is None


def test_top_videos_and_users_aggregations(db_session):
    """Multi-user × multi-video → both roll-up lists match."""
    from app.services.analytics import get_playback_analytics

    # Two users, two videos. Each user watches both videos for 60s.
    u1, v1 = _make_user_and_video(db_session, email="u1@example.com")
    u2, v2 = _make_user_and_video(db_session, email="u2@example.com")
    t0 = datetime(2026, 9, 6, 10, 0, 0)
    for u in (u1, u2):
        for v in (v1, v2):
            _insert_player_event(
                db_session, user_id=u, video_id=v,
                ts=t0, action="play", position_ms=0,
            )
            _insert_player_event(
                db_session, user_id=u, video_id=v,
                ts=t0 + timedelta(seconds=60), action="pause",
                position_ms=60_000,
            )
    db_session.commit()

    stats = get_playback_analytics(db_session, days=30)
    # 4 rows: u1/v1, u1/v2, u2/v1, u2/v2
    assert len(stats["per_user_video"]) == 4
    # Each video has 2 viewers × 60s = 120s total
    for v in stats["videos_by_watch_time"]:
        assert v["watch_sec"] == 120
        assert v["unique_viewers"] == 2
        assert v["plays"] == 2
    # Each user watched both videos for 60s each = 120s total
    for u in stats["users_by_watch_time"]:
        assert u["watch_sec"] == 120
        assert u["videos_started"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# 2. Auth gate
# ─────────────────────────────────────────────────────────────────────────────


def test_playback_page_requires_admin(client: TestClient):
    """Anonymous user is bounced away from /admin/playback."""
    r = client.get("/admin/playback")
    # Either 302 (redirect to login) or 403/401 (capability check) is
    # acceptable — the page should NEVER render without auth.
    assert r.status_code in (302, 401, 403)


def test_playback_page_returns_200_for_admin(admin_client: TestClient, db_session):
    """Admin (role=0, has CURATE_CATALOG) sees the page."""
    # admin_client fixture promotes all test UIDs to role=0 and clears
    # the role cache; _mock_admin authenticates as FAKE_ADMIN (uid
    # test-user-uid, part of the client fixture's standard uids).
    with _mock_admin():
        resp = admin_client.get("/admin/playback", headers=_auth_headers())
    assert resp.status_code == 200
    assert b"Playback Analytics" in resp.content
