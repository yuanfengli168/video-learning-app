"""Tests for app/services/section_picker.py.

Covers:
  - resolve_section_for_new_video: 3 priority levels (explicit / fallback / auto-create)
  - ensure_admin_has_a_section: idempotent creation
  - Cross-admin protection (requested_section_id from another admin's course)
  - Missing section_id (ValueError)

Why a dedicated test file (not folded into test_admin_router.py):
  - Pure-function helpers are easier to test in isolation
  - Avoids spinning up the FastAPI app / auth overrides
  - Mirrors the test_youtube_api.py pattern for app/services/* helpers
"""

import uuid

import pytest
from sqlalchemy.orm import Session

from app.models import Course, Section, User
from app.services.section_picker import (
    ensure_admin_has_a_section,
    resolve_section_for_new_video,
)


ADMIN_UID = "test-admin-uid"


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Ensure the admin user row exists."""
    user = db_session.get(User, ADMIN_UID)
    if user is None:
        # PK column is user_id (Firebase UID), not id
        user = User(user_id=ADMIN_UID, email="admin@test.com", role=2)  # ADMIN
        db_session.add(user)
        db_session.commit()
    return user


def _make_course(db_session: Session, owner_uid: str, title: str) -> Course:
    """Helper to create a Course with 0 sections."""
    course = Course(title=title, user_id=owner_uid)
    db_session.add(course)
    db_session.commit()
    db_session.refresh(course)
    return course


def _make_section(db_session: Session, course: Course, title: str, order_index: int = 0) -> Section:
    """Helper to create a Section under a Course."""
    section = Section(title=title, course_id=course.id, order_index=order_index)
    db_session.add(section)
    db_session.commit()
    db_session.refresh(section)
    return section


# ─────────────────────────────────────────────────────────────────────────
# resolve_section_for_new_video: priority 1 (explicit requested id)
# ─────────────────────────────────────────────────────────────────────────


def test_resolve_with_explicit_section_id_returns_that_section(
    db_session: Session, admin_user: User
):
    """When admin passes a section_id they own, that exact Section wins."""
    course = _make_course(db_session, ADMIN_UID, "ML")
    sec1 = _make_section(db_session, course, "Intro", order_index=0)
    sec2 = _make_section(db_session, course, "Advanced", order_index=1)

    chosen = resolve_section_for_new_video(
        db=db_session, uid=ADMIN_UID, requested_section_id=sec2.id
    )

    assert chosen.id == sec2.id
    assert chosen.title == "Advanced"


def test_resolve_with_explicit_section_id_from_another_admin_is_rejected(
    db_session: Session, admin_user: User
):
    """Defense: can't drop a video into another admin's course by guessing UUIDs."""
    # Another admin's course
    other_uid = "other-admin-uid"
    other_course = _make_course(db_session, other_uid, "Other's Course")
    other_section = _make_section(db_session, other_course, "Private", order_index=0)

    with pytest.raises(ValueError, match="does not belong to your account"):
        resolve_section_for_new_video(
            db=db_session, uid=ADMIN_UID, requested_section_id=other_section.id
        )


def test_resolve_with_nonexistent_section_id_raises(db_session: Session, admin_user: User):
    """Bogus UUID → ValueError (admin gets a clear error, not silent fallback)."""
    with pytest.raises(ValueError, match="does not exist"):
        resolve_section_for_new_video(
            db=db_session, uid=ADMIN_UID, requested_section_id=str(uuid.uuid4())
        )


# ─────────────────────────────────────────────────────────────────────────
# resolve_section_for_new_video: priority 2 (alphabetical fallback)
# ─────────────────────────────────────────────────────────────────────────


def test_resolve_without_section_id_picks_first_alphabetical_course(
    db_session: Session, admin_user: User
):
    """When admin omits section_id, fall back to first Course alphabetically."""
    # Order matters — add 'Z' first to confirm we sort, not insert-order.
    z_course = _make_course(db_session, ADMIN_UID, "Z-Last")
    _make_section(db_session, z_course, "Z Section", order_index=0)
    a_course = _make_course(db_session, ADMIN_UID, "A-First")
    a_sec = _make_section(db_session, a_course, "A Section", order_index=0)

    chosen = resolve_section_for_new_video(
        db=db_session, uid=ADMIN_UID, requested_section_id=None
    )

    assert chosen.id == a_sec.id


def test_resolve_without_section_id_picks_first_section_by_order_index(
    db_session: Session, admin_user: User
):
    """Within a Course, the section with lowest order_index wins."""
    course = _make_course(db_session, ADMIN_UID, "Single")
    _make_section(db_session, course, "Later", order_index=2)
    first_sec = _make_section(db_session, course, "First", order_index=1)

    chosen = resolve_section_for_new_video(
        db=db_session, uid=ADMIN_UID, requested_section_id=None
    )

    assert chosen.id == first_sec.id


# ─────────────────────────────────────────────────────────────────────────
# resolve_section_for_new_video: priority 3 (auto-create)
# ─────────────────────────────────────────────────────────────────────────


def test_resolve_auto_creates_default_when_admin_has_zero_sections(
    db_session: Session, admin_user: User
):
    """First-time admin (no courses) → auto-creates 'Default Catalog' + 'Uncategorized'."""
    assert (
        db_session.query(Section).count() == 0
    ), "precondition: zero sections exist"

    chosen = resolve_section_for_new_video(
        db=db_session, uid=ADMIN_UID, requested_section_id=None
    )

    assert chosen.title == "Uncategorized"
    assert chosen.course.title == "Default Catalog"
    assert chosen.course.user_id == ADMIN_UID
    # And it was persisted
    assert db_session.query(Section).count() == 1


# ─────────────────────────────────────────────────────────────────────────
# ensure_admin_has_a_section: idempotency
# ─────────────────────────────────────────────────────────────────────────


def test_ensure_admin_has_a_section_creates_when_none_exist(
    db_session: Session, admin_user: User
):
    """First call creates the default pair."""
    assert db_session.query(Section).count() == 0

    ensure_admin_has_a_section(db_session, ADMIN_UID)

    assert db_session.query(Section).count() == 1
    section = db_session.query(Section).first()
    assert section.title == "Uncategorized"
    assert section.course.title == "Default Catalog"


def test_ensure_admin_has_a_section_is_idempotent(
    db_session: Session, admin_user: User
):
    """Second call does nothing (admin already has a section)."""
    ensure_admin_has_a_section(db_session, ADMIN_UID)
    first_id = db_session.query(Section).first().id

    ensure_admin_has_a_section(db_session, ADMIN_UID)

    sections = db_session.query(Section).all()
    assert len(sections) == 1, "idempotent: should not create a second one"
    assert sections[0].id == first_id


def test_ensure_admin_has_a_section_does_not_touch_existing_courses(
    db_session: Session, admin_user: User
):
    """If admin already has a course with sections, this is a no-op."""
    existing = _make_course(db_session, ADMIN_UID, "Existing")
    _make_section(db_session, existing, "Existing Sec", order_index=0)

    ensure_admin_has_a_section(db_session, ADMIN_UID)

    # No new course/section was added
    courses = db_session.query(Course).all()
    assert len(courses) == 1
    assert courses[0].title == "Existing"


# ─────────────────────────────────────────────────────────────────────────
# Cross-admin safety
# ─────────────────────────────────────────────────────────────────────────


def test_resolve_does_not_pick_sections_from_other_admins(
    db_session: Session, admin_user: User
):
    """Sanity: even with the admin's first-section fallback, we don't leak
    another admin's courses. Setup: other admin has a section, admin has none.
    Expected: admin gets the auto-created default, NOT the other's section.
    """
    other_uid = "other-admin-uid"
    other_course = _make_course(db_session, other_uid, "Other's Catalog")
    _make_section(db_session, other_course, "Other's Section", order_index=0)

    chosen = resolve_section_for_new_video(
        db=db_session, uid=ADMIN_UID, requested_section_id=None
    )

    # The auto-created default is the admin's OWN; the other admin's
    # section must not bleed through.
    assert chosen.course.user_id == ADMIN_UID
    assert chosen.course.title == "Default Catalog"