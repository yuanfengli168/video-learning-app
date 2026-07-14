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

# ─────────────────────────────────────────────────────────────────────────────
# Video-scope chat (MVP2.0 — "💬 Discuss" tab on the video page)
# ─────────────────────────────────────────────────────────────────────────────


def test_create_video_chat_session_success(client: TestClient):
    """Should create a video-scope session with a system prompt that
    mentions the video's materials. The placeholder concept is stored
    so the NOT NULL column is satisfied, and scope='video' is set."""
    video_id = _setup_video(client)
    with _mock_auth():
        # Add some assets so the prompt has real content
        from app.database import SessionLocal
        from app.models import Asset
        with SessionLocal() as db:
            db.add(Asset(
                id=f"sum-{video_id}", video_id=video_id,
                asset_type="summary", content="This video covers RAG."
            ))
            db.add(Asset(
                id=f"mm-{video_id}", video_id=video_id,
                asset_type="mindmap", content="- RAG\n  - retrieval\n  - gen"
            ))
            db.add(Asset(
                id=f"q-{video_id}", video_id=video_id,
                asset_type="quiz",
                content='[{"question": "What is RAG?", "options": ["Retrieval", "Reactive"], "correct_index": 0}]',
            ))
            db.add(Asset(
                id=f"t-{video_id}", video_id=video_id,
                asset_type="transcript",
                content='{"segments": [{"start": 0, "end": 5, "text": "Hello"}], "language": "en", "duration": 5}',
            ))
            db.commit()

        response = client.post(
            "/api/chat/video-sessions",
            json={"video_id": video_id},
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert data["scope"] == "video"
    # System prompt should reference the assets
    assert "RAG" in data["system_prompt"]
    assert "retrieval" in data["system_prompt"]


def test_create_video_chat_session_empty_materials(client: TestClient):
    """When a video has no assets yet (just uploaded), the chat
    should still be creatable — the prompt uses friendly
    placeholders for each empty section."""
    video_id = _setup_video(client)
    with _mock_auth():
        response = client.post(
            "/api/chat/video-sessions",
            json={"video_id": video_id},
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    data = response.json()
    assert data["scope"] == "video"
    # Friendly placeholders, not "None" or empty
    prompt = data["system_prompt"]
    assert "No summary" in prompt
    assert "No mindmap" in prompt
    assert "No quiz" in prompt
    assert "No transcript" in prompt


def test_create_video_chat_session_video_not_found(client: TestClient):
    """Should return 404 for non-existent video."""
    with _mock_auth():
        response = client.post(
            "/api/chat/video-sessions",
            json={"video_id": "nonexistent"},
            headers=_auth_headers(),
        )
    assert response.status_code == 404


def test_create_video_chat_session_wrong_user(client: TestClient):
    """Should return 403 for video not owned by the user."""
    video_id = _setup_video(client)
    with patch("app.auth.dependencies.verify_token", return_value={"uid": "user-B"}):
        response = client.post(
            "/api/chat/video-sessions",
            json={"video_id": video_id},
            headers=_auth_headers(),
        )
    assert response.status_code == 403


def test_send_message_in_video_scope_session(client: TestClient):
    """Sending a message in a video-scope session should work the
    same as a flashcard-scope session — the /messages endpoint is
    scope-agnostic. The chat is about the whole video though, so
    the AI sees the full transcript context."""
    video_id = _setup_video(client)
    with _mock_auth():
        # Create a video session
        session_resp = client.post(
            "/api/chat/video-sessions",
            json={"video_id": video_id},
            headers=_auth_headers(),
        )
        session_id = session_resp.json()["session_id"]

        # Mock the Ollama call
        with patch("app.routers.chat.chat_with_ollama", return_value="The video covers RAG concepts."):
            response = client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "What does this video cover?"},
                headers=_auth_headers(),
            )
    assert response.status_code == 200
    data = response.json()
    assert data["ai_message"]["content"] == "The video covers RAG concepts."


def test_list_sessions_includes_scope(client: TestClient):
    """The list endpoint should return the scope field so the
    chat history UI can show 'Video' vs 'Flashcard' badges."""
    video_id = _setup_video(client)
    with _mock_auth():
        # One flashcard-scope session
        client.post(
            "/api/chat/sessions",
            json={"video_id": video_id, "concept": "RAG"},
            headers=_auth_headers(),
        )
        # One video-scope session
        client.post(
            "/api/chat/video-sessions",
            json={"video_id": video_id},
            headers=_auth_headers(),
        )
        response = client.get("/api/chat/sessions", headers=_auth_headers())
    assert response.status_code == 200
    sessions = response.json()
    assert len(sessions) == 2
    scopes = {s["scope"] for s in sessions}
    assert scopes == {"flashcard", "video"}
    # The video-scope one has the placeholder concept
    video_session = next(s for s in sessions if s["scope"] == "video")
    assert video_session["concept"] == "[whole video]"


