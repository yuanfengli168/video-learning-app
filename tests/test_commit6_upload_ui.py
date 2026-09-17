"""Commit 6 tests (2026-09-17, launch hardening #6):

1. Queued-cancel (decision #9): the course page shows ✕ ONLY on
   queued FILE uploads (not transcribing/ready/YouTube rows);
   the JS cancel reuses DELETE /videos/{id}.
2. Upload activity card (decision #11): mean/median/max/top-5
   aggregation + live queue gauge; renders on /admin/analytics.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Course, Section, Video


def _mk_section(db: Session, uid: str) -> Section:
    c = Course(title="C", user_id=uid)
    db.add(c)
    db.flush()
    s = Section(title="S", course_id=c.id, order_index=0)
    db.add(s)
    db.commit()
    return s


def _mk_video(
    db: Session, section: Section, *, status: str = "queued",
    youtube_id: str | None = None, title: str = "v",
) -> Video:
    v = Video(
        title=title, filename="v.mp4", file_path="/tmp/v.mp4", file_size=1,
        duration=1.0, order_index=0, section_id=section.id, status=status,
        visibility=0, caption_languages="[]", youtube_id=youtube_id,
    )
    db.add(v)
    db.commit()
    return v


def _owner_auth():
    return patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": "user-A", "email": "a@x.com", "role": 1},
    )


def _admin_auth():
    return patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": "uid-admin", "email": "admin@x.com", "role": 0},
    )


# ── 1. Queued-cancel UI ────────────────────────────────────────────────────


def test_cancel_button_on_queued_file_video(paid_client, db_session):
    """✕ renders on a queued FILE upload (the only deletable state)."""
    section = _mk_section(db_session, "user-A")
    _mk_video(db_session, section, status="queued")

    with _owner_auth():
        resp = paid_client.get(f"/course/{section.course_id}")
    html = resp.text
    assert "cancelQueuedVideo(" in html
    assert "function cancelQueuedVideo" in html


def test_cancel_button_not_on_transcribing(paid_client, db_session):
    """NO ✕ on a transcribing video — GPU already burning; cancel
    would waste it and killing a Metal-thread is dangerous
    (decision #9: transcribing runs to completion)."""
    section = _mk_section(db_session, "user-A")
    _mk_video(db_session, section, status="transcribing")

    with _owner_auth():
        resp = paid_client.get(f"/course/{section.course_id}")
    assert "cancelQueuedVideo('" not in resp.text


def test_cancel_button_not_on_ready_or_youtube(paid_client, db_session):
    """No ✕ on finished videos (they have the normal delete path via
    the video page) and none on YouTube catalog rows (not the user's
    file uploads — the queue never claims them either)."""
    section = _mk_section(db_session, "user-A")
    _mk_video(db_session, section, status="ready")
    _mk_video(db_session, section, status="queued", youtube_id="abc12345678")

    with _owner_auth():
        resp = paid_client.get(f"/course/{section.course_id}")
    assert "cancelQueuedVideo('" not in resp.text


def test_cancel_uses_delete_endpoint(paid_client, db_session):
    """The cancel flow hits DELETE /api/videos/{id} — the existing,
    ownership-checked, cascade-cleaning delete. A queued video the
    owner cancels is GONE from the DB (row + queue position freed +
    the in-flight cap budget returned)."""
    section = _mk_section(db_session, "user-A")
    v = _mk_video(db_session, section, status="queued")

    with _owner_auth():
        resp = paid_client.delete(f"/api/videos/{v.id}")
    assert resp.status_code == 200, resp.text
    # The test session holds a cached instance of the now-deleted row;
    # ANY later flush/rollback on it would try to refresh the ghost
    # and raise ObjectDeletedError. Expunge detaches it cleanly FIRST.
    db_session.expunge(v)
    db_session.rollback()
    from app.database import SessionLocal

    with SessionLocal() as fresh:
        assert fresh.query(Video.id).filter_by(id=v.id).first() is None


# ── 2. Upload activity aggregation ─────────────────────────────────────────


def test_upload_activity_mean_median_gap(db_session):
    """THE signal (decision #11): one 20-video heavy user among five
    1-video users → mean pulled up (4.0) vs median (1.0) — the card's
    ⚠️ copy keys off this gap."""
    from app.services.analytics import get_upload_activity

    sa = _mk_section(db_session, "heavy")
    for _ in range(20):
        _mk_video(db_session, sa, title="h")
    for i in range(5):
        s = _mk_section(db_session, f"light{i}")
        _mk_video(db_session, s, title="l")

    act = get_upload_activity(db_session, days=7)
    assert act["total"] == 25
    assert act["uploaders"] == 6
    # Service rounds to 2dp (display precision).
    assert act["mean_per_user_day"] == round(25 / 6, 2)
    assert act["median_per_user_day"] == 1.0, (
        "median must resist the heavy user — 5 light users at 1 each "
        "outvote one 20-upload user per user-day"
    )
    assert act["max_user_day"]["uploads"] == 20
    assert act["top_uploaders"][0]["uploads"] == 20


def test_upload_activity_empty_window(db_session):
    """No uploads in window → zeros, no crash, queue gauge intact."""
    from app.services.analytics import get_upload_activity

    act = get_upload_activity(db_session, days=7)
    assert act["total"] == 0
    assert act["uploaders"] == 0
    assert act["max_user_day"] is None
    assert act["top_uploaders"] == []
    assert act["queue"]["slots"] == 2


def test_upload_activity_queue_gauge(db_session):
    """The live queue gauge mirrors the mini-queue state — waiting +
    running counts flow straight through to the admin card."""
    from app.services.analytics import get_upload_activity

    sa = _mk_section(db_session, "u1")
    _mk_video(db_session, sa, status="queued")
    _mk_video(db_session, sa, status="queued")
    _mk_video(db_session, sa, status="transcribing")

    act = get_upload_activity(db_session, days=7)
    assert act["queue"]["waiting"] == 2
    assert act["queue"]["running"] == 1
    assert act["queue"]["free"] == 1


def test_analytics_page_renders_upload_card(admin_client, db_session):
    """The card renders on /admin/analytics with the mean/median/max
    numbers + queue gauge visible to the admin."""
    sa = _mk_section(db_session, "u1")
    _mk_video(db_session, sa, title="one")
    _mk_video(db_session, sa, status="queued", title="q")

    with _admin_auth():
        resp = admin_client.get("/admin/analytics")
    assert resp.status_code == 200
    html = resp.text
    assert "Upload activity" in html
    assert "Mean" in html and "Median" in html
    assert "Queue now" in html