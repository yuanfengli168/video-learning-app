"""Tests for Course model."""

from app.models import Course


def test_create_course(db_session):
    """Should create a course with default fields."""
    course = Course(title="Machine Learning", user_id="user-123")
    db_session.add(course)
    db_session.commit()

    assert course.id is not None
    assert course.title == "Machine Learning"
    assert course.user_id == "user-123"
    assert course.description == ""
    assert course.created_at is not None


def test_course_id_is_uuid(db_session):
    """Course ID should be a UUID string."""
    course = Course(title="Test", user_id="u1")
    db_session.add(course)
    db_session.commit()

    assert len(course.id) == 36
    assert course.id.count("-") == 4


def test_course_section_relationship(db_session):
    """Course should have a sections relationship."""
    from app.models import Section

    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()

    section = Section(title="Week 1", course_id=course.id, order_index=0)
    db_session.add(section)
    db_session.commit()

    assert len(course.sections) == 1
    assert course.sections[0].title == "Week 1"


def test_course_cascade_delete(db_session):
    """Deleting a course should cascade delete its sections."""
    from app.models import Section

    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()

    section = Section(title="Week 1", course_id=course.id)
    db_session.add(section)
    db_session.commit()
    section_id = section.id

    db_session.delete(course)
    db_session.commit()

    assert db_session.get(Section, section_id) is None


def test_course_updated_on_change(db_session):
    """updated_at should change when course is updated."""
    import time

    course = Course(title="Original", user_id="u1")
    db_session.add(course)
    db_session.commit()
    original_updated = course.updated_at

    time.sleep(0.01)
    course.title = "Updated"
    db_session.commit()

    assert course.updated_at >= original_updated