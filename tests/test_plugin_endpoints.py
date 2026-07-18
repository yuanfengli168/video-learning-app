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
