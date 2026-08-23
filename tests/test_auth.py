"""Tests for auth dependencies and auth router."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import text


def test_me_without_token(client: TestClient):
    """/api/auth/me should return 401 without a Bearer token."""
    # MVP2.0.6: conftest client fixture sets a default valid
    # cookie. Clear it for this unauthenticated test.
    client.cookies.clear()
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert "Not authenticated" in response.json()["detail"]


def test_verify_without_token(client: TestClient):
    """/api/auth/verify should return 401 without a Bearer token."""
    # MVP2.0.6: conftest client fixture sets a default valid
    # cookie. Clear it for this unauthenticated test.
    client.cookies.clear()
    response = client.get("/api/auth/verify")
    assert response.status_code == 401


def test_me_with_invalid_token(client: TestClient):
    """/api/auth/me should return 401 with an invalid token."""
    # MVP2.0.6: conftest client fixture sets a default valid
    # cookie. Clear it so only the Authorization header is read.
    client.cookies.clear()
    with patch("app.auth.dependencies.verify_token", side_effect=ValueError("Invalid token")):
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
    # MVP2.0.6: conftest client fixture sets a default valid
    # cookie. Clear it so only the Authorization header is read.
    client.cookies.clear()
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
    """get_current_user_optional should return None when no auth header or cookie."""
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

    result = asyncio.run(get_current_user_optional(request, None))
    assert result is None


def test_get_current_user_optional_valid_token(client: TestClient, db_session):
    """get_current_user_optional should return claims with a valid token."""
    fake_claims = {"uid": "test-uid", "email": "test@example.com"}

    with patch("app.auth.dependencies.verify_token", return_value=fake_claims):
        from starlette.requests import Request
        from fastapi.security import HTTPAuthorizationCredentials

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", b"Bearer valid-token")],
            "query_string": b"",
        }
        request = Request(scope)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid-token")

        import asyncio
        from app.auth.dependencies import get_current_user_optional

        result = asyncio.run(get_current_user_optional(request, credentials, db_session))
        assert result == fake_claims


def test_get_current_user_optional_invalid_token(client: TestClient, db_session):
    """get_current_user_optional should return None with an invalid token."""
    with patch(
        "app.auth.dependencies.verify_token",
        side_effect=ValueError("bad token"),
    ):
        from starlette.requests import Request
        from fastapi.security import HTTPAuthorizationCredentials

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", b"Bearer bad-token")],
            "query_string": b"",
        }
        request = Request(scope)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bad-token")

        import asyncio
        from app.auth.dependencies import get_current_user_optional

        result = asyncio.run(get_current_user_optional(request, credentials, db_session))
        assert result is None


def test_get_current_user_optional_creates_user_row(client: TestClient, db_session):
    """get_current_user_optional should call ensure_user_row (new in Day 2A fix).

    This is the fix for the 'sign in but no row in DB' bug — the user
    row is now created on FIRST authenticated request, not only on admin routes.
    """
    from app.auth.dependencies import get_current_user_optional
    from app.models import User
    from fastapi.security import HTTPAuthorizationCredentials
    from starlette.requests import Request
    import asyncio

    fake_claims = {"uid": "new-uid-123", "email": "new@example.com"}

    # Sanity: no row yet
    existing = db_session.query(User).filter_by(user_id="new-uid-123").first()
    assert existing is None

    with patch("app.auth.dependencies.verify_token", return_value=fake_claims):
        scope = {
            "type": "http", "method": "GET", "path": "/",
            "headers": [(b"authorization", b"Bearer token")],
            "query_string": b"",
        }
        request = Request(scope)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
        asyncio.run(get_current_user_optional(request, credentials, db_session))

    # Row should now exist with role=FREE
    row = db_session.query(User).filter_by(user_id="new-uid-123").first()
    assert row is not None
    assert row.email == "new@example.com"
    assert row.role == 2  # FREE


def test_get_current_user_optional_already_existing_user_no_change(
    client: TestClient, db_session
):
    """If user row already exists, ensure_user_row should NOT reset role."""
    from app.auth.dependencies import get_current_user_optional
    from app.models import User
    from app.auth.admin import ensure_user_row
    from fastapi.security import HTTPAuthorizationCredentials
    from starlette.requests import Request
    import asyncio

    # Pre-create an admin row
    ensure_user_row("existing-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='existing-admin'"))
    db_session.commit()

    fake_claims = {"uid": "existing-admin", "email": "new-email@x.com"}

    with patch("app.auth.dependencies.verify_token", return_value=fake_claims):
        scope = {
            "type": "http", "method": "GET", "path": "/",
            "headers": [(b"authorization", b"Bearer token")],
            "query_string": b"",
        }
        request = Request(scope)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
        asyncio.run(get_current_user_optional(request, credentials, db_session))

    # Role should still be ADMIN (not reset to FREE)
    row = db_session.query(User).filter_by(user_id="existing-admin").first()
    assert row is not None
    assert row.role == 0  # ADMIN — preserved
    # Email updated (in case user changed it in Firebase)
    assert row.email == "new-email@x.com"