def test_get_video_session_includes_scope(client: TestClient):
    """GET /sessions/{id} should also return scope."""
    video_id = _setup_video(client)
    with _mock_auth():
        create_resp = client.post(
            "/api/chat/video-sessions",
            json={"video_id": video_id},
            headers=_auth_headers(),
        )
        session_id = create_resp.json()["session_id"]
        response = client.get(
            f"/api/chat/sessions/{session_id}", headers=_auth_headers()
        )
    assert response.status_code == 200
    data = response.json()
    assert data["scope"] == "video"


# ─────────────────────────────────────────────────────────────────────────────
# Citations (MVP3.0 Part B, manualTodo [jul14] #6)
# ─────────────────────────────────────────────────────────────────────────────
#
# When the AI responds with [M:SS] / [H:MM:SS] markers in a video-scope
# chat, the response should include a structured `citations` list so the
# frontend can render clickable seek links. Flashcard-scope chats don't
# have a transcript to cite from, so the citations list is always empty
# there (even if the LLM invents markers — defense in depth).


def _create_video_session(client: TestClient) -> str:
    """Helper: create a video-scope session for the freshly-uploaded test video."""
    video_id = _setup_video(client)
    with _mock_auth():
        resp = client.post(
            "/api/chat/video-sessions",
            json={"video_id": video_id},
            headers=_auth_headers(),
        )
    return resp.json()["session_id"]


def test_video_scope_response_includes_empty_citations_list(client: TestClient):
    """Video-scope response shape includes a (possibly empty) citations
    list, even when the AI didn't cite any timestamps."""
    session_id = _create_video_session(client)
    with _mock_auth():
        with patch(
            "app.routers.chat.chat_with_ollama",
            return_value="这是普通的中文回答，没有时间戳。",
        ):
            response = client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "Q?"},
                headers=_auth_headers(),
            )
    assert response.status_code == 200
    data = response.json()
    assert "citations" in data
    assert data["citations"] == []


def test_video_scope_response_parses_mmss_citations(client: TestClient):
    """A response containing [M:SS] markers returns a populated citations list."""
    session_id = _create_video_session(client)
    with _mock_auth():
        with patch(
            "app.routers.chat.chat_with_ollama",
            return_value="视频在 [3:45] 提到 Claude Code 需要付费。",
        ):
            response = client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "Why?"},
                headers=_auth_headers(),
            )
    data = response.json()
    assert len(data["citations"]) == 1
    cite = data["citations"][0]
    assert cite["start_seconds"] == 225.0
    assert cite["display"] == "[3:45]"
    assert "offset" in cite
    assert "raw" in cite


def test_video_scope_response_parses_hhmmss_citations(client: TestClient):
    """[H:MM:SS] markers work for > 1h videos."""
    session_id = _create_video_session(client)
    with _mock_auth():
        with patch(
            "app.routers.chat.chat_with_ollama",
            return_value="See [1:30:45] for the deep dive.",
        ):
            response = client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "Show me"},
                headers=_auth_headers(),
            )
    data = response.json()
    assert len(data["citations"]) == 1
    assert data["citations"][0]["start_seconds"] == 1 * 3600 + 30 * 60 + 45
    assert data["citations"][0]["display"] == "[1:30:45]"


def test_video_scope_response_parses_multiple_citations(client: TestClient):
    """Multiple markers in one response produce a list of citations in order."""
    session_id = _create_video_session(client)
    with _mock_auth():
        with patch(
            "app.routers.chat.chat_with_ollama",
            return_value="At [0:30] we start, and at [5:00] we pivot.",
        ):
            response = client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "Summary?"},
                headers=_auth_headers(),
            )
    data = response.json()
    citations = data["citations"]
    assert len(citations) == 2
    assert citations[0]["start_seconds"] == 30.0
    assert citations[1]["start_seconds"] == 300.0
    # Offsets in source order
    assert citations[0]["offset"] < citations[1]["offset"]


def test_flashcard_scope_response_has_empty_citations(client: TestClient):
    """Flashcard-scope sessions (per-concept) don't get a citations
    list — there's no transcript to cite from. Even if the AI
    happens to write [3:45], we don't return it as a citation."""
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
            return_value="RAG is mentioned at [10:00] in the original paper.",
        ):
            response = client.post(
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "Tell me more"},
                headers=_auth_headers(),
            )
    data = response.json()
    # The AI text is preserved as-is, but citations is [] for flashcard scope.
    assert "citations" in data
    assert data["citations"] == []
