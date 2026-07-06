"""Tests for frontend template UI features — search nav, summary loading, mindmap button."""

from unittest.mock import patch

from fastapi.testclient import TestClient

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com", "name": "Test"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def test_video_page_has_transcribe_button(client: TestClient):
    """Video page should have a Transcribe button."""
    import io

    with _mock_auth():
        # Create course + section + video
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
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    assert "transcribe-btn" in response.text
    assert "Transcribe" in response.text


def test_video_page_has_whisper_model_selector(client: TestClient):
    """Video page should have a Whisper model dropdown."""
    import io

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
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    assert "whisper-model" in response.text
    assert "tiny" in response.text
    assert "base" in response.text
    assert "small" in response.text
    assert "medium" in response.text


def test_video_page_has_search_navigation(client: TestClient):
    """Video page should have search navigation (prev/next/clear)."""
    import io

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
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    assert "search-nav" in response.text
    assert "searchNavigate" in response.text
    assert "clearSearch" in response.text


def test_video_page_has_mindmap_fullscreen_button(client: TestClient):
    """Video page should have a mindmap fullscreen expand button."""
    import io

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
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    assert "openMindmapFullscreen" in response.text
    assert "closeMindmapFullscreen" in response.text
    assert "Expand to Full Screen" in response.text


def test_video_page_has_summary_loading_function(client: TestClient):
    """Video page should have loadSummary function."""
    import io

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
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    assert "loadSummary" in response.text
    assert "simpleMarkdown" in response.text


def test_dashboard_has_create_course_js(client: TestClient):
    """Dashboard should use JS fetch for course creation (not HTMX)."""
    with _mock_auth():
        response = client.get("/", headers=_auth_headers())

    assert response.status_code == 200
    assert "createCourse" in response.text
    # Should NOT have hx-post for course creation
    assert "hx-post" not in response.text


def test_course_page_has_upload_function(client: TestClient):
    """Course page should have uploadVideo function."""
    import io

    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        response = client.get(f"/course/{course_id}", headers=_auth_headers())

    assert response.status_code == 200
    assert "uploadVideo" in response.text
    assert "createSection" in response.text

def test_video_page_has_topic_banner(client: TestClient):
    """Video page should have a topic notification banner (hidden by default)."""
    import io

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
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    # Banner element
    assert "topic-banner" in response.text
    assert "topic-banner-name" in response.text
    assert "topic-banner-time" in response.text


def test_video_page_has_topic_click_functions(client: TestClient):
    """Video page should have JS functions for clicking mindmap topics."""
    import io

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
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    # Required functions for the topic click flow
    assert "function jumpToTopic" in response.text
    assert "function showTopicBanner" in response.text
    assert "function closeTopicBanner" in response.text
    assert "function highlightTranscriptRange" in response.text
    assert "function findTopicTimestamp" in response.text
    assert "function attachMindmapClickHandler" in response.text
    # Should fetch topic_timestamps asset
    assert "/assets/topic_timestamps" in response.text
    assert "topicTimestamps" in response.text
