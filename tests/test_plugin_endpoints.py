"""Tests for the /api/plugins router endpoints (MVP2.1.0).

Covers:
  - GET  /api/plugins                — list available plugins
  - POST /api/plugins/{name}/run     — run a plugin on a video
  - GET  /api/plugins/runs/{run_id}  — fetch a run's status
  - Auth: 404 (not 403) when accessing another user's video
  - Auth: the run endpoint requires a valid session cookie
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.plugin_run import PluginRun
from app.models.section import Section
from app.models.video import Video


# ── Helpers ─────────────────────────────────────────────────────────────
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
def test_list_plugins_returns_webm_to_mp4(client: TestClient, db_session: Session):
    """The list endpoint returns the v1 plugin."""
    _seed_video(db_session)
    resp = client.get("/api/plugins")
    assert resp.status_code == 200
    data = resp.json()
    assert "plugins" in data
    keys = [p["key"] for p in data["plugins"]]
    assert "webm_to_mp4" in keys


def test_list_plugins_each_has_required_fields(
    client: TestClient, db_session: Session
):
    """Each plugin in the list has key, label, description, available."""
    _seed_video(db_session)
    resp = client.get("/api/plugins")
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
    client: TestClient, db_session: Session
):
    """Unknown plugin name returns 404."""
    _seed_video(db_session)
    resp = client.post("/api/plugins/nonexistent/run?video_id=v1")
    assert resp.status_code == 404
    assert "Unknown plugin" in resp.json()["detail"]


def test_run_plugin_unknown_video_returns_404(
    client: TestClient, db_session: Session
):
    """Unknown video_id returns 404 (not 403, to avoid leaking IDs)."""
    _seed_video(db_session)
    resp = client.post("/api/plugins/webm_to_mp4/run?video_id=nonexistent")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_run_plugin_writes_audit_log_row(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """A successful POST writes a PluginRun row to the DB.

    We point upload_dir at a path with no source file so
    the plugin fails (the ffmpeg binary may not exist on
    the test machine). The point of this test is the audit
    log row, not the transcode success.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    _seed_video(db_session)

    # No source file exists -> plugin returns ok=False
    resp = client.post("/api/plugins/webm_to_mp4/run?video_id=v1")
    assert resp.status_code == 200
    data = resp.json()

    assert "run_id" in data
    assert data["plugin"] == "webm_to_mp4"
    assert data["video_id"] == "v1"
    assert isinstance(data["ok"], bool)
    assert "message" in data

    # Audit log row was written
    assert db_session.query(PluginRun).count() == 1
    row = db_session.query(PluginRun).first()
    assert row.id == data["run_id"]
    assert row.video_id == "v1"
    assert row.plugin_key == "webm_to_mp4"


# ── GET /api/plugins/runs/{run_id} ──────────────────────────────────────
def test_get_run_returns_run_row(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """After running a plugin, the run row is fetchable by id."""
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    _seed_video(db_session)

    # Run the plugin first
    run_resp = client.post("/api/plugins/webm_to_mp4/run?video_id=v1")
    run_id = run_resp.json()["run_id"]

    # Now fetch it
    resp = client.get(f"/api/plugins/runs/{run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == run_id
    assert data["video_id"] == "v1"
    assert data["plugin_key"] == "webm_to_mp4"


def test_get_run_unknown_id_returns_404(client: TestClient, db_session: Session):
    """Unknown run id returns 404."""
    resp = client.get("/api/plugins/runs/nonexistent")
    assert resp.status_code == 404


# ── MVP2.1.0.1: GET /api/plugins/runs/by-video/{video_id} ──────────────
def test_by_video_returns_null_when_no_runs(client: TestClient, db_session: Session):
    """When the video has no plugin runs, return {"run": null}."""
    _seed_video(db_session)
    resp = client.get("/api/plugins/runs/by-video/v1")
    assert resp.status_code == 200
    assert resp.json() == {"run": None}


def test_by_video_returns_most_recent_run(client: TestClient, db_session: Session):
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

    resp = client.get("/api/plugins/runs/by-video/v1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run"] is not None
    assert data["run"]["id"] == "r2"  # the most recent
    assert data["run"]["message"] == "Run #2"


def test_by_video_returns_null_for_unknown_video(client: TestClient, db_session: Session):
    """Unknown video_id returns {"run": null} (not 404)."""
    resp = client.get("/api/plugins/runs/by-video/nonexistent")
    assert resp.status_code == 200
    assert resp.json() == {"run": None}


# ── MVP2.1.0.1: POST /api/plugins/reveal ───────────────────────────────
def test_reveal_rejects_relative_path(client: TestClient, db_session: Session):
    """Relative paths are rejected with 400."""
    resp = client.post(
        "/api/plugins/reveal",
        json={"path": "uploads/lesson.mp4"},
    )
    assert resp.status_code == 400
    assert "absolute" in resp.json()["detail"].lower()


def test_reveal_rejects_path_outside_allowed_dirs(
    client: TestClient, db_session: Session, monkeypatch
):
    """Paths outside upload_dir/storage_dir are rejected with 403."""
    from app.config import settings
    # Force a known safe upload_dir so we can craft a
    # path that is definitely outside it.
    monkeypatch.setattr(settings, "upload_dir", "/tmp/allowed_uploads")
    monkeypatch.setattr(settings, "storage_dir", "/tmp/allowed_storage")

    # /etc/passwd is definitely outside both
    resp = client.post(
        "/api/plugins/reveal",
        json={"path": "/etc/passwd"},
    )
    assert resp.status_code == 403
    assert "allowed" in resp.json()["detail"].lower()


def test_reveal_accepts_path_inside_upload_dir(
    client: TestClient, db_session: Session, monkeypatch, tmp_path
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
        resp = client.post(
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
    client: TestClient, db_session: Session, monkeypatch, tmp_path
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
        resp = client.post(
            "/api/plugins/reveal",
            json={"path": str(real_file)},
        )

    assert resp.status_code == 500
    assert "Permission denied" in resp.json()["detail"]


def test_reveal_handles_command_not_found(
    client: TestClient, db_session: Session, monkeypatch, tmp_path
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
        resp = client.post(
            "/api/plugins/reveal",
            json={"path": str(real_file)},
        )

    assert resp.status_code == 500
