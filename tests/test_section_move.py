"""Section move tests (2026-09-12 — section ordering, option A).

POST /api/courses/{cid}/sections/{sid}/move {direction} moves a section
one slot up/down. Key behaviors to pin:

  - The swap actually changes render order
  - Ownership is enforced (403)
  - direction must be 'up'/'down' (400)
  - Edges 400 ("already at the top/bottom")
  - LEGACY FIX: all 14 production sections sit at order_index=0 (the
    column was never set by any flow). The first move NORMALIZES
    0..n-1 by created_at/id — without changing render order — then
    swaps. After any move, order_index is dense 0..n-1.
  - Section under the wrong course → 404 (no existence leak)
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Course, Section


def _mk_course_with_sections(
    db: Session, uid: str = "user-A", titles: list[str] | None = None
) -> tuple[Course, list[Section]]:
    """Course + sections named by `titles`, all at legacy order_index=0
    (matching production rows) so the normalization path is exercised
    by every test here.

    `created_at` is staggered explicitly: SQLite stores second-precision
    timestamps and the endpoint's tie-break is created_at/id, so
    same-transaction inserts (same second, random uuid4 ids) would
    otherwise render in a nondeterministic order.
    """
    from datetime import datetime, timedelta

    titles = titles or ["Alpha", "Beta", "Gamma"]
    course = Course(title="Some Course", description="d", user_id=uid)
    db.add(course)
    db.flush()
    sections = []
    base = datetime(2026, 9, 12, 12, 0, 0)
    for i, t in enumerate(titles):
        sections.append(
            Section(
                title=t,
                course_id=course.id,
                order_index=0,
                created_at=base + timedelta(minutes=i),
            )
        )
        db.add(sections[-1])
    db.commit()
    return course, sections


def _order(db: Session, course_id: str) -> list[str]:
    """Section titles in the same order the course page renders them."""
    db.expire_all()
    rows = (
        db.query(Section)
        .filter(Section.course_id == course_id)
        .order_by(
            Section.order_index.asc(), Section.created_at.asc(), Section.id.asc()
        )
        .all()
    )
    return [s.title for s in rows]


def _post_move(client: TestClient, course_id: str, section_id: str, direction: str):
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "user-A", "email": "a@x.com", "role": 1}):
        return client.post(
            f"/api/courses/{course_id}/sections/{section_id}/move",
            json={"direction": direction},
        )


# ── The happy paths ──────────────────────────────────────────────────────


def test_move_section_down_then_up(paid_client, db_session):
    course, sections = _mk_course_with_sections(db_session)
    assert _order(db_session, course.id) == ["Alpha", "Beta", "Gamma"]

    resp = _post_move(paid_client, course.id, sections[1].id, "down")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "moved"
    assert resp.json()["swapped_with"] == sections[2].id
    assert _order(db_session, course.id) == ["Alpha", "Gamma", "Beta"]

    resp = _post_move(paid_client, course.id, sections[1].id, "up")
    assert resp.status_code == 200, resp.text
    assert _order(db_session, course.id) == ["Alpha", "Beta", "Gamma"]


def test_move_section_normalizes_legacy_zero_order_index(paid_client, db_session):
    """All sections at legacy order_index=0: first move must normalize to
    dense 0..n-1 (without changing render order) before swapping."""
    course, sections = _mk_course_with_sections(db_session)
    assert {s.order_index for s in db_session.query(Section).all()} == {0}

    resp = _post_move(paid_client, course.id, sections[0].id, "down")
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    indexes = [
        s.order_index
        for s in db_session.query(Section)
        .filter(Section.course_id == course.id)
        .order_by(Section.order_index.asc())
        .all()
    ]
    assert indexes == [0, 1, 2], "order_index should be dense after a move"
    assert _order(db_session, course.id) == ["Beta", "Alpha", "Gamma"]


# ── Validation & access control ───────────────────────────────────────────


def test_move_section_bad_direction_400(paid_client, db_session):
    course, sections = _mk_course_with_sections(db_session)
    resp = _post_move(paid_client, course.id, sections[0].id, "sideways")
    assert resp.status_code == 400
    assert "direction" in resp.json()["detail"]


def test_move_section_edge_400(paid_client, db_session):
    course, sections = _mk_course_with_sections(db_session)
    top, bottom = sections[0], sections[-1]
    assert _post_move(paid_client, course.id, top.id, "up").status_code == 400
    assert _post_move(paid_client, course.id, bottom.id, "down").status_code == 400
    # order untouched by the failed moves
    assert _order(db_session, course.id) == ["Alpha", "Beta", "Gamma"]


def test_move_section_ownership_403(paid_client, db_session):
    course, sections = _mk_course_with_sections(db_session, uid="someone-else")
    resp = _post_move(paid_client, course.id, sections[0].id, "down")
    assert resp.status_code == 403
    assert _order(db_session, course.id) == ["Alpha", "Beta", "Gamma"]


def test_move_section_wrong_course_404(paid_client, db_session):
    """Moving someone's section via another course's URL → 404."""
    course, sections = _mk_course_with_sections(db_session)
    other = Course(title="Other", user_id="user-A")
    db_session.add(other)
    db_session.commit()
    resp = _post_move(paid_client, other.id, sections[0].id, "down")
    assert resp.status_code == 404


def test_move_section_unknown_ids(paid_client, db_session):
    """Unknown section → 404; unknown course → 403 (matches the
    codebase's no-existence-leak convention: a missing course is
    indistinguishable from someone else's course)."""
    course, _ = _mk_course_with_sections(db_session)
    assert _post_move(paid_client, course.id, "no-such-section", "down").status_code == 404
    assert _post_move(paid_client, "no-such-course", "no-such-section", "down").status_code == 403


# ── UI markup ────────────────────────────────────────────────────────────


def test_course_page_shows_arrows_for_owner(paid_client, db_session):
    """The course page SSR-includes ↑/↓ for the course owner (hidden at
    the top/bottom edge), and moveSection() exists for them to call."""
    course, _ = _mk_course_with_sections(db_session)
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "user-A", "email": "a@x.com", "role": 1}):
        html = paid_client.get(f"/course/{course.id}").text
    assert "moveSection(" in html, "buttons should call moveSection()"
    assert html.count("moveSection(") >= 2, "middle sections need both arrows"


def test_course_page_no_arrows_for_free_user(paid_client, db_session):
    """FREE users (no manage_own_course) see NO move buttons — same
    capability-gating pattern as the rename buttons. (Non-owner PAID
    users still see the buttons and the API 403s — pinned by
    test_move_section_ownership_403; that matches the established
    template convention for + Add Section / ✏️ rename.)"""
    course, _ = _mk_course_with_sections(db_session)
    fake_free = {"uid": "free-viewer-x", "email": "f@x.com", "role": 2}
    with patch("app.auth.dependencies.verify_token", return_value=fake_free):
        resp = paid_client.get(f"/course/{course.id}")
    assert resp.status_code == 200
    # Button markup (inside the {% if manage_own_course %} gate)
    assert "moveSection('" not in resp.text, "FREE users must not see move buttons"