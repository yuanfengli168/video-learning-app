"""Tests for the 2026-09-19 orphan-recovery batch.

Incident: a worker restart (kickstart / max_requests recycle) evapo-
rates in-memory BackgroundTasks mid-run. Rows stuck in 'transcribing'/
'pending' forever ALSO hold the 2-slot transcribe queue hostage (the
queue counts them as in-flight), so new uploads stop processing too.

Covers:
1) POST /api/admin/videos/recover-orphans:
   - stuck transcribing + old pending YouTube rows → marked error
     (slots freed) + re-dispatched with stagger
   - fresh rows (< 15 min) are NOT touched (in-flight jobs are safe)
   - ready/error rows untouched (idempotent)
   - empty case → friendly message
   - PAID user → 403
2) The course page badge: orphan count is computed server-side and
   rendered for admins only.
3) Job-registration bugfixes (2026-09-19):
   - _staggered_transcribe_job (queue path) registers the transcribe
     job so a cross-worker claim can't silently no-op
   - _requeue_staggered_pipeline (requeue-stuck path) ditto
"""

from datetime import datetime, timedelta, timezone as _tz
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


def _mk_video(
    db: Session,
    section_id: str,
    *,
    status: str = "pending",
    youtube_id: str | None = None,
    title: str = "v",
    created_at: datetime | None = None,
    transcribe_started_at: datetime | None = None,
) -> Video:
    video = Video(
        title=title, filename="a.mp4", file_path="/tmp/a.mp4",
        file_size=1, duration=1.0, order_index=0,
        section_id=section_id, status=status, visibility=0,
        caption_languages="[]", youtube_id=youtube_id,
    )
    if created_at is not None:
        video.created_at = created_at
    if transcribe_started_at is not None:
        video.transcribe_started_at = transcribe_started_at
    db.add(video)
    db.commit()
    return video


def _old(n_minutes: int = 30) -> datetime:
    """Timestamp n minutes ago, naive UTC (matches the DB)."""
    return datetime.now(_tz.utc).replace(tzinfo=None) - timedelta(
        minutes=n_minutes
    )


def _fresh(n_minutes: int = 1) -> datetime:
    return _old(-n_minutes)


# ── 1. recover-orphans endpoint ───────────────────────────────────────────


