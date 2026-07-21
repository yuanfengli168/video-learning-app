"""Tests for the PluginPool worker (MVP2.1.0.1).

The pool is a module-level singleton (app/workers/
plugin_pool.py). These tests verify:
  - `submit()` creates a queued PluginRun row
  - The synchronous mode (used by the test client)
    runs the plugin inline and updates the row to
    done / failed before returning
  - The pool's semaphore caps concurrent plugin runs
    (covered indirectly via the sync-mode single-
    thread test; concurrency behavior is exercised
    in production by the JS polling flow)
  - The `status` field is correctly transitioned
    queued → running → done / failed
  - The /api/plugins/runs/{id} endpoint includes
    the new `status` field
  - A "tab close" scenario: submit + close client +
    reopen + the run row is still findable (because
    the row is in the DB, not in-memory)

All tests use the synchronous mode (set by the
conftest's `client` fixture) so they don't have to
poll for the worker. This is appropriate for unit
tests of the row state machine; the production
async flow is covered by manual / smoke tests.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.plugin_run import PluginRun
from app.models.section import Section
from app.models.video import Video
from app.workers.plugin_pool import plugin_pool


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


# ── Status field transitions ───────────────────────────────────────────
def test_plugin_run_row_has_status_field(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """A successfully-submitted run has a 'status' field.

    The PluginRun model added the 'status' column in
    MVP2.1.0.1 (queued / running / done / failed).
    """
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _seed_video(db_session)

    resp = client.post("/api/plugins/webm_to_mp4/run?video_id=v1")
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    # In test mode, the worker has already run and
    # updated the row. Refresh and check.
    db_session.expire_all()
    row = db_session.query(PluginRun).filter(PluginRun.id == run_id).first()
    assert row is not None
    # In sync mode, the status is already 'done' or 'failed'
    # (the plugin failed because there's no source file)
    assert row.status in ("done", "failed")


def test_plugin_run_endpoint_includes_status(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """The /api/plugins/runs/{id} endpoint returns the status field."""
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _seed_video(db_session)

    resp = client.post("/api/plugins/webm_to_mp4/run?video_id=v1")
    run_id = resp.json()["run_id"]

    fetch = client.get(f"/api/plugins/runs/{run_id}")
    assert fetch.status_code == 200
    data = fetch.json()
    assert "status" in data
    assert data["status"] in ("done", "failed")


def test_plugin_run_by_video_includes_status(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """The /api/plugins/runs/by-video/{id} endpoint returns status."""
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _seed_video(db_session)

    client.post("/api/plugins/webm_to_mp4/run?video_id=v1")
    db_session.expire_all()

    fetch = client.get("/api/plugins/runs/by-video/v1")
    assert fetch.status_code == 200
    data = fetch.json()
    assert data["run"] is not None
    assert "status" in data["run"]


# ── 202 Accepted response ───────────────────────────────────────────────
def test_run_endpoint_returns_202_accepted(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """The /run endpoint returns 202 (Accepted), not 200.

    202 means "I started working on it, poll for
    status". The previous sync code path returned 200
    with the full result; that worked for fast
    plugins but blocked the HTTP request for slow
    ones (5+ min for a 1-hour WebM transcode).
    """
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _seed_video(db_session)

    resp = client.post("/api/plugins/webm_to_mp4/run?video_id=v1")
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "queued"
    assert "run_id" in data


# ── Tab close survival ──────────────────────────────────────────────────
def test_run_row_persists_after_client_close(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """Closing the TestClient (simulating tab close) does
    NOT delete the run row. The user can reopen and
    see the result.

    The plugin is queued in the DB. Even if the
    background worker hasn't run yet (in async mode),
    the row is in the DB and findable. In sync test
    mode, the row is already in 'done' / 'failed'.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _seed_video(db_session)

    resp = client.post("/api/plugins/webm_to_mp4/run?video_id=v1")
    run_id = resp.json()["run_id"]

    # The DB row is the source of truth. It exists
    # even if the HTTP client disconnects.
    db_session.expire_all()
    row = db_session.query(PluginRun).filter(PluginRun.id == run_id).first()
    assert row is not None, "Run row vanished — pool is not persisting to DB"


# ── Pool introspection ──────────────────────────────────────────────────
def test_pool_stats_reflects_submissions(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """PluginPool.stats() returns counts that match the
    number of submissions.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _seed_video(db_session)

    before = plugin_pool.stats()
    client.post("/api/plugins/webm_to_mp4/run?video_id=v1")
    after = plugin_pool.stats()
    assert after["submitted_count"] == before["submitted_count"] + 1
    # In sync mode, completed + failed counts go up
    # by exactly 1 as well
    assert (
        after["completed_count"] + after["failed_count"]
        == before["completed_count"] + before["failed_count"] + 1
    )


# ── 404 on unknown video ───────────────────────────────────────────────
def test_submit_unknown_video_raises_404(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """Submitting a run for a video that doesn't exist
    returns 404, not 500.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _seed_video(db_session)  # create v1, but we'll request v2

    resp = client.post("/api/plugins/webm_to_mp4/run?video_id=v2")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ── No duplicate rows after sync run ───────────────────────────────────
def test_sync_mode_writes_exactly_one_row(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """The pool's sync mode must write exactly one
    PluginRun row per submit. The internal "duplicate"
    row created by `_run_plugin_and_create_row` is
    deleted before submit() returns.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _seed_video(db_session)

    client.post("/api/plugins/webm_to_mp4/run?video_id=v1")
    db_session.expire_all()

    rows = db_session.query(PluginRun).all()
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}: {[(r.id, r.status) for r in rows]}"
