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
    """Should transcribe a video and store transcript."""
    course_id, section_id = _create_course_and_section(client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]

        # Mock the transcription
        fake_transcript = {
            "segments": [{"start": 0.0, "end": 2.0, "text": "Hello"}],
            "language": "en",
            "duration": 10.0,
        }
        with patch(
            "app.routers.videos.transcribe_video",
            return_value=fake_transcript,
        ):
            response = client.post(
                f"/api/videos/{video_id}/transcribe?model_name=base",
                headers=_auth_headers(),
            )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["segments"] == 1
    assert data["language"] == "en"


def test_get_transcript(client: TestClient):
    """Should get the transcript after transcription."""
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
        with patch(
            "app.routers.videos.transcribe_video",
            return_value=fake_transcript,
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
    """Should set video status to 'error' if transcription fails."""
    course_id, section_id = _create_course_and_section(client)

    fake_video = io.BytesIO(b"fake video content")
    with _mock_auth():
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]

        with patch(
            "app.routers.videos.transcribe_video",
            side_effect=RuntimeError("Whisper crashed"),
        ):
            response = client.post(
                f"/api/videos/{video_id}/transcribe?model_name=base",
                headers=_auth_headers(),
            )
    assert response.status_code == 500

    # Verify status was set to error
    with _mock_auth():
        get_resp = client.get(
            f"/api/videos/{video_id}", headers=_auth_headers()
        )
    assert get_resp.json()["status"] == "error"