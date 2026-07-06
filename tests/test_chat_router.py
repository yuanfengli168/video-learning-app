"""Tests for chat router — create sessions, send messages, list/delete."""

import io
from unittest.mock import patch

from fastapi.testclient import TestClient

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def _setup_video(client: TestClient):
    """Helper: create course → section → video. Returns video_id."""
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
    return upload_resp.json()["video_id"]


def test_create_chat_session(client: TestClient):
    """Should create a chat session for a concept."""
    video_id = _setup_video(client)

    with _mock_auth():
        response = client.post(
            "/api/chat/sessions",
            json={"video_id": video_id, "concept": "RAG"},
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert data["concept"] == "RAG"
    assert "real-world" in data["system_prompt"].lower()


def test_create_chat_session_video_not_found(client: TestClient):
    """Should return 404 for non-existent video."""
    with _mock_auth():
        response = client.post(
            "/api/chat/sessions",
            json={"video_id": "nonexistent", "concept": "RAG"},
            headers=_auth_headers(),
        )
    assert response.status_code == 404


def test_send_message(client: TestClient):
    """Should send a message and get an AI response."""
    video_id = _setup_video(client)

    with _mock_auth():
        # Create session
        session_resp = client.post(
            "/api/chat/sessions",
            json={"video_id": video_id, "concept": "RAG"},
            headers=_auth_headers(),
        )
        session_id = session_resp.json()["session_id"]

        # Send message
        with patch(
            "app.routers.chat.chat_with_ollama",
            return_value="RAG is used in search engines...",
        ):
            response = client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "How is RAG used in the real world?"},
                headers=_auth_headers(),
            )
    assert response.status_code == 200
    data = response.json()
    assert data["user_message"]["content"] == "How is RAG used in the real world?"
    assert data["ai_message"]["content"] == "RAG is used in search engines..."


def test_send_message_session_not_found(client: TestClient):
    """Should return 404 for non-existent session."""
    with _mock_auth():
        response = client.post(
            "/api/chat/sessions/nonexistent/messages",
            json={"content": "Hello"},
            headers=_auth_headers(),
        )
    assert response.status_code == 404


def test_send_message_ollama_failure(client: TestClient):
    """Should return 500 when Ollama fails."""
    video_id = _setup_video(client)

    with _mock_auth():
        session_resp = client.post(
            "/api/chat/sessions",
            json={"video_id": video_id, "concept": "RAG"},
            headers=_auth_headers(),
        )
        session_id = session_resp.json()["session_id"]

        with patch(
            "app.routers.chat.chat_with_ollama",
            side_effect=RuntimeError("Ollama down"),
        ):
            response = client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "Hello"},
                headers=_auth_headers(),
            )
    assert response.status_code == 500


def test_get_chat_session(client: TestClient):
    """Should get a chat session with messages."""
    video_id = _setup_video(client)

    with _mock_auth():
        session_resp = client.post(
            "/api/chat/sessions",
            json={"video_id": video_id, "concept": "RAG"},
            headers=_auth_headers(),
        )
        session_id = session_resp.json()["session_id"]

        with patch(
            "app.routers.chat.chat_with_ollama",
            return_value="RAG is used in...",
        ):
            client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "How?"},
                headers=_auth_headers(),
            )

        response = client.get(
            f"/api/chat/sessions/{session_id}", headers=_auth_headers()
        )
    assert response.status_code == 200
    data = response.json()
    assert data["concept"] == "RAG"
    assert len(data["messages"]) == 2  # user + assistant
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][1]["role"] == "assistant"


def test_get_chat_session_not_found(client: TestClient):
    """Should return 404 for non-existent session."""
    with _mock_auth():
        response = client.get(
            "/api/chat/sessions/nonexistent", headers=_auth_headers()
        )
    assert response.status_code == 404


def test_list_chat_sessions(client: TestClient):
    """Should list all chat sessions for the user."""
    video_id = _setup_video(client)

    with _mock_auth():
        # Create two sessions
        client.post(
            "/api/chat/sessions",
            json={"video_id": video_id, "concept": "RAG"},
            headers=_auth_headers(),
        )
        client.post(
            "/api/chat/sessions",
            json={"video_id": video_id, "concept": "Transformer"},
            headers=_auth_headers(),
        )

        response = client.get("/api/chat/sessions", headers=_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    concepts = {s["concept"] for s in data}
    assert "RAG" in concepts
    assert "Transformer" in concepts


def test_list_chat_sessions_empty(client: TestClient):
    """Should return empty list for user with no sessions."""
    with _mock_auth():
        response = client.get("/api/chat/sessions", headers=_auth_headers())
    assert response.status_code == 200
    assert response.json() == []


def test_delete_chat_session(client: TestClient):
    """Should delete a chat session."""
    video_id = _setup_video(client)

    with _mock_auth():
        session_resp = client.post(
            "/api/chat/sessions",
            json={"video_id": video_id, "concept": "RAG"},
            headers=_auth_headers(),
        )
        session_id = session_resp.json()["session_id"]

        response = client.delete(
            f"/api/chat/sessions/{session_id}", headers=_auth_headers()
        )
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"


def test_delete_chat_session_not_found(client: TestClient):
    """Should return 404 for non-existent session."""
    with _mock_auth():
        response = client.delete(
            "/api/chat/sessions/nonexistent", headers=_auth_headers()
        )
    assert response.status_code == 404


def test_chat_session_ownership(client: TestClient):
    """Should not allow access to another user's chat session."""
    # Create video as user-A
    with patch("app.auth.dependencies.verify_token", return_value={"uid": "user-A"}):
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

        session_resp = client.post(
            "/api/chat/sessions",
            json={"video_id": video_id, "concept": "RAG"},
            headers=_auth_headers(),
        )
        session_id = session_resp.json()["session_id"]

    # Try to access as user-B
    with patch("app.auth.dependencies.verify_token", return_value={"uid": "user-B"}):
        response = client.get(
            f"/api/chat/sessions/{session_id}", headers=_auth_headers()
        )
    assert response.status_code == 403


def test_unauthorized_chat_access(client: TestClient):
    """Should return 401 without auth."""
    response = client.get("/api/chat/sessions")
    assert response.status_code == 401