"""Tests for MVP2.1.0.4 — Capability-gated routes (role-based access control).

After the 5 security gaps were found (free user could upload, create
courses, create sections, run plugins; PAID had no own-course
management), we tightened the role × capability matrix and gated
these routes with `require_capability(...)`:

  - POST /api/videos/upload/{section_id}        → UPLOAD_VIDEO       (PAID+)
  - POST /api/videos/upload-bulk/{section_id}   → UPLOAD_VIDEO       (PAID+)
  - POST /api/courses                           → MANAGE_OWN_COURSE  (PAID+)
  - POST /api/courses/{id}/sections             → MANAGE_OWN_COURSE  (PAID+)
  - GET/POST /api/plugins*                      → RUN_PLUGIN         (ADMIN)

The matrix is in `app/auth/roles.py` (ROLE_CAPABILITIES) and the
fixtures in `tests/conftest.py` provide `paid_client` (role=1) and
`admin_client` (role=0). The base `client` fixture is FREE (role=2,
no users row → fallback).

This file covers:
  - Route gates (HTTP 403 with capability detail)
  - Matrix shape (each role has exactly the expected capabilities)
  - Dashboard template hides upload + new-course for FREE
  - Course detail template hides add-section for FREE
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.roles import (
    ROLE_CAPABILITIES,
    Capability,
    UserRole,
    capabilities_for_role,
    user_has_capability,
)


# ── Matrix shape (single source of truth) ───────────────────────────────


def test_role_capabilities_shape():
    """The matrix has exactly 3 roles (ADMIN, PAID, FREE)."""
    assert set(ROLE_CAPABILITIES.keys()) == {UserRole.ADMIN, UserRole.PAID, UserRole.FREE}


def test_admin_has_all_capabilities():
    """ADMIN should have every capability (incl. MANAGE_OWN_COURSE + RUN_PLUGIN)."""
    expected = set(Capability)
    assert ROLE_CAPABILITIES[UserRole.ADMIN] == expected


def test_paid_has_manage_own_course_but_not_run_plugin():
    """PAID gets MANAGE_OWN_COURSE (own courses) but NOT RUN_PLUGIN (admin-only).

    PAID gets 6 caps: VIEW_OWN_COURSES, UPLOAD_VIDEO, REGEN_MATERIALS,
    MANAGE_OWN_COURSE, plus the two built-in caps (VIEW_CATALOG,
    CHAT).
    """
    paid_caps = ROLE_CAPABILITIES[UserRole.PAID]
    assert Capability.MANAGE_OWN_COURSE in paid_caps
    assert Capability.RUN_PLUGIN not in paid_caps
    # No admin-only caps either
    assert Capability.MANAGE_USERS not in paid_caps
    assert Capability.VIEW_ADMIN_DASHBOARD not in paid_caps
    assert Capability.CURATE_CATALOG not in paid_caps


def test_free_user_has_minimum_capabilities():
    """FREE gets only the bare minimum (view_video + chat_free)."""
    free_caps = ROLE_CAPABILITIES[UserRole.FREE]
    assert Capability.VIEW_VIDEO in free_caps
    assert Capability.CHAT_FREE in free_caps
    # No paid-only caps
    assert Capability.UPLOAD_VIDEO not in free_caps
    assert Capability.MANAGE_OWN_COURSE not in free_caps
    assert Capability.REGEN_MATERIALS not in free_caps


def test_new_capabilities_exist_in_enum():
    """MVP2.1.0.4 added two new capabilities — make sure they're real."""
    assert Capability.MANAGE_OWN_COURSE.value == "manage_own_course"
    assert Capability.RUN_PLUGIN.value == "run_plugin"


def test_capabilities_for_role_helper():
    """Pure helper returns the right set for each role."""
    assert Capability.MANAGE_OWN_COURSE in capabilities_for_role(UserRole.PAID)
    assert Capability.RUN_PLUGIN in capabilities_for_role(UserRole.ADMIN)
    assert Capability.MANAGE_OWN_COURSE not in capabilities_for_role(UserRole.FREE)


def test_user_has_capability_helper():
    """Pure helper returns True/False correctly."""
    assert user_has_capability(UserRole.ADMIN, Capability.RUN_PLUGIN)
    assert not user_has_capability(UserRole.PAID, Capability.RUN_PLUGIN)
    assert user_has_capability(UserRole.PAID, Capability.UPLOAD_VIDEO)
    assert not user_has_capability(UserRole.FREE, Capability.UPLOAD_VIDEO)


# ── Route gates: FREE user (no users row → role=FREE) ───────────────────


def test_free_user_cannot_upload_video(client: TestClient):
    """A FREE user gets 403 with 'Missing capability: upload_video'."""
    r = client.post(
        "/api/videos/upload/somesection",
        files={"file": ("x.mp4", b"x", "video/mp4")},
    )
    assert r.status_code == 403
    assert "upload_video" in r.text


def test_free_user_cannot_bulk_upload(client: TestClient):
    """A FREE user gets 403 on /upload-bulk too."""
    r = client.post(
        "/api/videos/upload-bulk/somesection",
        files=[("files", ("x.mp4", b"x", "video/mp4"))],
    )
    assert r.status_code == 403
    assert "upload_video" in r.text


