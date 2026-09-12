"""Tests for the 2026-09-12 recovery/metadata batch.

1) admin requeue-stuck endpoint: videos stuck in status='queued'
   (their in-memory pipeline died with a server restart) get
   re-queued — file uploads via the Whisper auto-pipeline, YouTube
   videos via the caption-download job, staggered.
2) Discuss resume re-enables the textarea (the stuck-disabled input
   bug from the same day).
3) Chat history metadata: the sessions list resolves video_title for
   video-scope rows (and the detail endpoint too).
"""

import io
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Course, Section, Video

FAKE_ADMIN = {"uid": "uid-admin", "email": "admin@x.com", "role": 0}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_admin():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_ADMIN)


def _mk_course_section(db: Session, uid: str = "uid-admin") -> Section:
    course = Course(title="Course", description="", user_id=uid)
    db.add(course)
    db.flush()
    section = Section(title="S", course_id=course.id, order_index=0)
    db.add(section)
    db.commit()
    return section


def _mk_video(db: Session, section_id: str, *, status: str = "queued",
              youtube_id: str | None = None, title: str = "v") -> Video:
    video = Video(
        title=title, filename="a.mp4", file_path="/tmp/a.mp4",
        file_size=1, duration=1.0, order_index=0,
        section_id=section_id, status=status, visibility=0,
        caption_languages="[]", youtube_id=youtube_id,
    )
    db.add(video)
    db.commit()
    return video


# ── 1. requeue-stuck endpoint ─────────────────────────────────────────────


def test_requeue_stuck_dispatches_per_type(admin_client: TestClient, db_session):
    """File uploads → auto pipeline; YouTube videos → caption job;
    staggered; ready/error videos untouched."""
    section = _mk_course_section(db_session)
    file_video = _mk_video(db_session, section.id, title="file one")
    yt_video = _mk_video(db_session, section.id, title="yt one",
                         youtube_id="dQw4w9WgXcQ")
    ready_video = _mk_video(db_session, section.id, status="ready")

    with patch("app.routers.admin._requeue_staggered_pipeline") as file_job, \
         patch("app.routers.admin._requeue_staggered_youtube") as yt_job, \
         _mock_admin():
        resp = admin_client.post(
            "/api/admin/videos/requeue-stuck", headers=_auth_headers()
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["requeued"] == 2
    assert data["file_uploads"] == 1
    assert data["youtube_videos"] == 1

    # Both stuck videos dispatched (with stagger delays 0 and 5s)…
    file_job.assert_called_once()
    yt_job.assert_called_once()
    called_ids = {file_job.call_args[0][0], yt_job.call_args[0][0]}
    assert called_ids == {file_video.id, yt_video.id}
    # …and staggered: one of them got a 5s delay
    delays = {file_job.call_args[0][1], yt_job.call_args[0][1]}
    assert delays == {0, 5}


def test_requeue_stuck_nothing_queued(admin_client: TestClient, db_session):
    """No stuck videos → friendly no-op message, not an error."""
    section = _mk_course_section(db_session)
    _mk_video(db_session, section.id, status="ready")

    with _mock_admin():
        resp = admin_client.post(
            "/api/admin/videos/requeue-stuck", headers=_auth_headers()
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["requeued"] == 0
    assert "No stuck videos" in data["message"]


def test_requeue_stuck_requires_admin(paid_client: TestClient, db_session):
    """PAID users (no CURATE_CATALOG) get 403 — the recovery is an
    admin-only blast-radius control."""
    section = _mk_course_section(db_session)
    _mk_video(db_session, section.id)
    fake_paid = {"uid": "user-A", "email": "a@x.com", "role": 1}
    with patch("app.auth.dependencies.verify_token", return_value=fake_paid):
        resp = paid_client.post(
            "/api/admin/videos/requeue-stuck", headers=_auth_headers()
        )
    assert resp.status_code == 403


def test_course_page_shows_requeue_button_for_admin(
    admin_client: TestClient, db_session
):
    """The course page renders the ⏳ re-queue button for admins when a
    section has queued videos."""
    section = _mk_course_section(db_session)
    _mk_video(db_session, section.id)
    course_id = section.course_id

    with _mock_admin():
        resp = admin_client.get(f"/course/{course_id}")
    assert resp.status_code == 200
    html = resp.text
    assert "requeueStuck(" in html
    assert "queued — re-queue" in html
    assert "function requeueStuck" in html


def test_course_page_no_requeue_button_without_queued(
    admin_client: TestClient, db_session
):
    """No queued videos → no BUTTON markup (the JS function itself
    always ships — harmless without a caller, same convention as
    rename/sort)."""
    section = _mk_course_section(db_session)
    _mk_video(db_session, section.id, status="ready")

    with _mock_admin():
        resp = admin_client.get(f"/course/{section.course_id}")
    assert resp.status_code == 200
    # Target the button's onclick markup, not the function definition
    assert "requeueStuck(this)" not in resp.text
    assert "queued — re-queue" not in resp.text


# ── 2. Discuss resume re-enables the input ────────────────────────────────


def test_discuss_resume_reenables_input(paid_client: TestClient, db_session):
    """Regression pin for the stuck-disabled textarea: the resume path
    must re-enable the input before returning (the bug: only the
    create-new path re-enabled it)."""
    section = _mk_course_section(db_session, uid="test-user-uid")
    video = _mk_video(db_session, section.id, status="ready")

    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "test-user-uid", "email": "t@x.com", "role": 1}):
        resp = paid_client.get(f"/video/{video.id}")
    assert resp.status_code == 200
    html = resp.text
    # The resume branch re-enables + focuses the input before returning
    assert "input.disabled = false;" in html
    # ...and the fix comment is present (pin the intent)
    assert "Resume path done" in html


