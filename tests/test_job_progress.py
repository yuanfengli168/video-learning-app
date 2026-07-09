"""Tests for the background job tracker and progress-bar polling.

Covers:
- The in-memory job tracker (start / set_progress / finish)
- The /api/videos/{id}/status endpoint that the UI polls
- The ETA formatter (used by the frontend to show "About 3 minutes")
- The serialize / deserialize round-trip for the Video.last_*_job column
"""

import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import jobs as jobs_module
from app.jobs import (
    _reset_for_tests,
    finish_job,
    format_eta,
    get_job,
    serialize_job,
    set_progress,
    start_job,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

def _create_course_and_section(client: TestClient) -> tuple[str, str]:
    """Helper: create a course + section via the API."""
    with patch("app.auth.dependencies.verify_token", return_value={"uid": "u1", "email": "e@e.com"}):
        course_resp = client.post(
            "/api/courses",
            json={"title": "Test Course"},
            headers={"Authorization": "Bearer fake"},
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Section 1"},
            headers={"Authorization": "Bearer fake"},
        )
        section_id = section_resp.json()["section_id"]
    return course_id, section_id


def _upload_video(client: TestClient, section_id: str) -> str:
    """Helper: upload a small fake video, return video_id."""
    with patch("app.auth.dependencies.verify_token", return_value={"uid": "u1", "email": "e@e.com"}):
        fake = io.BytesIO(b"fake video content")
        resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("test.mp4", fake, "video/mp4")},
            headers={"Authorization": "Bearer fake"},
        )
    return resp.json()["video_id"]


@pytest.fixture(autouse=True)
def reset_job_state():
    """Ensure every test starts with a clean in-memory job tracker."""
    _reset_for_tests()
    yield
    _reset_for_tests()


# ── Job tracker unit tests ───────────────────────────────────────────────────

def test_start_job_initializes_state():
    job = start_job("vid-1", "transcribe", total=100, message="starting")
    assert job["video_id"] == "vid-1"
    assert job["job_type"] == "transcribe"
    assert job["status"] == "running"
    assert job["progress"] == 0
    assert job["total"] == 100
    assert job["pct"] == 0.0
    assert job["message"] == "starting"
    assert job["started_at"] > 0
    assert job["completed_at"] is None
    assert job["error"] is None


def test_get_job_returns_none_for_unknown():
    assert get_job("nope", "transcribe") is None
    assert get_job("vid-1", "generate") is None


def test_set_progress_clamps_to_100():
    """set_progress must clamp the percentage to [0, 100]."""
    job = start_job("vid-1", "transcribe", total=100)
    set_progress(job, done=150, total=100)
    assert job["pct"] == 100.0
    set_progress(job, done=-10, total=100)
    assert job["pct"] == 0.0


def test_set_progress_computes_pct():
    job = start_job("vid-1", "transcribe", total=200)
    set_progress(job, done=50, total=200)
    assert job["pct"] == 25.0


def test_set_progress_with_no_message_keeps_existing():
    job = start_job("vid-1", "transcribe", message="Building prompt...")
    set_progress(job, done=10, total=100, message="Transcribing segment 50...")
    assert job["message"] == "Transcribing segment 50..."
    set_progress(job, done=20, total=100)  # no message
    assert job["message"] == "Transcribing segment 50..."  # not clobbered


def test_finish_job_marks_completed():
    job = start_job("vid-1", "transcribe", total=100)
    finish_job(job, status="completed")
    assert job["status"] == "completed"
    assert job["pct"] == 100.0
    assert job["eta_seconds"] == 0
    assert job["completed_at"] is not None


def test_finish_job_marks_failed_with_error():
    job = start_job("vid-1", "transcribe")
    finish_job(job, status="failed", error="Out of memory")
    assert job["status"] == "failed"
    assert job["error"] == "Out of memory"
    assert job["pct"] == 100.0


def test_serialize_deserialize_roundtrip():
    job = start_job("vid-1", "transcribe", message="hello", total=100)
    set_progress(job, done=42, total=100, message="halfway")
    parsed = jobs_module.deserialize_job(serialize_job(job))
    assert parsed is not None
    assert parsed["video_id"] == "vid-1"
    assert parsed["progress"] == 42
    assert parsed["pct"] == 42.0
    assert parsed["message"] == "halfway"


def test_deserialize_handles_invalid_input():
    assert jobs_module.deserialize_job(None) is None
    assert jobs_module.deserialize_job("") is None
    assert jobs_module.deserialize_job("not json") is None


def test_format_eta_for_all_buckets():
    assert format_eta(None) == "Estimating..."
    assert format_eta(0) == "Almost done"
    assert format_eta(1) == "About 1 seconds remaining"
    assert format_eta(45) == "About 45 seconds remaining"
    assert format_eta(60) == "About 1 minute remaining"
    assert format_eta(125) == "About 2m 5s remaining"
    assert format_eta(3700) == "About 1h 1m remaining"


# ── /api/videos/{id}/status endpoint tests ──────────────────────────────────

def test_status_endpoint_returns_404_for_unknown_video(client: TestClient):
    with patch("app.auth.dependencies.verify_token", return_value={"uid": "u1", "email": "e@e.com"}):
        response = client.get(
            "/api/videos/nonexistent/status",
            headers={"Authorization": "Bearer fake"},
        )
    assert response.status_code == 404


