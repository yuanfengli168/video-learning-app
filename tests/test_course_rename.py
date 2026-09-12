"""Course/section rename tests (2026-09-12 — user report).

User report: PAID users could create a course + sections but NEVER
edit them — course title/description and section names were stuck
forever. Diagnosis:

  - PUT /api/courses/{id} already existed (title + description,
    ownership-checked) but NO UI called it
  - Sections had NO update endpoint at all

Fix: new PUT /api/courses/{cid}/sections/{sid} + edit UI (✏️ buttons +
modals) on the course page for both.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Course, Section


def _mk_course(db: Session, uid: str = "user-A", title: str = "Old Title",
               desc: str = "Old desc") -> tuple[Course, Section]:
    course = Course(title=title, description=desc, user_id=uid)
    db.add(course)
    db.flush()
    section = Section(title="Old Section", course_id=course.id, order_index=0)
    db.add(section)
    db.commit()
    return course, section


# ── Section rename (the new endpoint) ────────────────────────────────────


def test_rename_section(paid_client: TestClient, db_session: Session):
    course, section = _mk_course(db_session)
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "user-A", "email": "a@x.com", "role": 1}):
        resp = paid_client.put(
            f"/api/courses/{course.id}/sections/{section.id}",
            json={"title": "New Section Name"},
        )
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    assert db_session.get(Section, section.id).title == "New Section Name"


def test_rename_section_ownership_required(
    paid_client: TestClient, db_session: Session
):
    """A user can't rename another user's section."""
    course, section = _mk_course(db_session, uid="someone-else")
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "user-A", "email": "a@x.com", "role": 1}):
        resp = paid_client.put(
            f"/api/courses/{course.id}/sections/{section.id}",
            json={"title": "Stolen"},
        )
    assert resp.status_code == 403
    db_session.expire_all()
    assert db_session.get(Section, section.id).title == "Old Section"


def test_rename_section_empty_title_400(
    paid_client: TestClient, db_session: Session
):
    course, section = _mk_course(db_session)
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "user-A", "email": "a@x.com", "role": 1}):
        resp = paid_client.put(
            f"/api/courses/{course.id}/sections/{section.id}",
            json={"title": "   "},
        )
    assert resp.status_code == 400


def test_rename_section_wrong_course_404(
    paid_client: TestClient, db_session: Session
):
    """Section id + course id mismatch → 404 (no existence leak)."""
    course, section = _mk_course(db_session)
    other = Course(title="Other", user_id="user-A")
    db_session.add(other)
    db_session.commit()
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "user-A", "email": "a@x.com", "role": 1}):
        resp = paid_client.put(
            f"/api/courses/{other.id}/sections/{section.id}",
            json={"title": "Cross-wired"},
        )
    assert resp.status_code == 404


def test_section_update_noop_with_none_body(
    paid_client: TestClient, db_session: Session
):
    """All-None body = 200 no-op (mirrors CourseUpdate contract)."""
    course, section = _mk_course(db_session)
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "user-A", "email": "a@x.com", "role": 1}):
        resp = paid_client.put(
            f"/api/courses/{course.id}/sections/{section.id}",
            json={},
        )
    assert resp.status_code == 200
    db_session.expire_all()
    assert db_session.get(Section, section.id).title == "Old Section"


# ── Course update (endpoint existed; pin it + its ownership rule) ────────


def test_update_course_title_and_desc(
    paid_client: TestClient, db_session: Session
):
    course, _ = _mk_course(db_session)
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "user-A", "email": "a@x.com", "role": 1}):
        resp = paid_client.put(
            f"/api/courses/{course.id}",
            json={"title": "New Title", "description": "New desc"},
        )
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    c = db_session.get(Course, course.id)
    assert c.title == "New Title"
    assert c.description == "New desc"


def test_update_course_ownership_required(
    paid_client: TestClient, db_session: Session
):
    course, _ = _mk_course(db_session, uid="someone-else")
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "user-A", "email": "a@x.com", "role": 1}):
        resp = paid_client.put(
            f"/api/courses/{course.id}",
            json={"title": "Stolen"},
        )
    assert resp.status_code == 403


# ── UI rendering ──────────────────────────────────────────────────────────


def test_course_page_renders_edit_ui(
    paid_client: TestClient, db_session: Session
):
    course, section = _mk_course(db_session)
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "user-A", "email": "a@x.com", "role": 1}):
        resp = paid_client.get(f"/course/{course.id}")
    assert resp.status_code == 200
    html = resp.text
    # Edit-course button + modal
    assert "showEditCourseModal" in html
    assert 'id="edit-course-modal"' in html
    assert 'id="edit-course-title-input"' in html
    # Rename-section button + modal
    assert "showRenameSectionModal" in html
    assert 'id="rename-section-modal"' in html
    assert 'id="rename-section-input"' in html
    # Save handlers hit the right endpoints
    assert "/api/courses/${courseId}/sections/" in html  # section PUT
    assert "confirmEditCourse" in html


def test_edit_ui_hidden_from_users_without_capability(
    client: TestClient,
    paid_client: TestClient,
    db_session: Session,
):
    """FREE users (no manage_own_course) see NO edit BUTTONS — the
    course page renders read-only for them. (The JS function
    definitions still ship — harmless without a caller — so the
    assertion targets the button markup, which is inside the
    capability gate.)"""
    course, section = _mk_course(db_session)
    fake_free = {"uid": "free-viewer-x", "email": "f@x.com", "role": 2}
    with patch("app.auth.dependencies.verify_token", return_value=fake_free):
        resp = client.get(f"/course/{course.id}")
    assert resp.status_code == 200
    html = resp.text
    # Buttons (inside the {% if manage_own_course %} gates)
    assert "showEditCourseModal('" not in html      # edit-course button onclick
    assert "showRenameSectionModal('" not in html   # rename-section button onclick
    # And the API rejects FREE users directly (defense in depth):
    with patch("app.auth.dependencies.verify_token", return_value=fake_free):
        api_resp = client.put(
            f"/api/courses/{course.id}/sections/{section.id}",
            json={"title": "Hacked"},
        )
    assert api_resp.status_code == 403
    # And the PAID view DOES render them (control case — paid_client's
    # uid is promoted to role=1 by conftest, so the capability check
    # passes and the buttons render)
    resp2 = paid_client.get(f"/course/{course.id}")
    assert "showEditCourseModal('" in resp2.text
    assert "showRenameSectionModal('" in resp2.text