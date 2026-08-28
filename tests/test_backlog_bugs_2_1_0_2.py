"""Tests for the MVP2.1.0.2 backlog bug fixes.

The 3 known bugs that pre-dated 2.1.0.2:

1. **`Video.duration` column declared as `Integer` but stores
   floats like 336.44** (Whisper's segment-end timestamps are
   floats with sub-second precision; rounding to int loses
   millisecond accuracy in the course-page badge).
   - Schema fix in `app/models/video.py`:
     `Integer` → `Float`.
2. **`Video.file_size` not updated on swap** (after a
   WebM→MP4 swap, the DB still shows the original WebM's
   byte count instead of the new MP4's).
   - Fix in `app/services/plugins.py:swap_video_file_to`:
     stat the new file and update `video.file_size`.
3. **`get_video_file` hardcodes `Content-Type: video/mp4`**
   regardless of the actual file extension. Wrong for
   .webm / .avi / .mov / .mkv / .m4v files.
   - Fix in `app/routers/videos.py:get_video_file`:
     map extension → MIME type, fall back to
     `application/octet-stream` for unknown extensions.

These tests verify the fixes are in place and behave
correctly. The new tests are end-to-end (DB + HTTP)
to catch any regression in either layer.
"""

from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.section import Section
from app.models.video import Video


# ── Helpers ─────────────────────────────────────────────────────────────
def _seed_video(
    db: Session,
    *,
    video_id: str = "v1",
    ext: str = "mp4",
    status: str = "ready",
    file_path: str | None = None,
) -> Video:
    course = Course(id="c1", user_id="test-uid", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id=video_id,
        section_id="s1",
        title="Test video",
        filename=f"lesson.{ext}",
        # file_path defaults to a placeholder relative
        # path. Tests that need a real file on disk
        # should pass file_path= explicitly.
        file_path=file_path or f"uploads/{video_id}.{ext}",
        status=status,
    )
    db.add_all([course, section, video])
    db.commit()
    db.refresh(video)
    return video


# ── Bug 1: `Video.duration` is now Float ──────────────────────────────
def test_video_duration_column_is_float():
    """The `Video.duration` column is declared as Float, not Integer.

    Whisper's segment-end timestamps are floats with
    sub-second precision (e.g. 336.44 seconds). The
    original Integer declaration was a bug because it
    silently truncated the milliseconds.
    """
    from app.models.video import Video as VideoModel

    # Check the model's mapped type. SQLAlchemy 2.0 stores
    # this on the Column's `type` attribute. We verify the
    # python_type, which is the most reliable way to check
    # what Python type the column maps to (independent of
    # the SQL dialect).
    duration_col = VideoModel.__table__.c.duration
    assert duration_col.type.python_type is float, (
        f"videos.duration should be Float, got "
        f"{duration_col.type.python_type.__name__}"
    )


def test_video_duration_stores_float_value(db_session: Session):
    """A float value like 336.44 can be stored in `duration` without truncation.

    Before the fix (Integer column), setting 336.44 would
    silently become 336 (loses 440ms of accuracy). After
    the fix (Float column), 336.44 stays 336.44.
    """
    video = _seed_video(db_session)
    video.duration = 336.44
    db_session.commit()
    db_session.expire_all()

    # Re-fetch and check
    video = db_session.query(Video).filter(Video.id == "v1").first()
    assert video.duration == 336.44, (
        f"Expected duration=336.44 (float), got {video.duration!r}"
    )


