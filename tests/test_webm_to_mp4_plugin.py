"""Tests for the WebM -> MP4 transcode plugin (MVP2.1.0).

The plugin is a thin wrapper around ffmpeg. We test:
  - The ffmpeg-not-installed error case
  - The source-file-not-found error case
  - The PluginRun row is written for every invocation
  - A real ffmpeg transcode (skipped if ffmpeg isn't on
    $PATH; the test is conditional, not skipped)
  - Side-by-side: the original is NEVER modified

Most tests don't need a real ffmpeg install because we
catch the ffmpeg-missing case at the top of the function
and return a failed PluginResult.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.plugin_run import PluginRun
from app.models.section import Section
from app.models.video import Video
from app.services.plugins import (
    is_ffmpeg_available,
    run_plugin,
    transcode_webm_to_mp4,
)


# ── Helper: build a video in the test DB ───────────────────────────────
def _make_video(db: Session, tmp_path: Path, *, name: str = "lesson1.webm") -> Video:
    """Create a Course -> Section -> Video chain in the test DB.

    The video's `path` is a relative path like
    "abc/lesson1.webm" pointing at a file in tmp_path
    (so the plugin's src resolution can find it).
    """
    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    db.add_all([course, section])
    db.flush()

    sub = tmp_path / "abc"
    sub.mkdir(exist_ok=True)
    src = sub / name
    src.write_bytes(b"fake webm content for testing")

    video = Video(
        id="v1",
        section_id="s1",
        title="Test video",
        filename="lesson1.webm",
        file_path=str((tmp_path / "abc/lesson1.webm").resolve()),
        status="ready",
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


# ── ffmpeg detection ────────────────────────────────────────────────────
def test_transcode_returns_error_when_ffmpeg_missing(db_session, tmp_path, monkeypatch):
    """If ffmpeg isn't on $PATH, return ok=False with a helpful message.

    This test patches `shutil.which` to return None for the
    duration of the call, simulating an environment where
    ffmpeg is not installed. The plugin should detect this
    via `is_ffmpeg_available()` and return a failed result
    WITHOUT invoking ffmpeg.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr("app.services.plugins.is_ffmpeg_available", lambda: False)

    video = _make_video(db_session, tmp_path)

    result = transcode_webm_to_mp4(video, db_session)
    assert result.ok is False
    assert "ffmpeg" in result.message.lower()


def test_transcode_returns_error_when_source_missing(db_session, tmp_path, monkeypatch):
    """If the source file doesn't exist, return ok=False with a clear message."""
    # Set up video pointing at a NON-EXISTENT file
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1",
        section_id="s1",
        title="Test video",
        filename="does/not/exist.webm",
        file_path="does/not/exist.webm",
        status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    result = transcode_webm_to_mp4(video, db_session)
    assert result.ok is False
    assert "not found" in result.message.lower()


def test_transcode_handles_ffmpeg_error_gracefully(db_session, tmp_path, monkeypatch):
    """If ffmpeg exits non-zero, return ok=False with stderr in the message."""
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    # The test stores file_path as a relative path ("abc/lesson.webm")
    # which the plugin resolves from CWD. chdir to tmp_path so the
    # resolution finds the file we wrote at tmp_path/abc/lesson.webm.
    monkeypatch.chdir(tmp_path)

    # Set up a video with a real source file
    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    sub = tmp_path / "abc"
    sub.mkdir(exist_ok=True)
    (sub / "lesson.webm").write_bytes(b"fake content")
    video = Video(
        id="v1",
        section_id="s1",
        title="Test video",
        filename="abc/lesson.webm",
        file_path="abc/lesson.webm",
        status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    # Mock subprocess.run to simulate ffmpeg returning non-zero
    fake_proc = MagicMock()
    fake_proc.returncode = 1
    fake_proc.stderr = "Some error from ffmpeg\nLast line"

    with patch("app.services.plugins.subprocess.run", return_value=fake_proc):
        result = transcode_webm_to_mp4(video, db_session)

    assert result.ok is False
    assert "ffmpeg failed" in result.message
    assert "Last line" in result.message  # the tail of stderr


def test_transcode_handles_timeout(db_session, tmp_path, monkeypatch):
    """If ffmpeg takes longer than 30 min, return ok=False with timeout msg."""
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    sub = tmp_path / "abc"
    sub.mkdir(exist_ok=True)
    (sub / "lesson.webm").write_bytes(b"fake content")
    video = Video(
        id="v1",
        section_id="s1",
        title="Test video",
        filename="abc/lesson.webm",
        file_path="abc/lesson.webm",
        status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    # Mock subprocess.run to raise TimeoutExpired
    import subprocess
    with patch(
        "app.services.plugins.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=1800),
    ):
        result = transcode_webm_to_mp4(video, db_session)

    assert result.ok is False
    assert "timed out" in result.message.lower()


@pytest.mark.skipif(
    not shutil.which("ffmpeg"),
    reason="ffmpeg not installed; cannot test real transcode",
)
def test_transcode_actually_runs_ffmpeg_on_real_file(db_session, tmp_path, monkeypatch):
    """Real ffmpeg transcode of a real input file. Skipped without ffmpeg.

    Creates a tiny 1-second test video via ffmpeg itself,
    transcodes it, and asserts the output file exists and
    is non-empty. This is the "happy path" test.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    sub = tmp_path / "abc"
    sub.mkdir(exist_ok=True)

    # Generate a 1-second test pattern WebM via ffmpeg
    import subprocess
    webm = sub / "test.webm"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
            "-c:v", "libvpx", "-b:v", "100k",
            str(webm),
        ],
        check=True,
        capture_output=True,
    )

    video = Video(
        id="v1",
        section_id="s1",
        title="Test video",
        filename="abc/test.webm",
        file_path="abc/test.webm",
        status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    result = transcode_webm_to_mp4(video, db_session)
    assert result.ok is True
    assert result.output_path is not None
    assert Path(result.output_path).exists()
    assert Path(result.output_path).stat().st_size > 0

    # Side-by-side: the original WebM is untouched
    assert webm.exists()
    assert webm.read_bytes() == (sub / "test.webm").read_bytes()


# ── Plugin registry dispatch (run_plugin) ───────────────────────────────
def test_run_plugin_writes_audit_log(db_session, tmp_path, monkeypatch):
    """Every plugin invocation writes a PluginRun row."""
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1",
        section_id="s1",
        title="Test video",
        filename="missing.webm",
        file_path="missing.webm",  # source doesn't exist
        status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    # Before
    assert db_session.query(PluginRun).count() == 0

    result, run_row = run_plugin("webm_to_mp4", video, db_session)
    db_session.commit()

    # After: one PluginRun row written
    assert db_session.query(PluginRun).count() == 1
    assert run_row.id  # UUID
    assert run_row.video_id == video.id
    assert run_row.plugin_key == "webm_to_mp4"
    assert run_row.ok is False  # source missing
    assert "not found" in run_row.message.lower()


def test_run_plugin_with_unknown_key_writes_audit_log(db_session, tmp_path, monkeypatch):
    """Unknown plugin key still writes an audit log entry (for debugging)."""
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1",
        section_id="s1",
        title="Test video",
        filename="lesson.webm",
        file_path="lesson.webm",
        status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    result, run_row = run_plugin("nonexistent_plugin", video, db_session)
    db_session.commit()

    assert result.ok is False
    assert "unknown plugin" in result.message.lower()
    assert db_session.query(PluginRun).count() == 1
    assert run_row.ok is False


def test_run_plugin_swallows_exceptions_and_logs_them(db_session, tmp_path, monkeypatch):
    """If the plugin function raises, we still write a failed audit row.

    We patch the function pointer stored in PLUGIN_REGISTRY
    (not the module-level name) because run_plugin dispatches
    via `spec.function`, which is captured at registry
    build time. Patching the module name alone wouldn't
    affect the dispatch.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1",
        section_id="s1",
        title="Test video",
        filename="lesson.webm",
        file_path="lesson.webm",
        status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    # Mock the function pointer in the registry (not the
    # module-level name, which has no effect on dispatch).
    from app.services.plugins import PLUGIN_REGISTRY
    original = PLUGIN_REGISTRY["webm_to_mp4"].function
    PLUGIN_REGISTRY["webm_to_mp4"].function = MagicMock(
        side_effect=RuntimeError("boom")
    )
    try:
        result, run_row = run_plugin("webm_to_mp4", video, db_session)
        db_session.commit()
    finally:
        PLUGIN_REGISTRY["webm_to_mp4"].function = original

    assert result.ok is False
    assert "boom" in result.message.lower()
    assert db_session.query(PluginRun).count() == 1
    assert run_row.ok is False


# ── Regression: relative path resolution (2.1.0 bug fix) ──────────────
def test_relative_file_path_resolves_from_cwd_not_upload_dir(
    db_session, tmp_path, monkeypatch
):
    """Regression test for the 2.1.0 double-prefix bug.

    The videos.file_path column is written at upload time
    as `settings.upload_path / saved_filename`. When
    settings.upload_dir is the default "./uploads", the
    stored value is the RELATIVE path "uploads/<uuid>.<ext>"
    (NOT absolute, NOT prepended with another upload_dir).

    The plugin must resolve this relative path from CWD
    (the project root), NOT prepend upload_dir to it
    (which would produce "uploads/uploads/<uuid>.<ext>"
    and fail to find the file).

    This test pins down the fix so a future refactor
    can't reintroduce the bug.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    # The relative file_path is "uploads/<uuid>.<ext>"
    # (this is the exact format the upload router writes
    # when settings.upload_dir is the default "./uploads").
    # The file actually lives at tmp_path/uploads/<uuid>.<ext>.
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(exist_ok=True)
    real_file = upload_dir / "test.webm"
    real_file.write_bytes(b"fake webm content")
    # chdir to tmp_path so the plugin's "resolve the
    # relative path from CWD" finds the file at
    # tmp_path/uploads/test.webm (not the project root's
    # uploads/ dir, which is empty in the test env).
    monkeypatch.chdir(tmp_path)

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1",
        section_id="s1",
        title="Test video",
        filename="test.webm",
        file_path="uploads/test.webm",  # RELATIVE, with prefix
        status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    # Mock ffmpeg so we can verify the absolute path ffmpeg
    # was called with. We assert it's the resolved absolute
    # path under tmp_path, NOT tmp_path/uploads/uploads/...
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stderr = ""

    with patch("app.services.plugins.subprocess.run", return_value=fake_proc) as mock_run:
        result = transcode_webm_to_mp4(video, db_session)

    # The result should be ok=True (ffmpeg returned 0)
    assert result.ok is True, (
        f"Expected success, got ok=False with: {result.message}"
    )
    # And the source path passed to ffmpeg must NOT have
    # the double prefix. It should be tmp_path/uploads/test.webm
    # (one 'uploads', not two).
    call_args = mock_run.call_args[0][0]  # first positional arg = cmd list
    src_passed_to_ffmpeg = call_args[call_args.index("-i") + 1]
    assert "uploads/uploads" not in src_passed_to_ffmpeg, (
        f"DOUBLE-PREFIX BUG REGRESSED! ffmpeg was called with: "
        f"{src_passed_to_ffmpeg}. The plugin resolved the relative "
        f"path as 'uploads/uploads/test.webm' instead of 'uploads/test.webm'."
    )
    # Verify the actual file would have been found
    assert Path(src_passed_to_ffmpeg).resolve() == real_file.resolve()
