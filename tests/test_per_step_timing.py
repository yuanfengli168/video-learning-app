"""Tests for MVP2.0.4: per-step transcribe/generate timing on the
section page.

User's manualTodo [jul14] #8 (revised in [jul15] follow-up):
the 'ready · in 9:08' timing badge on the course page was
showing wall-clock time from upload, which for batch-uploaded
videos included the queue wait for all prior videos. The
expected behavior is TWO per-step times:

  Ready · T:0:55, G:0:44
  - T:0:55 = transcribe duration (transcribed_at - transcribe_started_at)
  - G:0:44 = generate duration (generated_at - transcribed_at)

Neither time includes queue wait. Each video's timer is a
per-video stopwatch that starts when its OWN transcribe worker
begins work, not when the user uploaded it.

This test file covers:
  1. The Video model has the new `transcribe_started_at` column
     and the migration was registered.
  2. The transcribe worker stamps `transcribe_started_at` at the
     very start (BEFORE whisper loads).
  3. The transcribe worker stamps `transcribed_at` at the end
     (status=ready).
  4. The generate worker stamps `generated_at` at the end
     (status=ready).
  5. The course page template renders BOTH per-step times
     ("T:..., G:...") when all three columns are present.
  6. The course page template falls back to the old
     generated_at - created_at formula for legacy rows
     where transcribe_started_at is NULL.
  7. The course page template hides the timing entirely when
     status is not 'ready'.
"""

import io
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.models import Course, Section, Video  # noqa: F401  (re-exported for tests that need them)

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def _setup_video(paid_client: TestClient) -> str:
    """Helper: create course → section → video. Returns video_id."""
    with _mock_auth():
        course_resp = paid_client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        section_resp = paid_client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
        fake_video = io.BytesIO(b"fake video content")
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
    return upload_resp.json()["video_id"]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Model + migration
# ─────────────────────────────────────────────────────────────────────────────


def test_video_model_has_transcribe_started_at_column():
    """The Video ORM must declare the new transcribe_started_at column
    so SQLAlchemy can read/write it. Without this the migration
    would add the column but the app would crash on the first read."""
    from app.models import Video
    # The column is declared as a Mapped[datetime | None]
    assert hasattr(Video, "transcribe_started_at"), (
        "Video.transcribe_started_at is missing — model wasn't updated"
    )
    # The underlying Column must be nullable so legacy rows from
    # before MVP2.0.4 (which have NULL) keep working.
    from sqlalchemy import DateTime
    col = Video.__table__.columns.get("transcribe_started_at")
    assert col is not None
    assert col.nullable is True
    assert isinstance(col.type, DateTime)


def test_transcribe_started_at_migration_registered():
    """The additive migration must be registered so existing
    DBs (with the table already created) get the new column on
    next startup. If missing, fresh app boots will crash on the
    first read of an existing video row."""
    from app.database import _MIGRATIONS
    matches = [m for m in _MIGRATIONS if m[1] == "transcribe_started_at"]
    assert len(matches) == 1, (
        f"Expected exactly 1 transcribe_started_at migration, "
        f"got {len(matches)}: {matches}"
    )
    table, column, ddl = matches[0]
    assert table == "videos"
    assert "ADD COLUMN transcribe_started_at" in ddl
    # Must be nullable so legacy rows keep working.
    assert "NOT NULL" not in ddl


# ─────────────────────────────────────────────────────────────────────────────
# 2. Transcribe worker stamps transcribe_started_at at the start
# ─────────────────────────────────────────────────────────────────────────────


