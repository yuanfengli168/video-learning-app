"""Tests for the /admin/upload page (admin-only UI form for adding YouTube videos).

The page itself is a thin wrapper around the API endpoint
/app/routers/admin.py::admin_add_youtube_video — these tests verify:
  1. Auth gating (401 / 403 / 200)
  2. Template renders with the visibility_options context
  3. JS-side URL preview regex is present (so user gets live feedback)
  4. Non-admin users don't see the Admin Upload nav link

The actual admin API endpoint behavior is tested in test_admin_router.py.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.admin import ensure_user_row
from app.main import app


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def admin_user(db_session):
    """An admin user (role=0) with admin email."""
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()
    return {"uid": "uid-admin", "email": "admin@x.com"}


@pytest.fixture
def free_user(db_session):
    """A free-tier user (role=2)."""
    ensure_user_row("uid-free", "free@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=2 WHERE user_id='uid-free'"))
    db_session.commit()
    return {"uid": "uid-free", "email": "free@x.com"}


def _auth_header(uid: str, email: str) -> dict:
    return {"Authorization": f"Bearer fake-{uid}"}


def _mock_verify_token(uid: str, email: str):
    """Returns a context manager that patches verify_token to return our user."""
    return patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": uid, "email": email},
    )


# ─────────────────────────────────────────────────────────────────────────
# Auth gating
# ─────────────────────────────────────────────────────────────────────────


def test_page_requires_auth(client: TestClient):
    """No Authorization header → 401."""
    with TestClient(app) as c:
        response = c.get("/admin/upload")
    assert response.status_code == 401


def test_page_rejects_non_admin(client: TestClient, free_user):
    """Signed in but role=2 → 403."""
    with _mock_verify_token(free_user["uid"], free_user["email"]):
        with TestClient(app) as c:
            response = c.get("/admin/upload", headers=_auth_header("uid-free", "free@x.com"))
    assert response.status_code == 403


def test_page_allows_admin(client: TestClient, admin_user):
    """Signed-in admin (role=0) → 200."""
    with _mock_verify_token(admin_user["uid"], admin_user["email"]):
        with TestClient(app) as c:
            response = c.get("/admin/upload", headers=_auth_header("uid-admin", "admin@x.com"))
    assert response.status_code == 200


# ─────────────────────────────────────────────────────────────────────────
# Template contents
# ─────────────────────────────────────────────────────────────────────────


def test_admin_page_renders_form(client: TestClient, admin_user):
    """200 page contains the form with url/title/visibility inputs."""
    with _mock_verify_token(admin_user["uid"], admin_user["email"]):
        with TestClient(app) as c:
            response = c.get("/admin/upload", headers=_auth_header("uid-admin", "admin@x.com"))
    html = response.text
    assert '<form id="admin-upload-form"' in html
    assert 'name="url"' in html
    assert 'name="title"' in html
    assert 'name="visibility"' in html
    assert 'submit-btn' in html


def test_admin_page_renders_visibility_options(client: TestClient, admin_user):
    """All 3 visibility options present (public, paid_only, admin_only)."""
    with _mock_verify_token(admin_user["uid"], admin_user["email"]):
        with TestClient(app) as c:
            response = c.get("/admin/upload", headers=_auth_header("uid-admin", "admin@x.com"))
    html = response.text
    assert 'value="0"' in html
    assert 'value="1"' in html
    assert 'value="2"' in html
    # Default selection is "public" (visibility=0)
    assert "Public — anyone can view" in html
    assert "Paid only" in html
    assert "Admin only" in html


def test_admin_page_has_url_preview_script(client: TestClient, admin_user):
    """JS-side URL preview regex is included (live feedback).

    The regex is rendered as `youtu\\.be` in the page (JS string escape).
    """
    with _mock_verify_token(admin_user["uid"], admin_user["email"]):
        with TestClient(app) as c:
            response = c.get("/admin/upload", headers=_auth_header("uid-admin", "admin@x.com"))
    html = response.text
    # The script has a regex that matches the 4 YouTube URL formats.
    # Backslash is escaped in the rendered HTML (`\.`).
    assert "youtu" in html and "\\.be" in html
    assert "[A-Za-z0-9_-]{11}" in html


def test_admin_page_posts_to_api(client: TestClient, admin_user):
    """Form action targets the API endpoint."""
    with _mock_verify_token(admin_user["uid"], admin_user["email"]):
        with TestClient(app) as c:
            response = c.get("/admin/upload", headers=_auth_header("uid-admin", "admin@x.com"))
    html = response.text
    assert "/api/admin/videos/youtube" in html


# ─────────────────────────────────────────────────────────────────────────
# Sidebar nav visibility
# ─────────────────────────────────────────────────────────────────────────


def test_sidebar_shows_admin_link_for_admin(client: TestClient, admin_user):
    """Admin sees 'Admin Upload' link in sidebar nav."""
    with _mock_verify_token(admin_user["uid"], admin_user["email"]):
        with TestClient(app) as c:
            response = c.get("/", headers=_auth_header("uid-admin", "admin@x.com"))
    html = response.text
    assert 'href="/admin/upload"' in html
    assert "Admin Upload" in html


def test_sidebar_hides_admin_link_for_free_user(client: TestClient, free_user):
    """Non-admin does NOT see 'Admin Upload' link."""
    with _mock_verify_token(free_user["uid"], free_user["email"]):
        with TestClient(app) as c:
            response = c.get("/", headers=_auth_header("uid-free", "free@x.com"))
    html = response.text
    assert 'href="/admin/upload"' not in html


def test_sidebar_hides_admin_link_for_anonymous(client: TestClient):
    """Anonymous user does NOT see 'Admin Upload' link."""
    with TestClient(app) as c:
        response = c.get("/")
    html = response.text
    assert 'href="/admin/upload"' not in html


# ─────────────────────────────────────────────────────────────────────────
# 404 if wrong path
# ─────────────────────────────────────────────────────────────────────────


def test_unknown_admin_path_returns_404(client: TestClient, admin_user):
    """Sanity: /admin/upload/wrong returns 404."""
    with _mock_verify_token(admin_user["uid"], admin_user["email"]):
        with TestClient(app) as c:
            response = c.get("/admin/upload/typo", headers=_auth_header("uid-admin", "admin@x.com"))
    assert response.status_code == 404


# ─────────────────────────────────────────────────────────────────────────
# Form submission uses session cookie (not Bearer token)
# ─────────────────────────────────────────────────────────────────────────


def test_form_uses_credentials_include_not_bearer(client: TestClient, admin_user):
    """Form POSTs with credentials:'include' so the session cookie is sent.

    Bug fix 2026-08-23: previous version called firebase.auth() which
    isn't loaded on this page (only login.html imports AuthKit).
    Switching to credentials:'include' works because the session cookie
    was set by /api/auth/session during login.
    """
    with _mock_verify_token(admin_user["uid"], admin_user["email"]):
        with TestClient(app) as c:
            response = c.get("/admin/upload", headers=_auth_header("uid-admin", "admin@x.com"))
    html = response.text
    assert "credentials: 'include'" in html
    # No firebase.auth() calls anywhere
    assert "firebase.auth" not in html
    # No Bearer header construction
    assert "Authorization: 'Bearer" not in html


def test_form_help_text_reflects_day2b(client: TestClient, admin_user):
    """Help text no longer says 'Day 2B will...' (it already does)."""
    with _mock_verify_token(admin_user["uid"], admin_user["email"]):
        with TestClient(app) as c:
            response = c.get("/admin/upload", headers=_auth_header("uid-admin", "admin@x.com"))
    html = response.text
    assert "Day 2B will" not in html
    assert "Day 2A — manual workflow" not in html
    # New copy
    assert "YouTube title" in html
    assert "Day 3" in html  # mentions caption download is Day 3
