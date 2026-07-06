"""Tests for generation router."""

import io
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}
FAKE_MATERIALS = {
    "summary": "# Summary\nKey points.",
    "mindmap": "# Topic\n## Branch",
    "flashcards": [{"term": "AI", "definition": "Artificial Intelligence"}],
    "quiz": [{"question": "What?", "options": ["A", "B", "C", "D"], "answer": "A", "answer_index": 0}],
    "topic_timestamps": [
        {"topic": "Topic", "start": 0, "end": 60},
        {"topic": "Branch", "start": 60, "end": 120},
    ],
}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def _setup_video_with_transcript(client: TestClient):
    """Helper: create course → section → video → transcript. Returns video_id."""
    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]

        fake_video = io.BytesIO(b"fake video content")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]

        fake_transcript = {
            "segments": [{"start": 0.0, "end": 5.0, "text": "Hello"}],
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
    return video_id


def test_generate_success(client: TestClient):
    """Should generate learning materials."""
    video_id = _setup_video_with_transcript(client)

    with _mock_auth():
        with patch(
            "app.routers.generation.generate_materials",
            return_value=FAKE_MATERIALS,
        ):
            response = client.post(
                f"/api/generate/{video_id}", headers=_auth_headers()
            )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["flashcard_count"] == 1
    assert data["quiz_count"] == 1


def test_generate_saves_topic_timestamps(client: TestClient):
    """Generate should save topic_timestamps asset."""
    video_id = _setup_video_with_transcript(client)

    with _mock_auth():
        with patch(
            "app.routers.generation.generate_materials",
            return_value=FAKE_MATERIALS,
        ):
            client.post(f"/api/generate/{video_id}", headers=_auth_headers())
            response = client.get(
                f"/api/generate/{video_id}/assets/topic_timestamps",
                headers=_auth_headers(),
            )
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "topic_timestamps"
    assert len(data["data"]) == 2
    assert data["data"][0]["topic"] == "Topic"
    assert data["data"][0]["start"] == 0
    assert data["data"][1]["topic"] == "Branch"


def test_generate_no_transcript(client: TestClient):
    """Should return 400 if no transcript exists."""
    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]

        fake_video = io.BytesIO(b"fake")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]

        response = client.post(
            f"/api/generate/{video_id}", headers=_auth_headers()
        )
    assert response.status_code == 400
    assert "No transcript" in response.json()["detail"]


def test_generate_video_not_found(client: TestClient):
    """Should return 404 for non-existent video."""
    with _mock_auth():
        response = client.post(
            "/api/generate/nonexistent", headers=_auth_headers()
        )
    assert response.status_code == 404


def test_generate_failure(client: TestClient):
    """Should set status to error and return 500 on generation failure."""
    video_id = _setup_video_with_transcript(client)

    with _mock_auth():
        with patch(
            "app.routers.generation.generate_materials",
            side_effect=RuntimeError("Ollama down"),
        ):
            response = client.post(
                f"/api/generate/{video_id}", headers=_auth_headers()
            )
    assert response.status_code == 500


def test_get_asset_summary(client: TestClient):
    """Should get the summary asset."""
    video_id = _setup_video_with_transcript(client)

    with _mock_auth():
        with patch(
            "app.routers.generation.generate_materials",
            return_value=FAKE_MATERIALS,
        ):
            client.post(f"/api/generate/{video_id}", headers=_auth_headers())
            response = client.get(
                f"/api/generate/{video_id}/assets/summary",
                headers=_auth_headers(),
            )
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "summary"
    assert "Key points" in data["data"]


def test_get_asset_flashcards(client: TestClient):
    """Should get flashcards as structured JSON."""
    video_id = _setup_video_with_transcript(client)

    with _mock_auth():
        with patch(
            "app.routers.generation.generate_materials",
            return_value=FAKE_MATERIALS,
        ):
            client.post(f"/api/generate/{video_id}", headers=_auth_headers())
            response = client.get(
                f"/api/generate/{video_id}/assets/flashcards",
                headers=_auth_headers(),
            )
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "flashcards"
    assert isinstance(data["data"], list)
    assert data["data"][0]["term"] == "AI"


def test_get_asset_quiz(client: TestClient):
    """Should get quiz as structured JSON."""
    video_id = _setup_video_with_transcript(client)

    with _mock_auth():
        with patch(
            "app.routers.generation.generate_materials",
            return_value=FAKE_MATERIALS,
        ):
            client.post(f"/api/generate/{video_id}", headers=_auth_headers())
            response = client.get(
                f"/api/generate/{video_id}/assets/quiz",
                headers=_auth_headers(),
            )
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "quiz"
    assert data["data"][0]["question"] == "What?"


def test_get_asset_mindmap(client: TestClient):
    """Should get mindmap as markdown text."""
    video_id = _setup_video_with_transcript(client)

    with _mock_auth():
        with patch(
            "app.routers.generation.generate_materials",
            return_value=FAKE_MATERIALS,
        ):
            client.post(f"/api/generate/{video_id}", headers=_auth_headers())
            response = client.get(
                f"/api/generate/{video_id}/assets/mindmap",
                headers=_auth_headers(),
            )
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "mindmap"
    assert "# Topic" in data["data"]


def test_get_asset_not_found(client: TestClient):
    """Should return 404 if asset not generated."""
    video_id = _setup_video_with_transcript(client)

    with _mock_auth():
        response = client.get(
            f"/api/generate/{video_id}/assets/summary",
            headers=_auth_headers(),
        )
    assert response.status_code == 404


def test_get_asset_invalid_type(client: TestClient):
    """Should return 400 for invalid asset type."""
    video_id = _setup_video_with_transcript(client)

    with _mock_auth():
        response = client.get(
            f"/api/generate/{video_id}/assets/nonexistent_type",
            headers=_auth_headers(),
        )
    assert response.status_code == 400


def test_generate_regenerates_overwrite(client: TestClient):
    """Generating again should overwrite existing assets."""
    video_id = _setup_video_with_transcript(client)

    new_materials = {
        "summary": "# New Summary",
        "mindmap": "# New Map",
        "flashcards": [{"term": "ML", "definition": "Machine Learning"}],
        "quiz": [],
    }

    with _mock_auth():
        with patch(
            "app.routers.generation.generate_materials",
            return_value=FAKE_MATERIALS,
        ):
            client.post(f"/api/generate/{video_id}", headers=_auth_headers())

        with patch(
            "app.routers.generation.generate_materials",
            return_value=new_materials,
        ):
            client.post(f"/api/generate/{video_id}", headers=_auth_headers())

            response = client.get(
                f"/api/generate/{video_id}/assets/summary",
                headers=_auth_headers(),
            )
    assert response.status_code == 200
    assert response.json()["data"] == "# New Summary"