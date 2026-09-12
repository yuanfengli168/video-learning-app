"""Owner self-service stuck-video retry (2026-09-12).

POST /api/courses/{cid}/sections/{sid}/retry-stuck + the amber
"↻ Retry N stuck" button on the course page.

Why: uploads run their transcribe→generate pipeline in-memory; a
server restart kills it while the row still says queued (nothing
errors, nothing processes). The admin requeue-stuck endpoint (same
day) covers the app-wide blast radius; this one lets the COURSE
OWNER recover their own stuck videos per-section without contacting
admin — the escalation path for when the user count outgrows the
message-the-admin loop.

'Stuck' contract (UI and endpoint agree): status='queued' AND
created_at older than a 10-minute grace window. Inside the window
it's normal bulk-upload processing — a retry button there would
double-fire pipelines.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Course, Section, Video


def _mk_course_section(db: Session, uid: str = "user-A") -> Section:
    course = Course(title="Course", description="", user_id=uid)
    db.add(course)
    db.flush()
    section = Section(title="S", course_id=course.id, order_index=0)
    db.add(section)
    db.commit()
    return section


def _mk_video(
    db: Session, section_id: str, *, status: str = "queued",
    created_at: datetime | None = None, title: str = "v",
) -> Video:
    video = Video(
        title=title, filename="a.mp4", file_path="/tmp/a.mp4",
        file_size=1, duration=1.0, order_index=0,
        section_id=section_id, status=status, visibility=0,
        caption_languages="[]",
        created_at=created_at or datetime(2026, 9, 12, 12, 0, 0),
    )
    db.add(video)
    db.commit()
    return video


def _stuck_video(db: Session, section_id: str) -> Video:
    """A video that satisfies the stuck condition (queued, created
    >10 min ago)."""
    return _mk_video(
        db, section_id,
        created_at=datetime.utcnow() - timedelta(minutes=30),
    )


def _mock_owner():
    return patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": "user-A", "email": "a@x.com", "role": 1},
    )


# ── Endpoint ──────────────────────────────────────────────────────────────


def test_retry_stuck_requeues_old_queued(paid_client: TestClient, db_session):
    """Videos queued >10 min get re-queued (status flips to
    transcribing + a transcribe job starts)."""
    section = _mk_course_section(db_session)
    stuck = _stuck_video(db_session, section.id)

    with patch("app.routers.courses._staggered_transcribe_job") as job, \
         _mock_owner():
        resp = paid_client.post(
            f"/api/courses/{section.course_id}/sections/{section.id}/retry-stuck"
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["retried"] == 1
    assert data["video_ids"] == [stuck.id]
    job.assert_called_once()
    # Stagger for the FIRST video is 0s
    assert job.call_args[0][1] == 0

    db_session.expire_all()
    assert db_session.get(Video, stuck.id).status == "transcribing"


def test_retry_stuck_grace_window_excludes_fresh(paid_client: TestClient, db_session):
    """Videos queued <10 min (normal upload processing) are NOT
    re-queued — the button must not double-fire live pipelines."""
    section = _mk_course_section(db_session)
    fresh = _mk_video(
        db_session, section.id,
        created_at=datetime.utcnow() - timedelta(minutes=2),
    )

    with patch("app.routers.courses._staggered_transcribe_job") as job, \
         _mock_owner():
        resp = paid_client.post(
            f"/api/courses/{section.course_id}/sections/{section.id}/retry-stuck"
        )

    assert resp.status_code == 200
    assert resp.json()["retried"] == 0
    job.assert_not_called()
    db_session.expire_all()
    assert db_session.get(Video, fresh.id).status == "queued"


def test_retry_stuck_ignores_other_statuses(paid_client: TestClient, db_session):
    """ready/error/transcribing rows are never touched."""
    section = _mk_course_section(db_session)
    _mk_video(db_session, section.id, status="ready",
              created_at=datetime.utcnow() - timedelta(hours=2))
    _mk_video(db_session, section.id, status="error",
              created_at=datetime.utcnow() - timedelta(hours=2))
    _mk_video(db_session, section.id, status="transcribing",
              created_at=datetime.utcnow() - timedelta(hours=2))

    with patch("app.routers.courses._staggered_transcribe_job") as job, \
         _mock_owner():
        resp = paid_client.post(
            f"/api/courses/{section.course_id}/sections/{section.id}/retry-stuck"
        )

    assert resp.status_code == 200
    assert resp.json()["retried"] == 0
    job.assert_not_called()


def test_retry_stuck_ownership_403(paid_client: TestClient, db_session):
    """Not the course owner → 403 (only your OWN stuck videos)."""
    section = _mk_course_section(db_session, uid="someone-else")
    _stuck_video(db_session, section.id)

    with _mock_owner():
        resp = paid_client.post(
            f"/api/courses/{section.course_id}/sections/{section.id}/retry-stuck"
        )
    assert resp.status_code == 403


def test_retry_stuck_wrong_section_404(paid_client: TestClient, db_session):
    """Section not under this course → 404 (no existence leak)."""
    section = _mk_course_section(db_session)
    other_course = Course(title="Other", user_id="user-A")
    db_session.add(other_course)
    db_session.commit()

    with _mock_owner():
        resp = paid_client.post(
            f"/api/courses/{other_course.id}/sections/{section.id}/retry-stuck"
        )
    assert resp.status_code == 404


def test_retry_stuck_scoped_to_section(paid_client: TestClient, db_session):
    """Stuck videos in a DIFFERENT section of the same course are not
    re-queued — the endpoint is per-section by design."""
    s1 = _mk_course_section(db_session)
    s2 = Section(title="S2", course_id=s1.course_id, order_index=1)
    db_session.add(s2)
    db_session.commit()
    _stuck_video(db_session, s2.id)

    with patch("app.routers.courses._staggered_transcribe_job") as job, \
         _mock_owner():
        resp = paid_client.post(
            f"/api/courses/{s1.course_id}/sections/{s1.id}/retry-stuck"
        )

    assert resp.status_code == 200
    assert resp.json()["retried"] == 0
    job.assert_not_called()


# ── The transcribe→generate CHAIN (2026-09-12 bugfix) ─────────────────────
#
# User report: retry button worked but the course page showed bare
# 'ready' with no T:/G: timing — and no materials. Root cause: the
# retry workers called only _run_transcribe_job, which does NOT chain
# generation (that lives in _run_auto_pipeline). Both retry paths
# (retry-stuck AND the pre-existing retry-failed transcribe bucket)
# now schedule _staggered_transcribe_job, which runs transcribe then
# generate. These tests pin the chain.


def test_staggered_transcribe_job_chains_generate(db_session):
    """After a successful transcribe, the chain must flip the video to
    'generating', start the generate job, and call _run_generate_job
    with the owner's uid + role."""
    from unittest.mock import patch as _patch

    section = _mk_course_section(db_session)
    video = _stuck_video(db_session, section.id)
    # Owner row with a role so the chain's owner lookup resolves it
    from app.models import User

    db_session.add(User(user_id="user-A", email="a@x.com", role=1))
    db_session.commit()

    from app.jobs import get_job, start_job

    # Simulate the request-side start_job that the endpoint already ran
    start_job(video.id, "transcribe", message="queued")

    from app.routers import courses as courses_mod

    with _patch("app.routers.videos._run_transcribe_job") as fake_transcribe, \
         _patch("app.routers.generation._run_generate_job") as fake_generate:
        # Make the transcribe "succeed": no-op + mark the in-memory job
        # completed, mirroring the real worker's finish_job call.
        def _transcribe_succeeds(vid, model):
            job = get_job(vid, "transcribe")
            from app.jobs import finish_job

            finish_job(job, status="completed", message="ok")
        fake_transcribe.side_effect = _transcribe_succeeds

        courses_mod._staggered_transcribe_job(video.id, 0)

    fake_transcribe.assert_called_once()
    fake_generate.assert_called_once()
    # Owner uid + role forwarded (the MVP2.1 silent-TypeError lesson)
    assert fake_generate.call_args[0][0] == video.id
    assert fake_generate.call_args[0][1] == "user-A"
    assert fake_generate.call_args[0][2] == 1

    db_session.expire_all()
    v = db_session.get(Video, video.id)
    assert v.status == "generating"
    assert v.last_generate_job is not None