def test_status_endpoint_returns_no_jobs_initially(client: TestClient):
    """A freshly uploaded video starts with a transcribe job already queued.

    MVP2.0 #1: upload now starts the transcribe job tracker immediately
    so the UI can poll /status right away. The no_auto_pipeline fixture
    (conftest.py) prevents the actual Whisper/Ollama work from running,
    but the job record itself IS created in the upload handler.
    """
    _, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)

    with patch("app.auth.dependencies.verify_token", return_value={"uid": "u1", "email": "e@e.com"}):
        response = client.get(
            f"/api/videos/{video_id}/status",
            headers={"Authorization": "Bearer fake"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["video_id"] == video_id
    assert data["video_status"] == "queued"
    # Transcribe job is started at upload time so the UI can poll
    assert data["transcribe_job"] is not None
    assert data["transcribe_job"]["status"] == "running"
    # Generate job not started yet (starts after transcribe completes)
    assert data["generate_job"] is None
    assert data["eta_text"]["generate"] is None


def test_status_endpoint_returns_running_job(client: TestClient):
    _, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)

    # Simulate a transcribe job in progress
    job = start_job(video_id, "transcribe", total=100, message="Loading model...")
    set_progress(job, done=10, total=100, message="Model loaded...")

    with patch("app.auth.dependencies.verify_token", return_value={"uid": "u1", "email": "e@e.com"}):
        response = client.get(
            f"/api/videos/{video_id}/status",
            headers={"Authorization": "Bearer fake"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["transcribe_job"] is not None
    assert data["transcribe_job"]["progress"] == 10
    assert data["transcribe_job"]["pct"] == 10.0
    assert data["transcribe_job"]["message"] == "Model loaded..."


def test_status_endpoint_enforces_ownership(client: TestClient):
    """A user should NOT be able to see another user's video status."""
    _, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)

    with patch("app.auth.dependencies.verify_token", return_value={"uid": "attacker", "email": "a@a.com"}):
        response = client.get(
            f"/api/videos/{video_id}/status",
            headers={"Authorization": "Bearer fake"},
        )
    assert response.status_code == 403


# ── Transcribe endpoint behavior change (202 Accepted) ─────────────────────

def test_transcribe_endpoint_returns_202_with_initial_job(client: TestClient):
    """POST /api/videos/{id}/transcribe should return 202 Accepted and
    an initial job dict. The actual transcription work happens in a
    FastAPI BackgroundTask (we don't wait for it)."""
    _, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)

    with patch("app.auth.dependencies.verify_token", return_value={"uid": "u1", "email": "e@e.com"}):
        with patch("app.routers.videos._run_transcribe_job") as mock_worker:
            response = client.post(
                f"/api/videos/{video_id}/transcribe?model_name=base",
                headers={"Authorization": "Bearer fake"},
            )
            assert response.status_code == 202
            assert mock_worker.called

        data = response.json()
        assert data["video_id"] == video_id
        assert data["status"] == "running"
        assert "job" in data
        assert data["job"]["job_type"] == "transcribe"
        assert data["job"]["status"] == "running"
        assert data["job"]["progress"] == 0

        # The /status endpoint should now show this job.
        status_response = client.get(
            f"/api/videos/{video_id}/status",
            headers={"Authorization": "Bearer fake"},
        )
        assert status_response.status_code == 200
        assert status_response.json()["transcribe_job"] is not None


# ── Generate endpoint behavior change (202 Accepted) ───────────────────────

def test_generate_endpoint_returns_202_with_initial_job(client: TestClient):
    """POST /api/generate/{id} should also return 202 + initial job state."""
    from unittest.mock import patch
    from app.services.transcription import transcript_to_json

    _, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)

    # Mock the transcription worker so we don't need real Whisper.
    # The worker writes a transcript asset to the DB; we just need
    # that to exist for the generate endpoint to accept the request.
    def fake_transcribe(video_id_arg, model_name):
        from app.database import SessionLocal
        from app.models import Asset, Video
        from app.services.transcription import transcript_to_json as to_json
        with SessionLocal() as db:
            v = db.get(Video, video_id_arg)
            if v:
                # Insert a transcript asset directly via the same DB
                db.add(Asset(
                    id="a-trans-" + video_id_arg[:8],
                    video_id=video_id_arg,
                    asset_type="transcript",
                    content=to_json({
                        "segments": [{"start": 0, "end": 1, "text": "hello"}],
                        "language": "en",
                        "duration": 1,
                    }),
                ))
                v.status = "ready"
                v.duration = 1.0
                db.commit()
    # Trigger the fake transcribe via the real endpoint
    with patch("app.routers.videos._run_transcribe_job", side_effect=fake_transcribe):
        with patch("app.auth.dependencies.verify_token", return_value={"uid": "u1", "email": "e@e.com"}):
            resp = client.post(
                f"/api/videos/{video_id}/transcribe?model_name=tiny",
                headers={"Authorization": "Bearer fake"},
            )
    assert resp.status_code == 202

    with patch("app.auth.dependencies.verify_token", return_value={"uid": "u1", "email": "e@e.com"}):
        with patch("app.routers.generation._run_generate_job") as mock_worker:
            response = client.post(
                f"/api/generate/{video_id}",
                headers={"Authorization": "Bearer fake"},
            )
            assert response.status_code == 202
            assert mock_worker.called

        data = response.json()
        assert data["status"] == "running"
        assert data["job"]["job_type"] == "generate"
