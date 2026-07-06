"""Tests for auth dependencies and auth router."""

from unittest.mock import patch

from fastapi.testclient import TestClient


def test_me_without_token(client: TestClient):
    """/api/auth/me should return 401 without a Bearer token."""
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert "Not authenticated" in response.json()["detail"]


def test_verify_without_token(client: TestClient):
    """/api/auth/verify should return 401 without a Bearer token."""
    response = client.get("/api/auth/verify")
    assert response.status_code == 401


def test_me_with_invalid_token(client: TestClient):
    """/api/auth/me should return 401 with an invalid token."""
    response = client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert response.status_code == 401
    assert "Invalid or expired token" in response.json()["detail"]


def test_me_with_valid_token(client: TestClient):
    """/api/auth/me should return user info with a valid token."""
    fake_claims = {
        "uid": "test-uid-123",
        "email": "test@example.com",
        "name": "Test User",
        "picture": "https://example.com/photo.jpg",
        "email_verified": True,
    }

    with patch("app.auth.dependencies.verify_token", return_value=fake_claims):
        response = client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer valid-token"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["uid"] == "test-uid-123"
    assert data["email"] == "test@example.com"
    assert data["name"] == "Test User"
    assert data["picture"] == "https://example.com/photo.jpg"
    assert data["email_verified"] is True


def test_verify_with_valid_token(client: TestClient):
    """/api/auth/verify should return verified status with a valid token."""
    fake_claims = {"uid": "test-uid-123"}

    with patch("app.auth.dependencies.verify_token", return_value=fake_claims):
        response = client.get(
            "/api/auth/verify",
            headers={"Authorization": "Bearer valid-token"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "verified"
    assert data["uid"] == "test-uid-123"


def test_me_with_malformed_auth_header(client: TestClient):
    """/api/auth/me should return 401 with a malformed Authorization header."""
    response = client.get(
        "/api/auth/me",
        headers={"Authorization": "NotBearer token"},
    )
    assert response.status_code == 401


def test_get_user_id_helper():
    """get_user_id should extract uid from claims."""
    from app.auth.dependencies import get_user_id

    assert get_user_id({"uid": "abc"}) == "abc"
    assert get_user_id({}) == ""


def test_get_current_user_optional_no_header(client: TestClient):
    """get_current_user_optional should return None when no auth header."""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope)

    import asyncio
    from app.auth.dependencies import get_current_user_optional

    result = asyncio.run(get_current_user_optional(request))
    assert result is None


def test_get_current_user_optional_valid_token(client: TestClient):
    """get_current_user_optional should return claims with a valid token."""
    fake_claims = {"uid": "test-uid", "email": "test@example.com"}

    with patch("app.auth.dependencies.verify_token", return_value=fake_claims):
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", b"Bearer valid-token")],
            "query_string": b"",
        }
        request = Request(scope)

        import asyncio
        from app.auth.dependencies import get_current_user_optional

        result = asyncio.run(get_current_user_optional(request))
        assert result == fake_claims


def test_get_current_user_optional_invalid_token(client: TestClient):
    """get_current_user_optional should return None with an invalid token."""
    with patch(
        "app.auth.dependencies.verify_token",
        side_effect=ValueError("bad token"),
    ):
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", b"Bearer bad-token")],
            "query_string": b"",
        }
        request = Request(scope)

        import asyncio
        from app.auth.dependencies import get_current_user_optional

        result = asyncio.run(get_current_user_optional(request))
        assert result is None