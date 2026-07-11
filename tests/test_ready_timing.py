"""Tests for MVP3.0 #8: 'ready · in 9:08' timing on the section page.

User's manual todo [jul11] #8: "can we add a small feature on how many
hours, and minutes a pending finally turned to ready when we open the
section, like beside ready - done in 09:08 means from generating to
ready, it used 9 minutes and 8 seconds."

This test file covers four layers:
  1. The model has the new `transcribed_at` + `generated_at` columns
     and the migration was registered.
  2. The transcribe worker sets `transcribed_at` on success and
     does NOT set it on failure.
  3. The generate worker sets `generated_at` on success and
     does NOT set it on failure.
  4. The course page template renders the timing as
     "ready · M:SS" (or "ready · H:MM:SS" for > 1 hour) when both
     `created_at` and `generated_at` are present, and omits the
     timing for legacy videos (no `generated_at`).
"""

import io
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

# NOTE: we import SessionLocal lazily inside each test (not at module
# level) because conftest.py's db_session fixture monkey-patches
# `app.database.SessionLocal` and the per-router `SessionLocal`
# references. Importing at module level would capture the production
# reference, which points at the on-disk video_learning.db instead of
# the per-test in-memory DB, and every DB write in the test would
# land in the wrong place. See conftest.py:db_session for the patch.
from app.models import Asset, Course, Section, Video  # noqa: F401  (re-exported for tests that need them)

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    from unittest.mock import patch
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def _create_course_and_section(client: TestClient):
    """Helper: create a course + section, return (course_id, section_id)."""
    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "Timing tests"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "S1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
    return course_id, section_id


def _upload_video(client: TestClient, section_id: str) -> str:
    """Helper: upload a video, return its id."""
    with _mock_auth():
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", io.BytesIO(b"x" * 100), "video/mp4")},
            headers=_auth_headers(),
        )
    return upload_resp.json()["video_id"]


def _get_session_local():
    """Return the SessionLocal class that the conftest fixture
    monkey-patched. Must be called inside the test (not at import time)
    so the patch is already in effect.
    """
    from app.database import SessionLocal
    return SessionLocal


# ─────────────────────────────────────────────────────────────────────────────
# 1. Model + migration
# ─────────────────────────────────────────────────────────────────────────────


def test_video_model_has_completion_timestamp_columns():
    """Video model must expose transcribed_at + generated_at (MVP3.0 #8)."""
    from app.models.video import Video
    cols = {c.name for c in Video.__table__.columns}
    assert "transcribed_at" in cols, "Video model missing transcribed_at column"
    assert "generated_at" in cols, "Video model missing generated_at column"


def test_completion_timestamps_are_nullable():
    """Both columns must be nullable so legacy rows (uploaded before
    MVP3.0) and videos still in flight don't violate NOT NULL."""
    from app.models.video import Video
    for col_name in ("transcribed_at", "generated_at"):
        col = Video.__table__.columns[col_name]
        assert col.nullable is True, (
            f"{col_name} should be nullable for legacy rows, got nullable={col.nullable}"
        )


def test_completion_timestamps_migration_is_registered():
    """The migration must be in _MIGRATIONS so the columns get added on
    startup. If someone removes the migration entry, the next deploy
    would crash on first /status poll."""
    from app.database import _MIGRATIONS
    migrations = {(t, c) for t, c, _ in _MIGRATIONS}
    assert ("videos", "transcribed_at") in migrations, (
        "videos.transcribed_at migration missing from _MIGRATIONS — "
        "legacy databases will not get the column on next startup"
    )
    assert ("videos", "generated_at") in migrations, (
        "videos.generated_at migration missing from _MIGRATIONS"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Transcribe worker
# ─────────────────────────────────────────────────────────────────────────────


def test_transcribe_worker_sets_transcribed_at_on_success(client: TestClient):
    """_run_transcribe_job must set transcribed_at when it succeeds.

    The worker uses `from faster_whisper import WhisperModel` *inside*
    the function body (line 367 of app/routers/videos.py), so we
    patch the source location `faster_whisper.WhisperModel` — that's
    the only way to intercept the real worker's Whisper call without
    also patching out the whole worker.
    """
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)

    # Build a fake WhisperModel whose .transcribe() returns our
    # canned segments + a DurationInfo with the expected fields.
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

    with _get_session_local()() as db:
        v = db.get(Video, video_id)
        assert v.transcribed_at is not None, (
            "transcribe worker did not set transcribed_at on success"
        )
        # Must be a datetime, not a string
        assert isinstance(v.transcribed_at, datetime)
        # Sanity: it's recent (within the last 10 seconds). The worker
        # stores naive UTC (matching the created_at convention), so
        # compare against naive UTC — using datetime.now() would mix
        # local-time and UTC and look 8 hours off in non-UTC zones.
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        assert (now_utc - v.transcribed_at) < timedelta(seconds=10)
        # The transcript asset must also be there (sanity check that
        # the worker actually ran end-to-end, not just skipped to
        # the timestamp line).
        assert v.status == "ready"


