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


def test_video_page_preloads_markmap_script(client: TestClient):
    """Video page should pre-load the Markmap CDN script on page load to
    avoid the multi-second cold start when the user first clicks the
    mindmap tab."""
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
    # The page should pre-load the Markmap script
    assert "preloadMarkmapScript" in response.text
    assert "markmap-autoloader" in response.text
    # Loading state should be shown to the user (so the tab is never blank)
    assert "mindmap-loading" in response.text or "Loading mindmap" in response.text


def test_mindmap_node_has_click_tooltip_and_pointer_cursor(client: TestClient):
    """Mindmap nodes should show a pointer cursor and a tooltip saying
    'Click to watch this part of the video'."""
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
    # Pointer cursor
    assert "node.style.cursor = 'pointer'" in response.text
    # Tooltip
    assert "Click to watch this part of the video" in response.text
    # jumpToTopic should close the fullscreen modal first
    assert "closeMindmapFullscreen()" in response.text


def test_video_page_has_mindmap_parent_map_ancestor_lookup(client: TestClient):
    """When a user clicks a leaf node that has no exact timestamp, the
    page should walk up the mindmap tree to find the closest ancestor
    that does. This prevents the 'No timestamp info' error for deeply
    nested topics that the LLM didn't enumerate directly.
    """
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
    # Parent map builder and ancestor walker must exist
    assert "function buildMindmapParentMap" in response.text
    assert "function findTopicTimestampWithAncestors" in response.text
    # The parent map should be populated when the mindmap loads
    assert "mindmapParentMap = buildMindmapParentMap" in response.text
    assert "mindmapParentMap" in response.text
    # jumpToTopic must call the ancestor-aware version
    assert "findTopicTimestampWithAncestors(topicName, mindmapParentMap)" in response.text


def test_video_page_has_graceful_toast_not_alert(client: TestClient):
    """The page should show a non-blocking toast (not an alert) when no
    timestamp is found, so the user can keep interacting with the mindmap.
    """
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
    # Toast helper exists
    assert "function showToast" in response.text
    # jumpToTopic should call showToast, not alert
    assert "showToast(" in response.text
    # Old alert() with 'No timestamp info' should be gone
    assert "alert(`No timestamp info" not in response.text
