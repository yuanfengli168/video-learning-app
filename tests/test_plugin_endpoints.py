"""Tests for the /api/plugins router endpoints (MVP2.1.0).

Covers:
  - GET  /api/plugins                — list available plugins
  - POST /api/plugins/{name}/run     — run a plugin on a video
  - GET  /api/plugins/runs/{run_id}  — fetch a run's status
  - Auth: 404 (not 403) when accessing another user's video
  - Auth: the run endpoint requires a valid session cookie
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.plugin_run import PluginRun
from app.models.section import Section
from app.models.video import Video


# ── Helpers ─────────────────────────────────────────────────────────────
def _wait_for_run_done(
    admin_client: TestClient, run_id: str, timeout_s: float = 30.0
) -> dict:
    """Poll GET /api/plugins/runs/{run_id} until the worker
    finishes (status='done' or 'failed'). Returns the final
    run JSON. Raises AssertionError on timeout.

    Used by the run-endpoint tests (MVP2.1.0.1) — the
    /run endpoint now returns 202 immediately, so the
    test must wait for the worker before asserting on
    the run's result. Polls every 100ms; for a typical
    test run (plugin fails fast on missing input) the
    worker finishes in <100ms. The 30s timeout is
    generous — the TestClient + asyncio loop has
    significant cold-start overhead on the first call
    (anyio portal setup, lifespan, pool worker spawn),
    so we leave plenty of headroom.
    """
    import time

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = admin_client.get(f"/api/plugins/runs/{run_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data.get("status") in ("done", "failed"):
            return data
        time.sleep(0.1)
    raise AssertionError(
        f"Plugin run {run_id} did not finish within {timeout_s}s"
    )


def _seed_video(db: Session, *, video_id: str = "v1") -> Video:
    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id=video_id,
        section_id="s1",
        title="Test video",
        filename="lesson.webm",
        file_path="lesson.webm",
        status="ready",
    )
    db.add_all([course, section, video])
    db.commit()
    db.refresh(video)
    return video


# ── GET /api/plugins ────────────────────────────────────────────────────
def test_list_plugins_returns_webm_to_mp4(admin_client: TestClient, db_session: Session):
    """The list endpoint returns the v1 plugin."""
    _seed_video(db_session)
    resp = admin_client.get("/api/plugins")
    assert resp.status_code == 200
    data = resp.json()
    assert "plugins" in data
    keys = [p["key"] for p in data["plugins"]]
    assert "webm_to_mp4" in keys


def test_list_plugins_each_has_required_fields(
    admin_client: TestClient, db_session: Session
):
    """Each plugin in the list has key, label, description, available."""
    _seed_video(db_session)
    resp = admin_client.get("/api/plugins")
    data = resp.json()
    for plugin in data["plugins"]:
        assert "key" in plugin
        assert "label" in plugin
        assert "description" in plugin
        assert "available" in plugin
        assert "missing" in plugin
        assert isinstance(plugin["available"], bool)
        assert isinstance(plugin["missing"], list)


# ── POST /api/plugins/{name}/run ────────────────────────────────────────
def test_run_plugin_unknown_name_returns_404(
    admin_client: TestClient, db_session: Session
):
    """Unknown plugin name returns 404."""
    _seed_video(db_session)
    resp = admin_client.post("/api/plugins/nonexistent/run?video_id=v1")
    assert resp.status_code == 404
    assert "Unknown plugin" in resp.json()["detail"]


def test_run_plugin_unknown_video_returns_404(
    admin_client: TestClient, db_session: Session
):
    """Unknown video_id returns 404 (not 403, to avoid leaking IDs)."""
    _seed_video(db_session)
    resp = admin_client.post("/api/plugins/webm_to_mp4/run?video_id=nonexistent")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_run_plugin_writes_audit_log_row(
    admin_client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """A successful POST writes a PluginRun row to the DB.

    We point upload_dir at a path with no source file so
    the plugin fails (the ffmpeg binary may not exist on
    the test machine). The point of this test is the audit
    log row, not the transcode success.

    MVP2.1.0.1: the /run endpoint now goes through
    PluginPool. In test mode (plugin_pool.synchronous_mode
    = True, set by the conftest's client fixture),
    submit() runs the plugin inline and updates the row
    before returning. So the response is still 202 +
    {run_id, status: "queued"} BUT the run is already
    done by the time the test asserts.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    _seed_video(db_session)

    # Submit. No source file exists -> plugin returns
    # ok=False, but the audit row IS written.
    resp = admin_client.post("/api/plugins/webm_to_mp4/run?video_id=v1")
    assert resp.status_code == 202
    data = resp.json()

    assert "run_id" in data
    assert data["plugin"] == "webm_to_mp4"
    assert data["video_id"] == "v1"
    assert data["status"] == "queued"

    # Expire the test session so it sees the worker's
    # commits (the worker uses its own session, opened
    # via SessionLocal()). Without expire_all(), the
    # test session's identity map may have stale
    # objects from before the worker's commit.
    db_session.expire_all()

    # Audit log row was written
    assert db_session.query(PluginRun).count() == 1
    row = db_session.query(PluginRun).first()
    assert row.id == data["run_id"]
    assert row.video_id == "v1"
    assert row.plugin_key == "webm_to_mp4"
    # The new status field is set to 'failed' because
    # the plugin couldn't find the source file.
    assert row.status in ("done", "failed")


# ── GET /api/plugins/runs/{run_id} ──────────────────────────────────────
def test_get_run_returns_run_row(
    admin_client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """After running a plugin, the run row is fetchable by id.

    MVP2.1.0.1: the run endpoint now uses the worker
    pool (in test mode, runs synchronously via
    plugin_pool.synchronous_mode = True). The run row
    is in the DB by the time the test fetches it.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    _seed_video(db_session)

    # Submit. Will fail (no source file) but the row
    # is created either way.
    run_resp = admin_client.post("/api/plugins/webm_to_mp4/run?video_id=v1")
    assert run_resp.status_code == 202
    run_id = run_resp.json()["run_id"]

    # Now fetch it
    resp = admin_client.get(f"/api/plugins/runs/{run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == run_id
    assert data["video_id"] == "v1"
    assert data["plugin_key"] == "webm_to_mp4"
    # MVP2.1.0.1: the new 'status' field is included
    assert data["status"] in ("done", "failed")


def test_get_run_unknown_id_returns_404(admin_client: TestClient, db_session: Session):
    """Unknown run id returns 404."""
    resp = admin_client.get("/api/plugins/runs/nonexistent")
    assert resp.status_code == 404


# ── MVP2.1.0.1: GET /api/plugins/runs/by-video/{video_id} ──────────────
def test_by_video_returns_null_when_no_runs(admin_client: TestClient, db_session: Session):
    """When the video has no plugin runs, return {"run": null}."""
    _seed_video(db_session)
    resp = admin_client.get("/api/plugins/runs/by-video/v1")
    assert resp.status_code == 200
    assert resp.json() == {"run": None}


def test_by_video_returns_most_recent_run(admin_client: TestClient, db_session: Session):
    """When the video has multiple runs, return the most recent one."""
    from datetime import datetime, timezone, timedelta

    _seed_video(db_session)

    # Insert 3 runs with different timestamps
    for i in range(3):
        run = PluginRun(
            id=f"r{i}",
            video_id="v1",
            plugin_key="webm_to_mp4",
            ok=True,
            message=f"Run #{i}",
            output_path=f"/uploads/lesson{i}.mp4" if i % 2 == 0 else None,
            extra_json=None,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=10 - i),
        )
        db_session.add(run)
    db_session.commit()

    resp = admin_client.get("/api/plugins/runs/by-video/v1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run"] is not None
    assert data["run"]["id"] == "r2"  # the most recent
    assert data["run"]["message"] == "Run #2"


def test_by_video_returns_null_for_unknown_video(admin_client: TestClient, db_session: Session):
    """Unknown video_id returns {"run": null} (not 404)."""
    resp = admin_client.get("/api/plugins/runs/by-video/nonexistent")
    assert resp.status_code == 200
    assert resp.json() == {"run": None}


# ── MVP2.1.0.1: POST /api/plugins/reveal ───────────────────────────────
def test_reveal_rejects_relative_path(admin_client: TestClient, db_session: Session):
    """Relative paths are rejected with 400."""
    resp = admin_client.post(
        "/api/plugins/reveal",
        json={"path": "uploads/lesson.mp4"},
    )
    assert resp.status_code == 400
    assert "absolute" in resp.json()["detail"].lower()


def test_reveal_rejects_path_outside_allowed_dirs(
    admin_client: TestClient, db_session: Session, monkeypatch
):
    """Paths outside upload_dir/storage_dir are rejected with 403."""
    from app.config import settings
    # Force a known safe upload_dir so we can craft a
    # path that is definitely outside it.
    monkeypatch.setattr(settings, "upload_dir", "/tmp/allowed_uploads")
    monkeypatch.setattr(settings, "storage_dir", "/tmp/allowed_storage")

    # /etc/passwd is definitely outside both
    resp = admin_client.post(
        "/api/plugins/reveal",
        json={"path": "/etc/passwd"},
    )
    assert resp.status_code == 403
    assert "allowed" in resp.json()["detail"].lower()


def test_reveal_accepts_path_inside_upload_dir(
    admin_client: TestClient, db_session: Session, monkeypatch, tmp_path
):
    """Paths inside upload_dir succeed (mocked subprocess)."""
    from unittest.mock import patch, MagicMock
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))

    # Create a real file so resolve() works
    real_file = tmp_path / "lesson.mp4"
    real_file.write_bytes(b"fake")

    # Mock the platform-specific reveal command.
    # We don't care which command runs; we just need
    # subprocess.run to return success.
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stderr = ""

    with patch("app.routers.plugins.subprocess.run", return_value=fake_proc) as mock_run:
        resp = admin_client.post(
            "/api/plugins/reveal",
            json={"path": str(real_file)},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["path"] == str(real_file.resolve())
    # And the subprocess was actually called
    mock_run.assert_called_once()


def test_reveal_handles_subprocess_nonzero_exit(
    admin_client: TestClient, db_session: Session, monkeypatch, tmp_path
):
    """If the platform command returns non-zero, return 500."""
    from unittest.mock import patch, MagicMock
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))

    real_file = tmp_path / "lesson.mp4"
    real_file.write_bytes(b"fake")

    fake_proc = MagicMock()
    fake_proc.returncode = 1
    fake_proc.stderr = "Permission denied"

    with patch("app.routers.plugins.subprocess.run", return_value=fake_proc):
        resp = admin_client.post(
            "/api/plugins/reveal",
            json={"path": str(real_file)},
        )

    assert resp.status_code == 500
    assert "Permission denied" in resp.json()["detail"]


def test_reveal_handles_command_not_found(
    admin_client: TestClient, db_session: Session, monkeypatch, tmp_path
):
    """If the platform command isn't on $PATH, return 500."""
    from unittest.mock import patch
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))

    real_file = tmp_path / "lesson.mp4"
    real_file.write_bytes(b"fake")

    with patch(
        "app.routers.plugins.subprocess.run",
        side_effect=FileNotFoundError("No such file: 'open'"),
    ):
        resp = admin_client.post(
            "/api/plugins/reveal",
            json={"path": str(real_file)},
        )

    assert resp.status_code == 500