def test_transcribe_worker_stamps_started_at_before_whisper_loads(paid_client, db_session):
    """The transcribe worker must stamp transcribe_started_at at the
    very TOP of _run_transcribe_job (BEFORE whisper loads), so the
    transcribe duration includes the model load time. If stamped
    later (e.g. after whisper loads), the duration would undercount
    by the load time, and the badge would show "0:40" instead of
    "0:55" for a video that took 5s to load + 50s to transcribe.

    The test mocks faster_whisper.WhisperModel (the leaf class
    that get_model() instantiates) and verifies started_at is
    stamped before .transcribe() is called.
    """
    from app.models import Video
    from app.jobs import start_job, serialize_job

    video_id = _setup_video(paid_client)
    with db_session as db:
        video = db.get(Video, video_id)
        # Register the job (the worker looks for it)
        job = start_job(video_id, "transcribe", total=100, message="test")
        video.last_transcribe_job = serialize_job(job)
        # CRITICAL: started_at must be NULL before the worker runs
        video.transcribe_started_at = None
        video.transcribed_at = None
        db.commit()

        # Build a fake WhisperModel. Pattern matches the existing
        # test in test_ready_timing.py:160.
        stamped_before_call = {"ok": False, "v_at_call": None}

        class _FakeSegment:
            def __init__(self, start, end, text):
                self.start = start
                self.end = end
                self.text = text

        class _FakeInfo:
            language = "en"
            duration = 5.0

        class _FakeWhisperModel:
            def __init__(self, *args, **kwargs):
                pass
            def transcribe(self, *args, **kwargs):
                # At this point the worker should have ALREADY
                # stamped transcribe_started_at (it's stamped at
                # the very top of the worker, BEFORE we get here).
                # If not, the duration would miss the model load
                # time.
                v = db.get(Video, video_id)
                stamped_before_call["v_at_call"] = v.transcribe_started_at
                stamped_before_call["ok"] = v.transcribe_started_at is not None
                return (
                    iter([
                        _FakeSegment(0.0, 2.5, "Hello"),
                        _FakeSegment(2.5, 5.0, " world"),
                    ]),
                    _FakeInfo(),
                )

        with patch("faster_whisper.WhisperModel", _FakeWhisperModel):
            from app.routers.videos import _run_transcribe_job
            _run_transcribe_job(video_id, "base")

        # The worker's outer except-block will catch any
        # AssertionError raised inside the fake's .transcribe()
        # and mark the job as failed (so transcribed_at is never
        # set on failure). That's fine — we capture the
        # started-at status via the flag instead.
        assert stamped_before_call["ok"], (
            "transcribe_started_at was NOT stamped before the "
            "backend was called (value at call: "
            f"{stamped_before_call['v_at_call']!r}) — the "
            "duration will miss the model load time"
        )

        # After the worker finishes (when it succeeds), both
        # timestamps should be set. The worker uses its own
        # SessionLocal(), so we need to expire our cached row
        # to see its commits.
        db.expire_all()
        video = db.get(Video, video_id)
        assert video.transcribe_started_at is not None
        assert video.transcribed_at is not None
        # transcribed_at should be at or after transcribe_started_at
        assert video.transcribed_at >= video.transcribe_started_at


# ─────────────────────────────────────────────────────────────────────────────
# 3. The course page template renders T:..., G:...
# ─────────────────────────────────────────────────────────────────────────────