def test_free_user_cannot_create_course(client: TestClient):
    """A FREE user gets 403 on POST /api/courses."""
    r = client.post("/api/courses", json={"title": "Free Course"})
    assert r.status_code == 403
    assert "manage_own_course" in r.text


def test_free_user_cannot_create_section(client: TestClient):
    """A FREE user gets 403 on POST /api/courses/{id}/sections."""
    # Doesn't matter if course exists; capability check fires first.
    r = client.post(
        "/api/courses/somecourse/sections",
        json={"title": "S"},
    )
    assert r.status_code == 403
    assert "manage_own_course" in r.text


def test_free_user_cannot_run_plugin(client: TestClient):
    """A FREE user gets 403 on /api/plugins (RUN_PLUGIN is admin-only)."""
    r = client.get("/api/plugins")
    assert r.status_code == 403
    assert "run_plugin" in r.text


# ── Route gates: PAID user (role=1, has MANAGE_OWN_COURSE + UPLOAD_VIDEO,
#    but NOT RUN_PLUGIN) ──────────────────────────────────────────────────


def test_paid_user_can_create_course(paid_client: TestClient):
    """PAID can create a course (MANAGE_OWN_COURSE granted)."""
    r = paid_client.post("/api/courses", json={"title": "Paid Course"})
    assert r.status_code == 200, r.text
    assert "course_id" in r.json()


def test_paid_user_cannot_run_plugin(paid_client: TestClient):
    """PAID still gets 403 on /api/plugins (RUN_PLUGIN is admin-only)."""
    r = paid_client.get("/api/plugins")
    assert r.status_code == 403
    assert "run_plugin" in r.text


# ── Route gates: ADMIN user (role=0, has everything) ─────────────────────


def test_admin_can_list_plugins(admin_client: TestClient):
    """ADMIN can hit /api/plugins."""
    r = admin_client.get("/api/plugins")
    assert r.status_code == 200
    assert "plugins" in r.json()


# ── Templates: dashboard hides upload + new-course for FREE ─────────────


def test_dashboard_hides_upload_zone_for_free(client: TestClient, db_session: Session):
    """Dashboard for a FREE user shows the upgrade CTA, not the upload zone."""
    # Logged-in FREE user — give them a row so get_current_user succeeds
    db_session.execute(
        text("INSERT OR IGNORE INTO users (user_id, email, role) VALUES ('test-uid', 't@t.com', 2)")
    )
    db_session.commit()

    with patch("app.auth.dependencies.verify_token", return_value={"uid": "test-uid", "email": "t@t.com"}):
        r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Upgrade CTA present
    assert "Uploading is a paid feature" in html
    # Drag-and-drop frame absent (replaced by upgrade CTA)
    assert "Drag &amp; drop a video file here" not in html


def test_dashboard_shows_upload_zone_for_paid(paid_client: TestClient):
    """Dashboard for a PAID user shows the upload zone (no upgrade CTA).

    The full upload-input only renders when the user has at least one
    section, but the upload-zone frame ("Drag & drop a video file
    here") should be visible for any PAID user regardless of whether
    they've set up a course yet.
    """
    r = paid_client.get("/")
    assert r.status_code == 200
    html = r.text
    # Upgrade CTA must be hidden
    assert "Uploading is a paid feature" not in html
    # Upload zone frame must be visible
    assert "Drag &amp; drop a video file here" in html


def test_dashboard_hides_new_course_button_for_free(client: TestClient, db_session: Session):
    """The '+ New Course' button is hidden for FREE users."""
    db_session.execute(
        text("INSERT OR IGNORE INTO users (user_id, email, role) VALUES ('test-uid', 't@t.com', 2)")
    )
    db_session.commit()

    with patch("app.auth.dependencies.verify_token", return_value={"uid": "test-uid", "email": "t@t.com"}):
        r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert "+ New Course" not in html


def test_dashboard_shows_new_course_button_for_paid(paid_client: TestClient):
    """The '+ New Course' button is visible for PAID users."""
    r = paid_client.get("/")
    assert r.status_code == 200
    assert "+ New Course" in r.text


# ── Templates: course page hides add-section for FREE ───────────────────


def test_course_page_hides_add_section_for_free(
    client: TestClient, db_session: Session, paid_client: TestClient
):
    """Course detail page hides '+ Add Section' for FREE users."""
    # Set up: PAID creates a course, then we view it as FREE
    r = paid_client.post("/api/courses", json={"title": "Viewable Course"})
    course_id = r.json()["course_id"]

    # Switch to FREE user
    from app.auth.admin import clear_role_cache
    db_session.execute(
        text("UPDATE users SET role=2 WHERE user_id='test-uid'")
    )
    db_session.commit()
    clear_role_cache()

    r = client.get(f"/course/{course_id}")
    assert r.status_code == 200
    assert "+ Add Section" not in r.text


def test_course_page_shows_add_section_for_paid(paid_client: TestClient):
    """Course detail page shows '+ Add Section' for PAID users (owner)."""
    r = paid_client.post("/api/courses", json={"title": "Owned Course"})
    course_id = r.json()["course_id"]

    r = paid_client.get(f"/course/{course_id}")
    assert r.status_code == 200
    assert "+ Add Section" in r.text