# ── Bug 2: file_size is updated on swap ─────────────────────────────
def test_swap_updates_file_size(
    admin_client: TestClient, db_session: Session, tmp_path: Path, monkeypatch
):
    """After a swap, `Video.file_size` matches the new file on disk.

    Before the fix, swapping from a 54 MB WebM to an 11 MB
    MP4 left the DB showing 54 MB (the original WebM's
    size). After the fix, the DB shows the new MP4's
    actual size.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    # Create a "real" WebM file (just bytes — ffmpeg
    # detection happens on the transcode, not the swap).
    old_file = tmp_path / "v1.webm"
    old_file.write_bytes(b"x" * (54 * 1024 * 1024))  # 54 MB

    # Create a smaller "MP4" file (the new file after
    # a WebM→MP4 transcode).
    new_file = tmp_path / "v1.mp4"
    new_size = 11 * 1024 * 1024 + 1234  # 11 MB + a bit, to be sure
    new_file.write_bytes(b"y" * new_size)

    # Seed the video row with the OLD size (54 MB)
    video = _seed_video(db_session, ext="webm")
    video.file_size = 54 * 1024 * 1024
    db_session.commit()

    # Call the swap endpoint
    resp = admin_client.post(
        "/api/plugins/swap-to-mp4",
        json={"video_id": "v1", "mp4_path": str(new_file)},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True

    # Refresh the video row and check the size matches the
    # new file (not the old WebM)
    db_session.expire_all()
    video = db_session.query(Video).filter(Video.id == "v1").first()
    assert video.file_size == new_size, (
        f"After swap, file_size should be {new_size} (new file's size), "
        f"got {video.file_size} (DB still shows the old WebM's size). "
        f"This is the 2.1.0.2 bug — swap didn't update file_size."
    )
    # filename should also be updated
    assert video.filename == "v1.mp4"
    assert video.file_path == str(new_file)


def test_swap_audit_log_includes_size_info(
    admin_client: TestClient, db_session: Session, tmp_path: Path, monkeypatch
):
    """The swap audit log row includes the old + new size in extra_json."""
    from app.config import settings
    from app.models.plugin_run import PluginRun
    import json as json_lib

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    old_file = tmp_path / "v1.webm"
    old_file.write_bytes(b"x" * (54 * 1024 * 1024))
    new_file = tmp_path / "v1.mp4"
    new_size = 11 * 1024 * 1024
    new_file.write_bytes(b"y" * new_size)

    video = _seed_video(db_session, ext="webm")
    video.file_size = 54 * 1024 * 1024
    db_session.commit()

    admin_client.post(
        "/api/plugins/swap-to-mp4",
        json={"video_id": "v1", "mp4_path": str(new_file)},
    )

    db_session.expire_all()
    run = (
        db_session.query(PluginRun)
        .filter(PluginRun.video_id == "v1", PluginRun.plugin_key == "swap_to_mp4")
        .first()
    )
    assert run is not None
    extra = json_lib.loads(run.extra_json) if run.extra_json else {}
    assert extra.get("old_size_bytes") == 54 * 1024 * 1024
    assert extra.get("new_size_bytes") == new_size
    # And the human-readable message mentions both sizes.
    # The format uses MB = 1,000,000 bytes (decimal), so
    # 54 MiB shows as ~56.6 MB. We just check the message
    # contains BOTH sizes as some X.Y MB string — the exact
    # values depend on the test data, but the structure
    # "(X.X MB) to ... (Y.Y MB)" is what we care about.
    import re as _re
    sizes = _re.findall(r"([\d.]+ MB)", run.message)
    assert len(sizes) == 2, f"Expected 2 size mentions, got {sizes}"
    # The old file was 54 * 1024 * 1024 bytes = 56.623...
    # MB. The format string uses :.1f, so "56.6 MB".
    assert sizes[0].startswith("56."), f"Old size wrong: {sizes[0]}"
    # The new file was 11 * 1024 * 1024 + 1234 bytes =
    # 11.001 MB. :.1f → "11.0 MB".
    assert sizes[1].startswith("11."), f"New size wrong: {sizes[1]}"


# ── Bug 3: get_video_file returns the right Content-Type ────────────
def test_get_video_file_returns_correct_mime_for_mp4(
    client: TestClient, db_session: Session, tmp_path: Path, monkeypatch
):
    """A .mp4 file is served with Content-Type: video/mp4."""
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    # Create a real (tiny) MP4 file on disk
    real_mp4 = tmp_path / "v1.mp4"
    real_mp4.write_bytes(b"\x00" * 1024)  # 1 KB of zeros
    _seed_video(db_session, ext="mp4", file_path=str(real_mp4))

    resp = client.get("/api/videos/v1/file")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("video/mp4")


def test_get_video_file_returns_correct_mime_for_webm(
    client: TestClient, db_session: Session, tmp_path: Path, monkeypatch
):
    """A .webm file is served with Content-Type: video/webm.

    Before the fix, all video files were served with
    Content-Type: video/mp4 (hardcoded), regardless of
    the actual file extension.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    real_webm = tmp_path / "v1.webm"
    real_webm.write_bytes(b"\x00" * 1024)
    _seed_video(db_session, ext="webm", file_path=str(real_webm))

    resp = client.get("/api/videos/v1/file")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("video/webm"), (
        f"Expected video/webm, got {resp.headers['content-type']}. "
        f"This is the 2.1.0.2 bug — get_video_file hardcoded video/mp4."
    )


def test_get_video_file_returns_correct_mime_for_mov(
    client: TestClient, db_session: Session, tmp_path: Path, monkeypatch
):
    """A .mov file is served with Content-Type: video/quicktime."""
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    real_mov = tmp_path / "v1.mov"
    real_mov.write_bytes(b"\x00" * 1024)
    _seed_video(db_session, ext="mov", file_path=str(real_mov))

    resp = client.get("/api/videos/v1/file")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("video/quicktime")


def test_get_video_file_returns_correct_mime_for_mkv(
    client: TestClient, db_session: Session, tmp_path: Path, monkeypatch
):
    """A .mkv file is served with Content-Type: video/x-matroska."""
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    real_mkv = tmp_path / "v1.mkv"
    real_mkv.write_bytes(b"\x00" * 1024)
    _seed_video(db_session, ext="mkv", file_path=str(real_mkv))

    resp = client.get("/api/videos/v1/file")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("video/x-matroska")


def test_get_video_file_unknown_extension_falls_back(
    client: TestClient, db_session: Session, tmp_path: Path, monkeypatch
):
    """Unknown extensions fall back to application/octet-stream (download, don't play)."""
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    # .foo isn't in our MEDIA_TYPES map. We write a
    # file with this extension to bypass the upload
    # extension check (we're testing the GET endpoint,
    # not the upload).
    real_file = tmp_path / "v1.foo"
    real_file.write_bytes(b"\x00" * 1024)
    _seed_video(db_session, ext="foo", file_path=str(real_file))

    resp = client.get("/api/videos/v1/file")
    assert resp.status_code == 200
    # Unknown → application/octet-stream (browser offers download)
    assert resp.headers["content-type"].startswith("application/octet-stream")