def test_staggered_transcribe_job_no_chain_on_failure(db_session):
    """If transcribe FAILS, the chain must NOT call generate — the
    video stays 'error' (the user's retry-failed button handles the
    next attempt)."""
    from unittest.mock import patch as _patch

    section = _mk_course_section(db_session)
    video = _stuck_video(db_session, section.id)

    from app.jobs import get_job, start_job

    start_job(video.id, "transcribe", message="queued")

    from app.routers import courses as courses_mod

    with _patch("app.routers.videos._run_transcribe_job") as fake_transcribe, \
         _patch("app.routers.generation._run_generate_job") as fake_generate:
        def _transcribe_fails(vid, model):
            job = get_job(vid, "transcribe")
            from app.jobs import finish_job

            finish_job(job, status="failed", error="boom")
        fake_transcribe.side_effect = _transcribe_fails

        courses_mod._staggered_transcribe_job(video.id, 0)

    fake_transcribe.assert_called_once()
    fake_generate.assert_not_called()


def test_retry_failed_transcribe_bucket_uses_chained_worker(
    paid_client: TestClient, db_session
):
    """The PRE-EXISTING retry-failed endpoint's transcribe bucket had
    the same missing-chain bug (its docstring wrongly claimed
    _run_transcribe_job chains generation). It must now schedule the
    chained worker."""
    import json as _json

    section = _mk_course_section(db_session)
    video = _stuck_video(db_session, section.id)
    # Seed a FAILED transcribe job so the endpoint's transcribe
    # bucket finds it.
    failed_job = _json.dumps({
        "video_id": video.id, "job_type": "transcribe",
        "status": "failed", "message": "boom",
    })
    video.last_transcribe_job = failed_job
    video.status = "error"
    db_session.commit()

    with patch("app.routers.courses._staggered_transcribe_job") as chained, \
         _mock_owner():
        resp = paid_client.post(
            f"/api/courses/{section.course_id}/sections/{section.id}/retry-failed"
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["transcribe_retried"] == 1
    chained.assert_called_once()
    assert chained.call_args[0][0] == video.id


# ── UI rendering ──────────────────────────────────────────────────────────


def test_course_page_shows_owner_stuck_button(paid_client: TestClient, db_session):
    """Owner (manage_own_course) sees the amber ↻ button when the
    section has stuck videos (>10 min grace)."""
    section = _mk_course_section(db_session)
    _stuck_video(db_session, section.id)

    with _mock_owner():
        resp = paid_client.get(f"/course/{section.course_id}")

    assert resp.status_code == 200
    html = resp.text
    assert "retryStuckVideos(" in html, "button should render for owner"
    assert "Retry 1 stuck" in html
    assert "function retryStuckVideos" in html


def test_course_page_hides_button_inside_grace(paid_client: TestClient, db_session):
    """Fresh uploads (inside the 10-min window) show NO retry button —
    they're just processing normally."""
    section = _mk_course_section(db_session)
    _mk_video(
        db_session, section.id,
        created_at=datetime.utcnow() - timedelta(minutes=2),
    )

    with _mock_owner():
        resp = paid_client.get(f"/course/{section.course_id}")

    assert resp.status_code == 200
    # Target the button markup (the JS function always ships —
    # harmless without a caller, same convention as rename/sort).
    assert "retryStuckVideos('" not in resp.text, (
        "no button while uploads are legitimately processing"
    )
    assert "stuck</button>" not in resp.text


def test_course_page_hides_button_from_visitor(paid_client: TestClient, db_session):
    """Non-owner PAID viewers don't see the button (endpoint 403s
    anyway — pinned above)."""
    section = _mk_course_section(db_session, uid="someone-else")
    _stuck_video(db_session, section.id)

    with _mock_owner():
        resp = paid_client.get(f"/course/{section.course_id}")

    assert resp.status_code == 200
    assert "retryStuckVideos('" not in resp.text, "non-owner: no button markup"
    assert "Retry 1 stuck" not in resp.text


def test_admin_button_and_owner_button_can_coexist(
    admin_client: TestClient, db_session
):
    """An admin viewing their own stuck course sees BOTH buttons —
    they do different things (app-wide vs this-section) and the
    labels distinguish them."""
    section = _mk_course_section(db_session, uid="uid-admin")
    _stuck_video(db_session, section.id)

    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "uid-admin", "email": "a@x.com", "role": 0}):
        resp = admin_client.get(f"/course/{section.course_id}")

    assert resp.status_code == 200
    html = resp.text
    assert "queued — re-queue" in html, "admin app-wide button"
    assert "Retry 1 stuck" in html, "owner this-section button"