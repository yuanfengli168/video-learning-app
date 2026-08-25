"""Tests for app/services/video_status.py.

Day 5 hotfix2: when a video's status is 'error' but all 5 required
assets exist, flip it back to 'ready' so the UI renders materials.
"""

import uuid

import pytest
from sqlalchemy import text

from app.models import Asset, Video
from app.services.video_status import (
    REQUIRED_ASSET_TYPES,
    has_all_required_assets,
    reconcile_all_error_videos,
    reconcile_video_status,
)


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


def _ensure_section(db_session) -> str:
    """Return a section_id, creating a dummy one if the test DB is empty."""
    row = db_session.execute(text("SELECT id FROM sections LIMIT 1")).fetchone()
    if row:
        return row[0]
    # No sections in the test DB — create one with a fresh course.
    course_id = str(uuid.uuid4())
    section_id = str(uuid.uuid4())
    db_session.execute(text(
        "INSERT INTO courses (id, title, description, user_id) "
        "VALUES (:id, 'Test', '', 'test-uid')"
    ), {"id": course_id})
    db_session.execute(text(
        "INSERT INTO sections (id, title, course_id, order_index) "
        "VALUES (:id, 'Test', :course_id, 0)"
    ), {"id": section_id, "course_id": course_id})
    db_session.commit()
    return section_id


@pytest.fixture
def video_with_all_assets(db_session):
    """Insert a video in 'error' state with all 5 required assets."""
    video_id = str(uuid.uuid4())
    section_id = _ensure_section(db_session)
    db_session.add(Video(
        id=video_id,
        title="Status reconcile test",
        filename="test.mp4",
        file_path="/tmp/test.mp4",
        section_id=section_id,
        status="error",
    ))
    for asset_type in REQUIRED_ASSET_TYPES:
        db_session.add(Asset(
            video_id=video_id,
            asset_type=asset_type,
            content="{}" if asset_type != "summary" else "## summary",
        ))
    db_session.commit()
    return video_id


@pytest.fixture
def video_missing_summary(db_session):
    """Video in 'error' state with 4/5 required assets (no summary)."""
    video_id = str(uuid.uuid4())
    section_id = _ensure_section(db_session)
    db_session.add(Video(
        id=video_id,
        title="Missing summary",
        filename="test.mp4",
        file_path="/tmp/test.mp4",
        section_id=section_id,
        status="error",
    ))
    for asset_type in ("transcript", "mindmap", "flashcards", "quiz"):
        db_session.add(Asset(
            video_id=video_id,
            asset_type=asset_type,
            content="{}",
        ))
    db_session.commit()
    return video_id


# ─────────────────────────────────────────────────────────────────────────
# has_all_required_assets
# ─────────────────────────────────────────────────────────────────────────


def test_has_all_required_assets_true_when_all_present(db_session, video_with_all_assets):
    assert has_all_required_assets(db_session, video_with_all_assets) is True


def test_has_all_required_assets_false_when_one_missing(db_session, video_missing_summary):
    assert has_all_required_assets(db_session, video_missing_summary) is False


def test_has_all_required_assets_false_for_nonexistent_video(db_session):
    assert has_all_required_assets(db_session, "00000000-0000-0000-0000-000000000000") is False


# ─────────────────────────────────────────────────────────────────────────
# reconcile_video_status
# ─────────────────────────────────────────────────────────────────────────


def test_reconcile_flips_error_to_ready_when_assets_present(db_session, video_with_all_assets):
    """The whole point of this module."""
    video = db_session.get(Video, video_with_all_assets)
    assert video.status == "error"

    changed = reconcile_video_status(db_session, video)
    assert changed is True

    db_session.refresh(video)
    assert video.status == "ready"


def test_reconcile_does_nothing_when_assets_missing(db_session, video_missing_summary):
    video = db_session.get(Video, video_missing_summary)
    assert video.status == "error"

    changed = reconcile_video_status(db_session, video)
    assert changed is False

    db_session.refresh(video)
    assert video.status == "error"


def test_reconcile_does_nothing_when_status_already_ready(db_session, video_with_all_assets):
    video = db_session.get(Video, video_with_all_assets)
    video.status = "ready"
    db_session.commit()

    changed = reconcile_video_status(db_session, video)
    assert changed is False
    db_session.refresh(video)
    assert video.status == "ready"  # unchanged


def test_reconcile_does_nothing_for_pending_status(db_session, video_with_all_assets):
    """'pending' is a transient state; we don't touch it."""
    video = db_session.get(Video, video_with_all_assets)
    video.status = "pending"
    db_session.commit()

    changed = reconcile_video_status(db_session, video)
    assert changed is False
    db_session.refresh(video)
    assert video.status == "pending"


# ─────────────────────────────────────────────────────────────────────────
# reconcile_all_error_videos
# ─────────────────────────────────────────────────────────────────────────


def test_reconcile_all_flips_only_complete_videos(db_session, video_with_all_assets, video_missing_summary):
    """Bulk reconcile: only the videos with all required assets flip."""
    flipped = reconcile_all_error_videos(db_session)
    flipped_set = set(flipped)

    assert video_with_all_assets in flipped_set
    assert video_missing_summary not in flipped_set

    # Verify final state
    ready_video = db_session.get(Video, video_with_all_assets)
    incomplete_video = db_session.get(Video, video_missing_summary)
    assert ready_video.status == "ready"
    assert incomplete_video.status == "error"
