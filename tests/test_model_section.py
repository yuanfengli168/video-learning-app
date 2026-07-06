"""Tests for Section model."""

from app.models import Course, Section, Video


def test_create_section(db_session):
    """Should create a section."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()

    section = Section(title="Week 1: Neural Networks", course_id=course.id, order_index=0)
    db_session.add(section)
    db_session.commit()

    assert section.id is not None
    assert section.title == "Week 1: Neural Networks"
    assert section.order_index == 0
    assert section.course_id == course.id


def test_section_video_relationship(db_session):
    """Section should have a videos relationship."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()

    section = Section(title="Week 1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    video = Video(title="Intro", filename="intro.mp4", file_path="/tmp/intro.mp4",
                  section_id=section.id)
    db_session.add(video)
    db_session.commit()

    assert len(section.videos) == 1
    assert section.videos[0].title == "Intro"


def test_section_cascade_delete(db_session):
    """Deleting a section should cascade delete its videos."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()

    section = Section(title="Week 1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    video = Video(title="Intro", filename="intro.mp4", file_path="/tmp/intro.mp4",
                  section_id=section.id)
    db_session.add(video)
    db_session.commit()
    video_id = video.id

    db_session.delete(section)
    db_session.commit()

    assert db_session.get(Video, video_id) is None


def test_section_back_populates_course(db_session):
    """Section should back-populate its course."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()

    section = Section(title="Week 1", course_id=course.id)
    db_session.add(section)
    db_session.commit()

    assert section.course.title == "ML"