# ── MVP2.1.0.1: POST /api/plugins/swap-to-mp4 ──────────────────────────
def test_swap_to_mp4_unknown_video_returns_404(
    admin_client: TestClient, db_session: Session
):
    """Unknown video_id returns 404."""
    resp = admin_client.post(
        "/api/plugins/swap-to-mp4",
        json={"video_id": "nonexistent", "mp4_path": "/tmp/lesson.mp4"},
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_swap_to_mp4_rejects_video_in_transient_status(
    admin_client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """Video in 'transcribing' status returns 409 (can't swap mid-job)."""
    from app.config import settings
    from app.models.course import Course
    from app.models.section import Section
    from app.models.video import Video

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    # Create a real MP4 file so the path-resolution check passes
    (tmp_path / "lesson.mp4").write_bytes(b"fake")

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1", section_id="s1", title="Test video",
        file_path="lesson.webm", filename="lesson.webm",
        status="transcribing",  # NOT 'ready'
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    resp = admin_client.post(
        "/api/plugins/swap-to-mp4",
        json={"video_id": "v1", "mp4_path": str(tmp_path / "lesson.mp4")},
    )
    assert resp.status_code == 409
    assert "transcribing" in resp.json()["detail"].lower()


def test_swap_to_mp4_rejects_missing_file(
    admin_client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """MP4 path that doesn't exist returns 400."""
    from app.config import settings
    from app.models.course import Course
    from app.models.section import Section
    from app.models.video import Video

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1", section_id="s1", title="Test video",
        file_path="lesson.webm", filename="lesson.webm",
        status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    # Path that doesn't exist
    resp = admin_client.post(
        "/api/plugins/swap-to-mp4",
        json={"video_id": "v1", "mp4_path": str(tmp_path / "nonexistent.mp4")},
    )
    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"].lower()


def test_swap_to_mp4_success_updates_video(
    admin_client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """Happy path: swap from WebM to MP4, video row updated, audit log written."""
    from app.config import settings
    from app.models.course import Course
    from app.models.section import Section
    from app.models.video import Video
    from app.models.plugin_run import PluginRun
    from datetime import datetime, timezone

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    # Create the source WebM
    webm = tmp_path / "lesson.webm"
    webm.write_bytes(b"fake webm")
    # Create the target MP4
    mp4 = tmp_path / "lesson.mp4"
    mp4.write_bytes(b"fake mp4")

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1", section_id="s1", title="Test video",
        file_path=str(webm), filename="lesson.webm",
        status="ready",
        transcribed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),  # preserved
        generated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),    # preserved
    )
    db_session.add_all([course, section, video])
    db_session.commit()
    old_transcribed_at = video.transcribed_at
    old_generated_at = video.generated_at

    resp = admin_client.post(
        "/api/plugins/swap-to-mp4",
        json={"video_id": "v1", "mp4_path": str(mp4)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["new_filename"] == "lesson.mp4"
    assert Path(data["new_path"]).resolve() == mp4.resolve()

    # Verify the DB row was updated
    db_session.expire_all()  # force re-fetch
    updated = db_session.get(Video, "v1")
    assert updated.file_path == str(mp4.resolve())
    assert updated.filename == "lesson.mp4"
    # status is unchanged
    assert updated.status == "ready"
    # transcripts are preserved (the user said "no need to
    # transcribe again")
    assert updated.transcribed_at == old_transcribed_at
    assert updated.generated_at == old_generated_at

    # Audit log row was written
    runs = db_session.query(PluginRun).filter(PluginRun.video_id == "v1").all()
    assert len(runs) == 1
    run = runs[0]
    assert run.plugin_key == "swap_to_mp4"
    assert run.ok is True
    # The old values are in extra_json (for future undo)
    assert "lesson.webm" in run.extra_json