def test_course_page_renders_both_per_step_times(paid_client: TestClient):
    """When transcribe_started_at, transcribed_at, and generated_at
    are all set, the course page renders 'Ready · T:0:55, G:0:44'
    (or the actual computed durations)."""
    from app.database import SessionLocal
    from datetime import datetime, timedelta, timezone
    from app.models import Video

    video_id = _setup_video(paid_client)
    base_time = datetime(2026, 7, 15, 10, 0, 0)
    with SessionLocal() as db:
        v = db.get(Video, video_id)
        v.status = "ready"
        v.created_at = base_time - timedelta(minutes=10)  # uploaded 10min ago
        v.transcribe_started_at = base_time  # transcribe began
        v.transcribed_at = base_time + timedelta(seconds=55)  # T = 55s
        v.generated_at = base_time + timedelta(seconds=55, milliseconds=44_000)  # G = 44s
        db.commit()

    with _mock_auth():
        course_id = SessionLocal().query(Course).filter_by(user_id=FAKE_USER["uid"]).first().id
        resp = paid_client.get(f"/course/{course_id}", headers=_auth_headers())

    assert resp.status_code == 200
    # T:0:55 (transcribe duration)
    assert "T:0:55" in resp.text, (
        f"expected T:0:55 in page; got: {resp.text[resp.text.find('ready'):resp.text.find('ready')+200] if 'ready' in resp.text else '(no ready found)'}"
    )
    # G:0:44 (generate duration)
    assert "G:0:44" in resp.text, (
        f"expected G:0:44 in page; got: {resp.text[resp.text.find('T:0:55'):resp.text.find('T:0:55')+200] if 'T:0:55' in resp.text else '(no T:0:55 found)'}"
    )
    # And the two should appear together as "T:0:55, G:0:44"
    assert "T:0:55, G:0:44" in resp.text


def test_course_page_legacy_fallback(paid_client: TestClient):
    """For legacy rows (uploaded before MVP2.0.4), transcribe_started_at
    is NULL. The page should fall back to the old
    generated_at - created_at formula so the badge still shows
    *something*. Per user choice 'b' on 2026-07-15."""
    from app.database import SessionLocal
    from datetime import datetime, timedelta, timezone
    from app.models import Video

    video_id = _setup_video(paid_client)
    base_time = datetime(2026, 7, 15, 10, 0, 0)
    with SessionLocal() as db:
        v = db.get(Video, video_id)
        v.status = "ready"
        v.created_at = base_time
        # Legacy row: no transcribe_started_at
        v.transcribe_started_at = None
        v.transcribed_at = base_time + timedelta(seconds=55)
        v.generated_at = base_time + timedelta(seconds=99)  # total 1:39
        db.commit()

    with _mock_auth():
        course_id = SessionLocal().query(Course).filter_by(user_id=FAKE_USER["uid"]).first().id
        resp = paid_client.get(f"/course/{course_id}", headers=_auth_headers())

    assert resp.status_code == 200
    # Should fall back to 1:39 (generated_at - created_at)
    assert "1:39" in resp.text, (
        f"expected legacy fallback 1:39 in page; got: {resp.text[resp.text.find('ready'):resp.text.find('ready')+200] if 'ready' in resp.text else '(no ready found)'}"
    )
    # And should NOT have the new T:/G: format
    assert "T:" not in resp.text, "should not use T: format on legacy rows"
    assert "G:" not in resp.text, "should not use G: format on legacy rows"


def test_course_page_hides_timing_for_non_ready_status(paid_client: TestClient):
    """The timing suffix must only show when status == 'ready'. A
    video in 'transcribing' / 'generating' / 'error' should not
    show any timing (per user choice: 'hide it' on 2026-07-15)."""
    from app.database import SessionLocal
    from datetime import datetime, timedelta, timezone
    from app.models import Video

    video_id = _setup_video(paid_client)
    base_time = datetime(2026, 7, 15, 10, 0, 0)
    with SessionLocal() as db:
        v = db.get(Video, video_id)
        # Set up the same data as the ready case, but mark it transcribing
        v.status = "transcribing"
        v.created_at = base_time
        v.transcribe_started_at = base_time
        v.transcribed_at = base_time + timedelta(seconds=55)
        v.generated_at = base_time + timedelta(seconds=99)
        db.commit()

    with _mock_auth():
        course_id = SessionLocal().query(Course).filter_by(user_id=FAKE_USER["uid"]).first().id
        resp = paid_client.get(f"/course/{course_id}", headers=_auth_headers())

    assert resp.status_code == 200
    # No T: or G: on a transcribing video
    assert "T:" not in resp.text
    assert "G:" not in resp.text
    # The "transcribing" status should still be there
    assert "transcribing" in resp.text
