"""Course-level section sort tests (2026-09-12).

User feedback: the ↑/↓ arrow-button reorder (option A, shipped earlier
today in dfa4980) wasn't liked and was REVERTED. Replacement request:

  "asc (default) button on the course level (beside the Edit Course
   button) — once clicked, the sections below sort by name ascending,
   and the label flips to desc. Just like the current asc/desc at the
   section level."

So this pins:
  1. The course page renders the `sortCourseSections()` button (both
     the manage and viewer variants of the header).
  2. Each section card carries the data hooks the sort needs:
     data-section-card + data-sort-key (server-computed natural sort
     key), and the container has id="course-sections".
  3. The button is NOT capability-gated for the manage variant —
     sorting is a view-only preference, exactly like the per-section
     video sort, which is also ungated.
  4. The JS function pair exists on the page (toggle + stored-value
     apply-on-load), with per-course localStorage persistence.
  5. The reverted arrows are really gone — no moveSection markup, no
     /move endpoint route anywhere in the app.

(Drag-and-drop reordering was discussed and deferred — see Todo.md.)
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Course, Section
from app.main import app


def _mk_course_with_sections(
    db: Session, uid: str = "user-A", titles: list[str] | None = None
) -> Course:
    """Course + sections named by `titles`.

    `created_at` is staggered explicitly (SQLite second-precision
    timestamps would otherwise tie for same-transaction inserts and
    render order would be uuid-tie-break random).
    """
    from datetime import datetime, timedelta

    titles = titles or ["Alpha", "Beta", "Gamma"]
    course = Course(title="Some Course", description="d", user_id=uid)
    db.add(course)
    db.flush()
    base = datetime(2026, 9, 12, 12, 0, 0)
    for i, t in enumerate(titles):
        db.add(
            Section(
                title=t,
                course_id=course.id,
                order_index=0,
                created_at=base + timedelta(minutes=i),
            )
        )
    db.commit()
    return course


def _get_course_html(client: TestClient, course_id: str) -> str:
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "user-A", "email": "a@x.com", "role": 1}):
        resp = client.get(f"/course/{course_id}")
    assert resp.status_code == 200, resp.text
    return resp.text


# ── The new course-level sort button ─────────────────────────────────────


def test_course_page_has_sort_button(paid_client: TestClient, db_session):
    course = _mk_course_with_sections(db_session)
    html = _get_course_html(paid_client, course.id)
    # Button markup (id used by the JS to flip the label)
    assert 'id="course-sort-btn"' in html
    assert "sortCourseSections()" in html
    # Default label matches the per-section sort button's default
    assert "↑ asc" in html


def test_section_cards_carry_sort_hooks(paid_client: TestClient, db_session):
    """The JS sorts cards by their server-computed natural sort key —
    these attributes are the contract between template and JS."""
    course = _mk_course_with_sections(
        db_session, titles=["2. Networks", "10. Transformers", "Alpha"]
    )
    html = _get_course_html(paid_client, course.id)
    assert 'id="course-sections"' in html
    assert "data-section-card" in html
    # natural_sort_key_str format is "{n:09d}:{title_lower}" — leading
    # numbers keep their numeric order under lexicographic compare, and
    # unnumbered titles get the 10**9 sentinel (sort last in asc).
    assert 'data-sort-key="000000002:2. networks"' in html
    assert 'data-sort-key="000000010:10. transformers"' in html
    assert 'data-sort-key="1000000000:alpha"' in html


def test_sort_button_ungated_for_viewer(paid_client: TestClient, db_session):
    """FREE/anonymous viewers see the sort button too — it only
    reorders THEIR view (client-side), exactly like the per-section
    video sort which is also ungated."""
    course = _mk_course_with_sections(db_session)
    fake_free = {"uid": "free-viewer-x", "email": "f@x.com", "role": 2}
    with patch("app.auth.dependencies.verify_token", return_value=fake_free):
        resp = paid_client.get(f"/course/{course.id}")
    assert resp.status_code == 200
    assert 'id="course-sort-btn"' in resp.text
    assert "sortCourseSections()" in resp.text


def test_sort_js_present_with_persistence(paid_client: TestClient, db_session):
    """The toggle + apply-on-load pair ships, with per-course
    localStorage persistence (same UX contract as sortSection)."""
    course = _mk_course_with_sections(db_session)
    html = _get_course_html(paid_client, course.id)
    assert "function sortCourseSections()" in html
    assert "function applyStoredCourseSortOnLoad()" in html
    assert "courseSectionSort:" in html, "per-course localStorage prefix"
    # On-load application must be UNCONDITIONAL (the asc default sorts
    # too) — regression pin for the first-open bug where the page
    # rendered DB order while the button already said "↑ asc".
    assert "_sortCourseSectionCards(getStoredCourseSort(courseId))" in html


# ── Arrow-button revert regression ───────────────────────────────────────


def test_arrows_removed_from_page(paid_client: TestClient, db_session):
    course = _mk_course_with_sections(db_session)
    html = _get_course_html(paid_client, course.id)
    assert "moveSection('" not in html, "↑/↓ button markup must be gone"
    assert "async function moveSection" not in html


def test_move_endpoint_removed():
    """The reverted POST /sections/{sid}/move route must not be
    registered anywhere (the whole endpoint + schema were removed)."""
    routes = {f"{getattr(r, 'path', '')}" for r in app.routes}
    assert not any(p.endswith("/move") for p in routes), \
        "no /move route should exist after the revert"