def test_transcribe_worker_does_not_set_transcribed_at_on_failure(client: TestClient):
    """If the worker raises, transcribed_at must stay None (don't
    stamp a timestamp for a job that never actually completed)."""
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)

    class _BrokenWhisperModel:
        def __init__(self, *args, **kwargs):
            pass
        def transcribe(self, *args, **kwargs):
            raise RuntimeError("Whisper crashed")

    with patch("faster_whisper.WhisperModel", _BrokenWhisperModel):
        from app.routers.videos import _run_transcribe_job
        _run_transcribe_job(video_id, "base")

    with _get_session_local()() as db:
        v = db.get(Video, video_id)
        assert v.transcribed_at is None, (
            "transcribe worker set transcribed_at despite the job failing — "
            "user would see a 'ready · in 9:08' label on an errored video"
        )
        assert v.status == "error"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Generate worker
# ─────────────────────────────────────────────────────────────────────────────


def _seed_ready_transcript(client: TestClient, video_id: str) -> None:
    """Helper: pre-write a transcript asset, set video.status=ready, AND
    start the generate job tracker so _run_generate_job has a job to find.

    The real auto-pipeline starts the generate job in
    `_run_auto_pipeline` (videos.py) BEFORE calling the generate worker.
    If we don't do the same here, the worker returns early at
    `if not job: return` and the test never exercises the code path
    we care about.
    """
    from app.services.transcription import transcript_to_json
    from app.jobs import start_job, serialize_job
    fake_transcript = {
        "segments": [{"start": 0.0, "end": 5.0, "text": "Hello"}],
        "language": "en",
        "duration": 5.0,
    }
    with _get_session_local()() as db:
        v = db.get(Video, video_id)
        db.add(Asset(
            id=f"t-{video_id[:8]}",
            video_id=video_id,
            asset_type="transcript",
            content=transcript_to_json(fake_transcript),
        ))
        v.status = "ready"
        v.duration = 5.0
        # Start the generate job (mirrors what _run_auto_pipeline does)
        gen_job = start_job(video_id, "generate", total=100, message="seed")
        v.last_generate_job = serialize_job(gen_job)
        db.commit()


def test_generate_worker_sets_generated_at_on_success(client: TestClient):
    """_run_generate_job must set generated_at when it succeeds."""
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)
    _seed_ready_transcript(client, video_id)

    fake_materials = {
        "summary": "# Summary",
        "mindmap": "# Topic",
        "flashcards": [],
        "quiz": [],
        "topic_timestamps": [],
    }

    with patch(
        "app.routers.generation.generate_materials",
        return_value=fake_materials,
    ):
        from app.routers.generation import _run_generate_job
        _run_generate_job(video_id)

    with _get_session_local()() as db:
        v = db.get(Video, video_id)
        assert v.generated_at is not None, (
            "generate worker did not set generated_at on success"
        )
        assert isinstance(v.generated_at, datetime)
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        assert (now_utc - v.generated_at) < timedelta(seconds=10)
        assert v.status == "ready"


def test_generate_worker_does_not_set_generated_at_on_failure(client: TestClient):
    """If the LLM call raises, generated_at must stay None."""
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)
    _seed_ready_transcript(client, video_id)

    def fake_broken(*args, **kwargs):
        raise RuntimeError("LLM crashed")

    with patch(
        "app.routers.generation.generate_materials",
        side_effect=fake_broken,
    ):
        from app.routers.generation import _run_generate_job
        _run_generate_job(video_id)

    with _get_session_local()() as db:
        v = db.get(Video, video_id)
        assert v.generated_at is None, (
            "generate worker set generated_at despite the job failing"
        )
        assert v.status == "error"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Template rendering on the section page
# ─────────────────────────────────────────────────────────────────────────────


def test_course_page_shows_ready_with_timing_when_generated_at_set(client: TestClient):
    """A video with both created_at and generated_at set must render
    as 'ready · M:SS' (or H:MM:SS) on the section page."""
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)

    # Stamp the timestamps directly so we don't need to run the workers.
    with _get_session_local()() as db:
        v = db.get(Video, video_id)
        v.status = "ready"
        v.created_at = datetime(2026, 7, 11, 10, 0, 0)
        v.transcribed_at = datetime(2026, 7, 11, 10, 4, 30)  # 4:30
        v.generated_at = datetime(2026, 7, 11, 10, 9, 8)    # 9:08 total
        db.commit()

    with _mock_auth():
        resp = client.get(f"/course/{course_id}", headers=_auth_headers())
    assert resp.status_code == 200
    # The badge text is "ready · 9:08"
    assert "ready · 9:08" in resp.text, (
        f"course page should render 'ready · 9:08' for a video that "
        f"took 9 min 8 sec from created_at to generated_at. "
        f"Got snippet: {resp.text[resp.text.find('ready'):resp.text.find('ready')+200]!r}"
    )


