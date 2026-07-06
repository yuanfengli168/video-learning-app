"""Tests for courses router."""

from unittest.mock import patch

from fastapi.testclient import TestClient

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def test_list_courses_empty(client: TestClient):
    """List courses should return empty list for new user."""
    with _mock_auth():
        response = client.get("/api/courses", headers=_auth_headers())
    assert response.status_code == 200
    assert response.json() == []


def test_create_course(client: TestClient):
    """Should create a course."""
    with _mock_auth():
        response = client.post(
            "/api/courses",
            json={"title": "Machine Learning", "description": "ML course"},
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    assert "course_id" in response.json()


def test_list_courses_after_create(client: TestClient):
    """Should list created courses."""
    with _mock_auth():
        client.post(
            "/api/courses",
            json={"title": "ML"},
            headers=_auth_headers(),
        )
        response = client.get("/api/courses", headers=_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "ML"


def test_get_course(client: TestClient):
    """Should get a course by ID."""
    with _mock_auth():
        create_resp = client.post(
            "/api/courses",
            json={"title": "ML", "description": "Test"},
            headers=_auth_headers(),
        )
        course_id = create_resp.json()["course_id"]
        response = client.get(f"/api/courses/{course_id}", headers=_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "ML"
    assert data["description"] == "Test"
    assert data["sections"] == []


def test_get_course_not_found(client: TestClient):
    """Should return 404 for non-existent course."""
    with _mock_auth():
        response = client.get(
            "/api/courses/nonexistent-id", headers=_auth_headers()
        )
    assert response.status_code == 404


def test_update_course(client: TestClient):
    """Should update a course."""
    with _mock_auth():
        create_resp = client.post(
            "/api/courses",
            json={"title": "Original"},
            headers=_auth_headers(),
        )
        course_id = create_resp.json()["course_id"]
        response = client.put(
            f"/api/courses/{course_id}",
            json={"title": "Updated", "description": "New desc"},
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    assert response.json()["status"] == "updated"


def test_delete_course(client: TestClient):
    """Should delete a course."""
    with _mock_auth():
        create_resp = client.post(
            "/api/courses",
            json={"title": "To Delete"},
            headers=_auth_headers(),
        )
        course_id = create_resp.json()["course_id"]
        response = client.delete(
            f"/api/courses/{course_id}", headers=_auth_headers()
        )
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"


def test_create_section(client: TestClient):
    """Should create a section in a course."""
    with _mock_auth():
        create_resp = client.post(
            "/api/courses",
            json={"title": "ML"},
            headers=_auth_headers(),
        )
        course_id = create_resp.json()["course_id"]
        response = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1", "order_index": 0},
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    assert "section_id" in response.json()


def test_list_section_videos_empty(client: TestClient):
    """Should return empty list for a section with no videos."""
    with _mock_auth():
        create_resp = client.post(
            "/api/courses",
            json={"title": "ML"},
            headers=_auth_headers(),
        )
        course_id = create_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
        response = client.get(
            f"/api/courses/{course_id}/sections/{section_id}/videos",
            headers=_auth_headers(),
        )
    assert response.status_code == 200
    assert response.json() == []


def test_course_ownership(client: TestClient):
    """Should not allow access to another user's course."""
    with patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": "user-A"},
    ):
        create_resp = client.post(
            "/api/courses",
            json={"title": "A's course"},
            headers=_auth_headers(),
        )
        course_id = create_resp.json()["course_id"]

    with patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": "user-B"},
    ):
        response = client.get(
            f"/api/courses/{course_id}", headers=_auth_headers()
        )
    assert response.status_code == 403


def test_unauthorized_access(client: TestClient):
    """Should return 401 without auth."""
    response = client.get("/api/courses")
    assert response.status_code == 401