def test_recover_orphans_marks_and_dispatches(admin_client, db_session):
    """Stuck transcribing + old pending YouTube rows → marked error
    (slots freed immediately) + staggered re-dispatch per type."""
    section = _mk_course_section(db_session)

    # Stuck file upload: transcribing for 30 min
    stuck_file = _mk_video(
        db_session, section.id, status="transcribing",
        created_at=_old(40), transcribe_started_at=_old(30),
    )
    # Orphaned pending YouTube row: created 30 min ago, never started
    orphan_yt = _mk_video(
        db_session, section.id, status="pending",
        youtube_id="dQw4w9WgXcQ", created_at=_old(30),
    )

    with patch("app.routers.admin._requeue_staggered_pipeline") as file_job, \
         patch("app.routers.admin._requeue_staggered_youtube") as yt_job, \
         _mock_admin():
        resp = admin_client.post(
            "/api/admin/videos/recover-orphans", headers=_auth_headers()
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["recovered"] == 2
    assert data["file_uploads"] == 1
    assert data["youtube_videos"] == 1

    # Both re-dispatched with the right helpers
    file_job.assert_called_once()
    yt_job.assert_called_once()
    assert file_job.call_args[0][0] == stuck_file.id
    assert yt_job.call_args[0][0] == orphan_yt.id
    # YouTube spacing (30s) is wider than the file stagger (5s)
    assert yt_job.call_args[0][1] == 0
    assert file_job.call_args[0][1] == 0

    # Slots freed: rows now 'error' with a recovery reason
    db_session.expire_all()
    for v in (stuck_file, orphan_yt):
        row = db_session.get(Video, v.id)
        assert row.status == "error"
        assert "orphaned" in (row.whisper_fallback_reason or "")


def test_recover_orphans_spares_fresh_rows(admin_client, db_session):
    """Rows younger than 15 min are in-flight, not orphaned — a
    recovery click during normal processing must not kill them."""
    section = _mk_course_section(db_session)
    fresh_yt = _mk_video(
        db_session, section.id, status="pending",
        youtube_id="dQw4w9WgXcQ", created_at=_fresh(2),
    )
    fresh_file = _mk_video(
        db_session, section.id, status="transcribing",
        created_at=_fresh(1), transcribe_started_at=_fresh(1),
    )

    with _mock_admin():
        resp = admin_client.post(
            "/api/admin/videos/recover-orphans", headers=_auth_headers()
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["recovered"] == 0
    assert "No orphaned" in data["message"]

    db_session.expire_all()
    assert db_session.get(Video, fresh_yt.id).status == "pending"
    assert db_session.get(Video, fresh_file.id).status == "transcribing"


def test_recover_orphans_pending_file_upload_untouched(
    admin_client, db_session
):
    """A 'pending' FILE row is not in the orphan taxonomy (it uses the
    transcribe queue, which self-recovers; only YouTube pending rows
    are orphanable — their pipeline is a BackgroundTask)."""
    section = _mk_course_section(db_session)
    old_file = _mk_video(
        db_session, section.id, status="pending",
        created_at=_old(60),
    )

    with _mock_admin():
        resp = admin_client.post(
            "/api/admin/videos/recover-orphans", headers=_auth_headers()
        )

    data = resp.json()
    assert data["recovered"] == 0
    db_session.expire_all()
    assert db_session.get(Video, old_file.id).status == "pending"


def test_recover_orphans_requires_admin(paid_client, db_session):
    """PAID user → 403 (CURATE_CATALOG required)."""
    section = _mk_course_section(db_session)
    _mk_video(
        db_session, section.id, status="transcribing",
        created_at=_old(40), transcribe_started_at=_old(30),
    )
    fake_paid = {"uid": "user-A", "email": "a@x.com", "role": 1}
    with patch(
        "app.auth.dependencies.verify_token", return_value=fake_paid
    ):
        resp = paid_client.post(
            "/api/admin/videos/recover-orphans", headers=_auth_headers()
        )
    assert resp.status_code == 403


# ── 2. course page badge ──────────────────────────────────────────────────


def test_course_page_shows_orphan_button_for_admin(
    admin_client, db_session
):
    """Admin sees the orphan badge; the count matches the endpoint's
    15-min rule (fresh rows don't count)."""
    section = _mk_course_section(db_session)
    _mk_video(
        db_session, section.id, status="transcribing",
        created_at=_old(40), transcribe_started_at=_old(30),
    )
    # Fresh pending row — must NOT bump the badge
    _mk_video(
        db_session, section.id, status="pending",
        youtube_id="dQw4w9WgXcQ", created_at=_fresh(2),
    )

    with _mock_admin():
        resp = admin_client.get(
            f"/course/{section.course_id}", headers=_auth_headers()
        )
    assert resp.status_code == 200
    html = resp.text
    assert "recover-orphans" in html
    assert "1 orphaned" in html
    assert "2 orphaned" not in html


def test_course_page_hides_orphan_button_for_paid(paid_client, db_session):
    """Non-admins never see the button (endpoint is admin-only)."""
    section = _mk_course_section(db_session, uid="user-A")
    _mk_video(
        db_session, section.id, status="transcribing",
        created_at=_old(40), transcribe_started_at=_old(30),
    )
    fake_paid = {"uid": "user-A", "email": "a@x.com", "role": 1}
    with patch(
        "app.auth.dependencies.verify_token", return_value=fake_paid
    ):
        resp = paid_client.get(
            f"/course/{section.course_id}", headers=_auth_headers()
        )
    assert resp.status_code == 200
    # The BUTTON must not render (the shared JS block always ships,
    # so assert on the button's data attribute, not the fetch URL)
    assert "data-recover-orphans-btn" not in resp.text
    assert "orphaned — recover" not in resp.text


# ── 3. job-registration bugfixes ─────────────────────────────────────────


def test_staggered_transcribe_job_registers_job_before_running(db_session):
    """The queue's dispatch thread must register the transcribe job in
    the per-process tracker before calling _run_transcribe_job —
    otherwise a cross-worker claim silently no-ops (the 9/18 stuck
    LangChain video was the live casualty).

    Needs db_session: the dispatch opens its own SessionLocal and
    stamps the row's last_transcribe_job — tables must exist.
    """
    section = _mk_course_section(db_session)
    video = _mk_video(db_session, section.id, status="queued")

    from app.routers.courses import _staggered_transcribe_job
    from app.jobs import get_job

    with patch(
        "app.routers.videos._run_transcribe_job"
    ) as run_mock:
        _staggered_transcribe_job(video.id, 0)

    # The job EXISTS in the tracker before the worker runs
    job = get_job(video.id, "transcribe")
    assert job is not None
    assert job["status"] == "running"
    # 2026-09-21 (§4 fix): the dispatch passes the model STAMPED on the
    # row — _mk_video leaves whisper_model NULL → the smart default.
    from app.services.transcription import get_default_model_choice
    run_mock.assert_called_once_with(
        video.id, get_default_model_choice()
    )


def test_staggered_transcribe_job_uses_stamped_model(db_session):
    """REGRESSION (doc/known-issues-2026-09-21.md §4): the dispatch must
    use the whisper_model the upload STAMPED on the row, not a hardcoded
    'base'. Live impact: every queue upload since 9/16 ran CPU
    faster-whisper 'base' instead of MLX turbo (quality + speed loss;
    the 7-minute transcribe on a 47-min 4K video).
    """
    section = _mk_course_section(db_session)
    video = _mk_video(db_session, section.id, status="queued")
    video.whisper_model = "local-large-turbo"   # what a real upload stamps
    db_session.commit()

    from app.routers.courses import _staggered_transcribe_job

    with patch(
        "app.routers.videos._run_transcribe_job"
    ) as run_mock:
        _staggered_transcribe_job(video.id, 0)

    run_mock.assert_called_once_with(video.id, "local-large-turbo")


def test_requeue_staggered_pipeline_registers_job(db_session):
    """Same guarantee for the requeue-stuck path (its old version
    called _run_auto_pipeline with no registration → silent no-op).

    Needs db_session: the helper opens its own SessionLocal and
    flips the row's status — tables must exist.
    """
    section = _mk_course_section(db_session)
    video = _mk_video(db_session, section.id, status="error")

    from app.routers.admin import _requeue_staggered_pipeline
    from app.jobs import get_job

    with patch(
        "app.routers.videos._run_auto_pipeline"
    ) as pipeline_mock:
        _requeue_staggered_pipeline(video.id, 0)

    job = get_job(video.id, "transcribe")
    assert job is not None
    pipeline_mock.assert_called_once()


def test_requeue_staggered_pipeline_marks_row_transcribing(db_session):
    """The recovery path also flips the row to 'transcribing' so the
    UI shows progress and the queue accounting stays consistent."""
    section = _mk_course_section(db_session)
    video = _mk_video(
        db_session, section.id, status="error", title="requeue me"
    )

    from app.routers.admin import _requeue_staggered_pipeline

    with patch("app.routers.videos._run_auto_pipeline"):
        _requeue_staggered_pipeline(video.id, 0)

    db_session.expire_all()
    row = db_session.get(Video, video.id)
    assert row.status == "transcribing"
    assert "recovery" in (row.last_transcribe_job or "")