def test_course_page_shows_ready_with_hours_when_over_60_minutes(client: TestClient):
    """A video that took > 1 hour should render as H:MM:SS, not just M:SS."""
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)

    with _get_session_local()() as db:
        v = db.get(Video, video_id)
        v.status = "ready"
        v.created_at = datetime(2026, 7, 11, 10, 0, 0)
        v.generated_at = datetime(2026, 7, 11, 12, 5, 33)  # 2:05:33
        db.commit()

    with _mock_auth():
        resp = client.get(f"/course/{course_id}", headers=_auth_headers())
    assert resp.status_code == 200
    assert "ready · 2:05:33" in resp.text, (
        "videos that took > 1 hour should render as H:MM:SS, not M:SS"
    )


def test_course_page_omits_timing_for_legacy_videos_without_generated_at(client: TestClient):
    """A video that was uploaded before MVP3.0 has no generated_at
    column populated. The badge must still show 'ready', just without
    the timing suffix — otherwise legacy videos would render as
    'ready · 0:00' which is misleading."""
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)

    with _get_session_local()() as db:
        v = db.get(Video, video_id)
        v.status = "ready"
        v.generated_at = None  # legacy: never set
        db.commit()

    with _mock_auth():
        resp = client.get(f"/course/{course_id}", headers=_auth_headers())
    assert resp.status_code == 200
    # Find the badge for this video
    # The badge text is rendered inside a <span class="text-xs px-2 py-1 rounded ...">
    # We just check that "ready" appears but "ready · 0" does NOT.
    assert "ready" in resp.text
    # The "·" separator should NOT appear on this video's badge.
    # Find the section containing our video and look at its badge
    # (we only have one video, so the pattern is unique enough).
    # Easy check: no occurrence of "ready · 0:00" anywhere on the page.
    assert "ready · 0:00" not in resp.text, (
        "legacy videos (no generated_at) must NOT show 'ready · 0:00' — "
        "they should just show 'ready'"
    )


def test_course_page_omits_timing_for_non_ready_statuses(client: TestClient):
    """Videos in transcribing / generating / error / pending must show
    the status as today, without any timing suffix — the timing only
    applies to fully-ready videos."""
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)

    with _get_session_local()() as db:
        v = db.get(Video, video_id)
        v.status = "transcribing"
        v.transcribed_at = datetime.now()
        v.generated_at = datetime.now()  # even if set, only show for status=ready
        db.commit()

    with _mock_auth():
        resp = client.get(f"/course/{course_id}", headers=_auth_headers())
    assert resp.status_code == 200
    # The transcribing badge must show "transcribing", not "transcribing · X:XX"
    assert "transcribing" in resp.text
    # No "transcribing ·" with a timing suffix
    assert "transcribing ·" not in resp.text, (
        "timing suffix should only show on 'ready' status"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. format_duration Jinja filter (used by the template)
# ─────────────────────────────────────────────────────────────────────────────


def test_format_duration_filter_units():
    """The format_duration Jinja filter must produce the right
    M:SS / H:MM:SS strings for every plausible duration.

    This is a unit test (no DB, no template render) so it runs in
    microseconds and covers edge cases that the integration tests
    above don't hit.
    """
    from app.routers.frontend import _format_duration_filter as fmt
    # Sub-minute durations → 0:SS
    assert fmt(0) == "0:00"
    assert fmt(5) == "0:05"
    assert fmt(59) == "0:59"
    # Sub-hour → M:SS
    assert fmt(60) == "1:00"
    assert fmt(90) == "1:30"
    assert fmt(9 * 60 + 8) == "9:08"  # the user's example
    assert fmt(59 * 60 + 59) == "59:59"
    # Hour+ → H:MM:SS
    assert fmt(3600) == "1:00:00"
    assert fmt(2 * 3600 + 5 * 60 + 33) == "2:05:33"  # another test case
    assert fmt(10 * 3600) == "10:00:00"
    # Floats get floored
    assert fmt(9.9) == "0:09"
    # Edge: negative / None → empty string (caller checks for None)
    assert fmt(None) == ""
    assert fmt(-1) == ""


def test_format_duration_filter_registered_in_jinja_env():
    """The filter must be registered on the templates.env so the
    template can call it. If someone removes the registration line in
    app/routers/frontend.py, the template would render as
    'ready · ' (empty) and the user would lose the timing entirely."""
    from app.routers.frontend import templates
    assert "format_duration" in templates.env.filters, (
        "format_duration filter missing from templates.env.filters — "
        "course.html would render the timing as empty"
    )
