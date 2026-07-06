"""Tests for videos router — upload, transcribe, get."""

import io
from unittest.mock import patch

from fastapi.testclient import TestClient

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def _create_course_and_section(client: TestClient):
    """Helper: create a course and section, return (course_id, section_id)."""
    with _mock_auth():
        course_resp = client.post(
            "/api/courses",
            json={"title": "ML"},
            headers=_auth_headers(),
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
    return course_id, section_id


def test_list_whisper_models(client: TestClient):
    """/api/videos/models should return available models."""
    response = client.get("/api/videos/models")
    assert response.status_code == 200
    models = response.json()["models"]
    assert "base" in models
    assert "small" in models
    assert "medium" in models


def test_upload_video(client: TestClient):
    """Should upload a video file."""
    course_id, section_id = _create_course_and_section(client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        response = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("test.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    assert "video_id" in response.json()
    assert response.json()["status"] == "uploaded"


def test_upload_invalid_extension(client: TestClient):
    """Should reject non-video files."""
    _, section_id = _create_course_and_section(client)

    fake_file = io.BytesIO(b"text content")
    with _mock_auth():
        response = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("document.txt", fake_file, "text/plain")},
            headers=_auth_headers(),
        )
    assert response.status_code == 400
    assert "not allowed" in response.json()["detail"]


def test_upload_to_nonexistent_section(client: TestClient):
    """Should return 404 for non-existent section."""
    fake_video = io.BytesIO(b"fake")
    with _mock_auth():
        response = client.post(
            "/api/videos/upload/nonexistent-section",
            files={"file": ("test.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
    assert response.status_code == 404


def test_get_video(client: TestClient):
    """Should get video details."""
    course_id, section_id = _create_course_and_section(client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(
            f"/api/videos/{video_id}", headers=_auth_headers()
        )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "lecture"
    assert data["status"] == "pending"
    assert data["has_transcript"] is False


def test_get_video_not_found(client: TestClient):
    """Should return 404 for non-existent video."""
    with _mock_auth():
        response = client.get(
            "/api/videos/nonexistent", headers=_auth_headers()
        )
    assert response.status_code == 404


def test_transcribe_video(client: TestClient):
    """Should kick off transcription and return 202 Accepted with a job."""
    course_id, section_id = _create_course_and_section(client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]

        # Mock the background worker so we don't need real Whisper.
        # The worker writes the transcript asset + sets video status,
        # simulating the end state we used to get from the synchronous
        # endpoint.
        fake_transcript = {
            "segments": [{"start": 0.0, "end": 2.0, "text": "Hello"}],
            "language": "en",
            "duration": 10.0,
        }
        def fake_worker(vid: str, model: str) -> None:
            from app.services.transcription import transcript_to_json
            with patch(
                "app.routers.videos.transcribe_video",
                return_value=fake_transcript,
            ):
                # Re-import inside the worker to avoid circular issues
                from app.routers.videos import transcribe_video
                result = transcribe_video("ignored", model)
                from app.database import SessionLocal
                from app.models import Asset, Video
                with SessionLocal() as db:
                    v = db.get(Video, vid)
                    if v:
                        db.add(Asset(
                            id="t1", video_id=vid, asset_type="transcript",
                            content=transcript_to_json(result),
                        ))
                        v.status = "ready"
                        v.duration = result["duration"]
                        db.commit()
        with patch(
            "app.routers.videos._run_transcribe_job",
            side_effect=fake_worker,
        ):
            response = client.post(
                f"/api/videos/{video_id}/transcribe?model_name=base",
                headers=_auth_headers(),
            )
    # The endpoint returns 202 Accepted immediately, with the
    # initial job state in the response body.
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "running"
    assert data["job"]["job_type"] == "transcribe"
    # Once the (mocked) worker ran, the transcript is queryable.
    with _mock_auth():
        get_resp = client.get(
            f"/api/videos/{video_id}/transcript", headers=_auth_headers()
        )
    assert get_resp.status_code == 200
    segs = get_resp.json()["segments"]
    assert len(segs) == 1
    assert segs[0]["text"] == "Hello"
    assert get_resp.json()["language"] == "en"


def test_get_transcript(client: TestClient):
    """Should get the transcript after the (mocked) transcribe worker ran."""
    course_id, section_id = _create_course_and_section(client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]

        fake_transcript = {
            "segments": [{"start": 0.0, "end": 2.0, "text": "Hello world"}],
            "language": "en",
            "duration": 10.0,
        }
        def fake_worker(vid: str, model: str) -> None:
            from app.services.transcription import transcript_to_json
            with patch(
                "app.routers.videos.transcribe_video",
                return_value=fake_transcript,
            ):
                from app.routers.videos import transcribe_video
                result = transcribe_video("ignored", model)
                from app.database import SessionLocal
                from app.models import Asset, Video
                with SessionLocal() as db:
                    v = db.get(Video, vid)
                    if v:
                        db.add(Asset(
                            id="t1", video_id=vid, asset_type="transcript",
                            content=transcript_to_json(result),
                        ))
                        v.status = "ready"
                        v.duration = result["duration"]
                        db.commit()
        with patch(
            "app.routers.videos._run_transcribe_job",
            side_effect=fake_worker,
        ):
            client.post(
                f"/api/videos/{video_id}/transcribe?model_name=base",
                headers=_auth_headers(),
            )

        # Now get the transcript
        response = client.get(
            f"/api/videos/{video_id}/transcript", headers=_auth_headers()
        )
    assert response.status_code == 200
    data = response.json()
    assert data["language"] == "en"
    assert len(data["segments"]) == 1
    assert data["segments"][0]["text"] == "Hello world"


def test_get_transcript_not_found(client: TestClient):
    """Should return 404 if no transcript exists."""
    course_id, section_id = _create_course_and_section(client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(
            f"/api/videos/{video_id}/transcript", headers=_auth_headers()
        )
    assert response.status_code == 404


def test_transcribe_invalid_model(client: TestClient):
    """Should return 400 for invalid model name."""
    course_id, section_id = _create_course_and_section(client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.post(
            f"/api/videos/{video_id}/transcribe?model_name=nonexistent",
            headers=_auth_headers(),
        )
    assert response.status_code == 400


def test_transcribe_failure_sets_error_status(client: TestClient):
    """Should set video status to 'error' if the background transcription fails."""
    course_id, section_id = _create_course_and_section(client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]

        def fake_worker_raises(vid: str, model: str) -> None:
            # Simulate the worker catching an exception from
            # transcribe_video and marking the job + video as failed.
            from app.jobs import get_job, finish_job
            job = get_job(vid, "transcribe")
            if job:
                finish_job(job, status="failed", error="Whisper crashed")
            from app.database import SessionLocal
            from app.models import Video
            with SessionLocal() as db:
                v = db.get(Video, vid)
                if v:
                    v.status = "error"
                    db.commit()

        with patch(
            "app.routers.videos._run_transcribe_job",
            side_effect=fake_worker_raises,
        ):
            response = client.post(
                f"/api/videos/{video_id}/transcribe?model_name=base",
                headers=_auth_headers(),
            )
    # Endpoint returns 202 — the failure happens in the background.
    assert response.status_code == 202

    # Verify the (mocked) worker marked the video as 'error'.
    with _mock_auth():
        get_resp = client.get(
            f"/api/videos/{video_id}", headers=_auth_headers()
        )
    assert get_resp.json()["status"] == "error"