# ── 3. Chat history metadata ──────────────────────────────────────────────


def test_sessions_list_resolves_video_title(paid_client: TestClient, db_session):
    """The list endpoint joins the video title so the chat-history page
    can show WHICH video a discussion belongs to."""
    section = _mk_course_section(db_session, uid="test-user-uid")
    video = _mk_video(db_session, section.id, status="ready",
                      title="My Test Video Title")

    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "test-user-uid", "email": "t@x.com", "role": 1}):
        create = paid_client.post(
            "/api/chat/video-sessions",
            json={"video_id": video.id},
            headers=_auth_headers(),
        )
        assert create.status_code == 200, create.text
        listing = paid_client.get(
            "/api/chat/sessions", headers=_auth_headers()
        )

    assert listing.status_code == 200
    sessions = listing.json()
    mine = [s for s in sessions if s["id"] == create.json()["session_id"]]
    assert mine, "created session should be listed"
    assert mine[0]["video_title"] == "My Test Video Title"
    assert mine[0]["scope"] == "video"


def test_sessions_list_title_none_when_video_deleted(
    paid_client: TestClient, db_session
):
    """Deleted video → video_title None (UI falls back), not a crash."""
    section = _mk_course_section(db_session, uid="test-user-uid")
    video = _mk_video(db_session, section.id, status="ready")

    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "test-user-uid", "email": "t@x.com", "role": 1}):
        create = paid_client.post(
            "/api/chat/video-sessions",
            json={"video_id": video.id},
            headers=_auth_headers(),
        )
        session_id = create.json()["session_id"]
        # Simulate the video vanishing (cascade not wired in this
        # minimal fixture) — point the session at a dead id.
        from app.models import ChatSession
        sess = db_session.get(ChatSession, session_id)
        sess.video_id = "no-such-video"
        db_session.commit()

        listing = paid_client.get(
            "/api/chat/sessions", headers=_auth_headers()
        )

    assert listing.status_code == 200
    row = next(
        s for s in listing.json() if s["id"] == session_id
    )
    assert row["video_title"] is None


def test_session_detail_resolves_video_title(
    paid_client: TestClient, db_session
):
    """GET /sessions/{id} also returns video_title for the detail header."""
    section = _mk_course_section(db_session, uid="test-user-uid")
    video = _mk_video(db_session, section.id, status="ready",
                      title="Detail Title Video")

    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "test-user-uid", "email": "t@x.com", "role": 1}):
        create = paid_client.post(
            "/api/chat/video-sessions",
            json={"video_id": video.id},
            headers=_auth_headers(),
        )
        detail = paid_client.get(
            f"/api/chat/sessions/{create.json()['session_id']}",
            headers=_auth_headers(),
        )

    assert detail.status_code == 200
    assert detail.json()["video_title"] == "Detail Title Video"