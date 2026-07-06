"""Tests for session cookie auth flow."""

from unittest.mock import patch

from fastapi.testclient import TestClient

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com", "name": "Test"}
FAKE_TOKEN = "fake-firebase-id-token"


def test_create_session(client: TestClient):
    """POST /api/auth/session should set an httpOnly cookie."""
    with patch("app.auth.session.verify_token", return_value=FAKE_USER):
        response = client.post(
            "/api/auth/session",
            json={"id_token": FAKE_TOKEN},
        )
    assert response.status_code == 200
    assert response.json()["uid"] == "test-user-uid"
    assert response.json()["email"] == "test@example.com"
    # Check cookie was set
    cookies = response.headers.get("set-cookie", "")
    assert "fb_token" in cookies
    assert "httponly" in cookies.lower()


def test_create_session_invalid_token(client: TestClient):
    """POST /api/auth/session should return 401 for invalid token."""
    with patch("app.auth.session.verify_token", side_effect=ValueError("Bad token")):
        response = client.post(
            "/api/auth/session",
            json={"id_token": "bad-token"},
        )
    assert response.status_code == 401


def test_delete_session(client: TestClient):
    """DELETE /api/auth/session should clear the cookie."""
    response = client.delete("/api/auth/session")
    assert response.status_code == 200
    assert response.json()["status"] == "logged_out"


def test_get_token_from_cookie_none(client: TestClient):
    """get_token_from_cookie should return None when no cookie."""
    from app.auth.session import get_token_from_cookie
    from starlette.requests import Request

    scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}
    request = Request(scope)
    assert get_token_from_cookie(request) is None


def test_get_token_from_cookie_present(client: TestClient):
    """get_token_from_cookie should return the token when cookie is set."""
    from app.auth.session import get_token_from_cookie, COOKIE_NAME
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"cookie", f"{COOKIE_NAME}=my-token".encode())],
        "query_string": b"",
    }
    request = Request(scope)
    assert get_token_from_cookie(request) == "my-token"


def test_auth_with_cookie(client: TestClient):
    """API routes should accept the session cookie for auth."""
    # First create a session
    with patch("app.auth.session.verify_token", return_value=FAKE_USER):
        resp = client.post("/api/auth/session", json={"id_token": FAKE_TOKEN})
        assert resp.status_code == 200

    # Now use the cookie for a protected route
    with patch("app.auth.dependencies.verify_token", return_value=FAKE_USER):
        response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["uid"] == "test-user-uid"


def test_auth_cookie_takes_priority_over_header(client: TestClient):
    """Cookie should be checked before Authorization header."""
    from app.auth.session import COOKIE_NAME

    # Set a valid cookie
    with patch("app.auth.session.verify_token", return_value=FAKE_USER):
        client.post("/api/auth/session", json={"id_token": FAKE_TOKEN})

    # Send a request with both cookie and bad header — cookie should win
    with patch("app.auth.dependencies.verify_token", return_value=FAKE_USER) as mock_verify:
        response = client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer different-token"},
        )
        # The cookie token should be used (not the header token)
        mock_verify.assert_called_once_with(FAKE_TOKEN)
    assert response.status_code == 200