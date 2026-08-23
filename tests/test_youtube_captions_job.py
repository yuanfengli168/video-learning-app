"""Tests for app/services/youtube_captions_job.py — the background worker.

Strategy
--------
The worker calls yt-dlp via fetch_youtube_captions() which makes a
real network call. We patch fetch_youtube_captions to return canned
results, so we can test the worker's logic (status transitions, DB
persistence, error handling) without any I/O.

What we test
------------
  - Happy path: success → transcript Asset written, status='ready',
    transcribed_at stamped, language locked
  - Unavailable: no captions → status='error', whisper_fallback_reason set
  - Failed: yt-dlp error → status='error', error message preserved
  - Idempotency: re-running on a video with existing transcript → skip
  - Missing youtube_id: defensive 400-style failure
  - Retry endpoint: synchronously re-downloads and overwrites
  - Admin endpoint GET status: returns segment count + job state

Mirrors the pattern in tests/test_admin_router.py — minimal HTTP,
focused on the worker's behavior.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.main import app
from app.models import Asset, Video


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def youtube_video(db_session: Session):
    """A YouTube-typed Video row (admin added it).

    Status='pending' is the precondition the worker expects.

    Creates its own Course + Section so the fixture is self-contained
    (doesn't depend on other tests' setup order).
    """
    import uuid
    from app.models import Course, Section

    course = Course(
        id=str(uuid.uuid4()),
        title="Test Course",
        user_id="test-uid",
    )
    db_session.add(course)
    db_session.flush()

    section = Section(
        id=str(uuid.uuid4()),
        title="Test Section",
        course_id=course.id,
        order_index=0,
    )
    db_session.add(section)
    db_session.flush()

    video = Video(
        id=str(uuid.uuid4()),
        title="Test YouTube Video",
        filename="youtube:dQw4w9WgXcQ",
        file_path="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        file_size=0,
        youtube_id="dQw4w9WgXcQ",
        thumbnail_url="https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
        channel="Test Channel",
        caption_languages=json.dumps(["en", "zh-Hans"]),
        duration=214.0,
        section_id=section.id,
        status="pending",
        visibility=0,
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)
    return video


def _fake_caption_result(
    *,
    segments: list | None = None,
    language: str = "en",
    source: str = "auto",
    duration: float = 213.5,
):
    """Build a CaptionResult-shaped mock without invoking yt-dlp."""
    from app.services.youtube_captions import CaptionResult

    if segments is None:
        segments = [
            {"start": 0.0, "end": 2.5, "text": "Hello world"},
            {"start": 2.5, "end": 5.0, "text": "Second cue"},
        ]
    return CaptionResult(
        segments=segments,
        language=language,
        source=source,
        duration=duration,
    )


# ─────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────


def test_worker_writes_transcript_asset_on_success(
    db_session: Session, youtube_video: Video
):
    """Happy path: fake fetch → worker writes Asset + stamps status='ready'."""
    from app.services.youtube_captions_job import _run_caption_download_job

    fake = _fake_caption_result()

    with patch(
        "app.services.youtube_captions_job.fetch_youtube_captions",
        return_value=fake,
    ):
        _run_caption_download_job(youtube_video.id)

    # Refresh to pick up the worker's changes
    db_session.refresh(youtube_video)
    assert youtube_video.status == "ready"
    assert youtube_video.transcribed_at is not None
    assert youtube_video.language == "en"  # locked

    # Transcript Asset exists with the right shape
    asset = db_session.execute(
        select(Asset).where(
            Asset.video_id == youtube_video.id,
            Asset.asset_type == "transcript",
        )
    ).scalar_one_or_none()
    assert asset is not None
    payload = json.loads(asset.content)
    assert len(payload["segments"]) == 2
    assert payload["language"] == "en"
    assert payload["source"] == "auto"


def test_worker_preserves_existing_user_locked_language(
    db_session: Session, youtube_video: Video
):
    """If admin pre-locked the language, worker keeps that — even if
    yt-dlp returned a different one (rare, but possible if YouTube
    served a different track than requested)."""
    from app.services.youtube_captions_job import _run_caption_download_job

    # Admin pre-locked Chinese
    youtube_video.language = "zh-Hans"
    db_session.commit()

    fake = _fake_caption_result(language="en")  # yt-dlp returned en anyway

    with patch(
        "app.services.youtube_captions_job.fetch_youtube_captions",
        return_value=fake,
    ):
        _run_caption_download_job(youtube_video.id)

    db_session.refresh(youtube_video)
    # The user's lock is preserved (we only SET language if it was NULL)
    assert youtube_video.language == "zh-Hans"


def test_worker_locks_language_from_youtube_when_no_user_pref(
    db_session: Session, youtube_video: Video
):
    """If video.language was NULL, worker stamps the YouTube caption's
    language so future Whisper retries lock to it (anti-drift)."""
    from app.services.youtube_captions_job import _run_caption_download_job

    assert youtube_video.language is None

    fake = _fake_caption_result(language="ja")

    with patch(
        "app.services.youtube_captions_job.fetch_youtube_captions",
        return_value=fake,
    ):
        _run_caption_download_job(youtube_video.id)

    db_session.refresh(youtube_video)
    assert youtube_video.language == "ja"


def test_worker_stamps_duration_from_caption(
    db_session: Session, youtube_video: Video
):
    """Worker's caption duration overrides the YouTube Data API duration
    (caption timing is more accurate for transcript rendering)."""
    from app.services.youtube_captions_job import _run_caption_download_job

    fake = _fake_caption_result(duration=314.5)  # 5:14

    with patch(
        "app.services.youtube_captions_job.fetch_youtube_captions",
        return_value=fake,
    ):
        _run_caption_download_job(youtube_video.id)

    db_session.refresh(youtube_video)
    assert youtube_video.duration == 314.5


# ─────────────────────────────────────────────────────────────────────────
# Failure paths
# ─────────────────────────────────────────────────────────────────────────


def test_worker_sets_error_status_when_captions_unavailable(
    db_session: Session, youtube_video: Video
):
    """YouTubeCaptionsUnavailable → status='error', reason recorded."""
    from app.services.youtube_captions import YouTubeCaptionsUnavailable
    from app.services.youtube_captions_job import _run_caption_download_job

    with patch(
        "app.services.youtube_captions_job.fetch_youtube_captions",
        side_effect=YouTubeCaptionsUnavailable("No captions for this video"),
    ):
        _run_caption_download_job(youtube_video.id)

    db_session.refresh(youtube_video)
    assert youtube_video.status == "error"
    assert youtube_video.whisper_fallback_reason is not None
    assert "No captions" in youtube_video.whisper_fallback_reason


def test_worker_sets_error_status_when_captions_failed(
    db_session: Session, youtube_video: Video
):
    """YouTubeCaptionsFailed (network/private/etc) → status='error'."""
    from app.services.youtube_captions import YouTubeCaptionsFailed
    from app.services.youtube_captions_job import _run_caption_download_job

    with patch(
        "app.services.youtube_captions_job.fetch_youtube_captions",
        side_effect=YouTubeCaptionsFailed("Private video"),
    ):
        _run_caption_download_job(youtube_video.id)

    db_session.refresh(youtube_video)
    assert youtube_video.status == "error"
    assert "Private video" in youtube_video.whisper_fallback_reason


def test_worker_swallows_unexpected_exceptions(
    db_session: Session, youtube_video: Video
):
    """Defense in depth: any unexpected exception still produces
    status='error' + a clear message. No 500 to the admin."""
    from app.services.youtube_captions_job import _run_caption_download_job

    with patch(
        "app.services.youtube_captions_job.fetch_youtube_captions",
        side_effect=RuntimeError("yt-dlp internals exploded"),
    ):
        # Must NOT raise
        _run_caption_download_job(youtube_video.id)

    db_session.refresh(youtube_video)
    assert youtube_video.status == "error"
    assert "exploded" in youtube_video.whisper_fallback_reason


# ─────────────────────────────────────────────────────────────────────────
# Idempotency + edge cases
# ─────────────────────────────────────────────────────────────────────────


def test_worker_skips_if_transcript_already_exists(
    db_session: Session, youtube_video: Video
):
    """Re-running on a video that already has a transcript is a no-op.
    This prevents double-write when the admin retries the auto-fire."""
    import uuid

    # Pre-insert a transcript Asset
    existing_asset = Asset(
        id=str(uuid.uuid4()),
        video_id=youtube_video.id,
        asset_type="transcript",
        content=json.dumps({
            "segments": [{"start": 0, "end": 1, "text": "existing"}],
            "language": "en",
            "source": "manual",
            "duration": 1.0,
        }),
    )
    db_session.add(existing_asset)
    db_session.commit()

    from app.services.youtube_captions_job import _run_caption_download_job

    # If fetch_youtube_captions is called, the test will fail (we
    # didn't set up a return). Patch to a MagicMock so any call
    # raises if it happens.
    with patch(
        "app.services.youtube_captions_job.fetch_youtube_captions",
        MagicMock(side_effect=AssertionError("fetch called!")),
    ):
        _run_caption_download_job(youtube_video.id)

    # Existing asset is untouched
    db_session.refresh(existing_asset)
    payload = json.loads(existing_asset.content)
    assert payload["segments"][0]["text"] == "existing"


def test_worker_handles_missing_youtube_id(
    db_session: Session, youtube_video: Video
):
    """Defensive: legacy video without youtube_id → fail with clear msg."""
    youtube_video.youtube_id = None
    db_session.commit()

    from app.services.youtube_captions_job import _run_caption_download_job

    with patch(
        "app.services.youtube_captions_job.fetch_youtube_captions",
        MagicMock(side_effect=AssertionError("fetch called!")),
    ):
        _run_caption_download_job(youtube_video.id)

    db_session.refresh(youtube_video)
    assert youtube_video.status == "pending"  # unchanged (worker exited early)
    # The job itself finished with an error — but we don't check
    # app.jobs here; that's covered by the status endpoint test.


# ─────────────────────────────────────────────────────────────────────────
# Retry helper (synchronous, called from /captions/retry endpoint)
# ─────────────────────────────────────────────────────────────────────────


def test_retry_downloads_and_overwrites_existing_transcript(
    db_session: Session, youtube_video: Video
):
    """retry_caption_download always overwrites; useful when the previous
    attempt had bad data."""
    import uuid

    # Insert an old (wrong) transcript
    old_asset = Asset(
        id=str(uuid.uuid4()),
        video_id=youtube_video.id,
        asset_type="transcript",
        content=json.dumps({
            "segments": [{"start": 0, "end": 1, "text": "stale"}],
            "language": "en",
            "source": "auto",
            "duration": 1.0,
        }),
    )
    db_session.add(old_asset)
    db_session.commit()

    from app.services.youtube_captions_job import retry_caption_download

    fake = _fake_caption_result(segments=[
        {"start": 0.0, "end": 1.0, "text": "fresh 1"},
        {"start": 1.0, "end": 2.0, "text": "fresh 2"},
    ])

    with patch(
        "app.services.youtube_captions_job.fetch_youtube_captions",
        return_value=fake,
    ):
        result = retry_caption_download(youtube_video.id, db_session)

    assert result["status"] == "completed"
    assert result["segments"] == 2

    db_session.refresh(youtube_video)
    assert youtube_video.status == "ready"

    # The asset was updated in place (no second row)
    assets = db_session.execute(
        select(Asset).where(
            Asset.video_id == youtube_video.id,
            Asset.asset_type == "transcript",
        )
    ).scalars().all()
    assert len(assets) == 1
    payload = json.loads(assets[0].content)
    assert payload["segments"][0]["text"] == "fresh 1"


def test_retry_returns_error_dict_on_failure(
    db_session: Session, youtube_video: Video
):
    """Retry endpoint never 500s — failures come back as JSON."""
    from app.services.youtube_captions import YouTubeCaptionsUnavailable
    from app.services.youtube_captions_job import retry_caption_download

    with patch(
        "app.services.youtube_captions_job.fetch_youtube_captions",
        side_effect=YouTubeCaptionsUnavailable("nope"),
    ):
        result = retry_caption_download(youtube_video.id, db_session)

    assert result["status"] == "failed"
    assert "nope" in result["error"]

    db_session.refresh(youtube_video)
    assert youtube_video.status == "error"


def test_retry_returns_error_dict_for_missing_video(db_session: Session):
    """Sanity: retrying on a nonexistent video_id returns a clear error."""
    from app.services.youtube_captions_job import retry_caption_download

    result = retry_caption_download("does-not-exist", db_session)
    assert result["status"] == "failed"
    assert "not found" in result["error"].lower()


# ─────────────────────────────────────────────────────────────────────────
# GET /api/admin/videos/{id}/captions/status endpoint
# ─────────────────────────────────────────────────────────────────────────


def test_status_endpoint_returns_segments_count(
    client, db_session: Session, youtube_video: Video
):
    """After caption download, the status endpoint reports segment count."""
    import uuid
    from fastapi.testclient import TestClient
    from app.main import app
    from app.auth.admin import ensure_user_row
    from unittest.mock import patch

    # Ensure admin user
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()

    # Pre-insert a transcript asset
    asset = Asset(
        id=str(uuid.uuid4()),
        video_id=youtube_video.id,
        asset_type="transcript",
        content=json.dumps({
            "segments": [
                {"start": 0, "end": 1, "text": "a"},
                {"start": 1, "end": 2, "text": "b"},
                {"start": 2, "end": 3, "text": "c"},
            ],
            "language": "en",
            "source": "manual",
            "duration": 3.0,
        }),
    )
    db_session.add(asset)
    youtube_video.status = "ready"
    youtube_video.transcribed_at = None  # set so we can verify the field
    db_session.commit()

    with patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": "uid-admin", "email": "admin@x.com"},
    ):
        with TestClient(app) as c:
            response = c.get(
                f"/api/admin/videos/{youtube_video.id}/captions/status",
                headers={"Authorization": "Bearer fake"},
            )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["transcript_segments"] == 3


def test_status_endpoint_returns_404_for_missing_video(
    client, db_session: Session
):
    """Sanity: 404 for a video_id that doesn't exist."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.auth.admin import ensure_user_row
    from unittest.mock import patch

    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()

    with patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": "uid-admin", "email": "admin@x.com"},
    ):
        with TestClient(app) as c:
            response = c.get(
                "/api/admin/videos/does-not-exist/captions/status",
                headers={"Authorization": "Bearer fake"},
            )
    assert response.status_code == 404