"""Tests for frontend router — template rendering."""

from unittest.mock import patch

from fastapi.testclient import TestClient

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com", "name": "Test User"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth(user=FAKE_USER):
    return patch("app.auth.dependencies.verify_token", return_value=user)


def test_dashboard_unauthenticated(client: TestClient):
    """Dashboard should render for unauthenticated users (sign-in prompt)."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Sign in" in response.text


def test_dashboard_authenticated(client: TestClient):
    """Dashboard should render for authenticated users with courses."""
    with _mock_auth():
        # Create a course first
        client.post(
            "/api/courses",
            json={"title": "Test Course"},
            headers=_auth_headers(),
        )
        response = client.get("/", headers=_auth_headers())
    assert response.status_code == 200
    assert "Test Course" in response.text


def test_login_page(client: TestClient):
    """Login page should render with AuthKit."""
    response = client.get("/login")
    assert response.status_code == 200
    assert "auth-anchor" in response.text
    assert "AuthKit" in response.text


def test_login_page_redirects_authenticated(client: TestClient):
    """Login page should redirect if already authenticated."""
    with _mock_auth():
        response = client.get("/login", headers=_auth_headers())
    # Should return redirect (200 with redirect template or 302)
    assert response.status_code == 200
    assert "Redirecting" in response.text or "redirect" in response.text.lower()


def test_course_view_not_found(client: TestClient):
    """Course view should return 404 for non-existent course."""
    response = client.get("/course/nonexistent-id")
    assert response.status_code == 404
    assert "not found" in response.text.lower()


def test_course_view_found(client: TestClient):
    """Course view should render for existing course."""
    with _mock_auth():
        create_resp = client.post(
            "/api/courses",
            json={"title": "ML Course"},
            headers=_auth_headers(),
        )
        course_id = create_resp.json()["course_id"]
        response = client.get(f"/course/{course_id}", headers=_auth_headers())
    assert response.status_code == 200
    assert "ML Course" in response.text


def test_video_view_not_found(client: TestClient):
    """Video view should return 404 for non-existent video."""
    response = client.get("/video/nonexistent-id")
    assert response.status_code == 404


def test_video_view_found(client: TestClient):
    """Video view should render for existing video."""
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
    assert "lecture" in response.text


def test_video_file_serving(client: TestClient):
    """Video file endpoint should serve the file."""
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
        fake_video = io.BytesIO(b"fake video content")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(
            f"/api/videos/{video_id}/file", headers=_auth_headers()
        )
    assert response.status_code == 200


def test_video_file_not_found(client: TestClient):
    """Video file endpoint should return 404 for non-existent video."""
    with _mock_auth():
        response = client.get(
            "/api/videos/nonexistent/file", headers=_auth_headers()
        )
    assert response.status_code == 404


def test_templates_have_dark_mode(client: TestClient):
    """Templates should include dark mode support."""
    response = client.get("/")
    assert "dark:" in response.text
    assert "toggleTheme" in response.text


def test_templates_have_htmx(client: TestClient):
    """Templates should include HTMX."""
    response = client.get("/")
    assert "htmx" in response.text.lower()


def test_templates_have_sidebar(client: TestClient):
    """Templates should include sidebar navigation."""
    response = client.get("/")
    assert "sidebar" in response.text.lower()
    assert "Dashboard" in response.text