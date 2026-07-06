"""Tests for Video model."""

from app.models import Asset, Course, Section, Video


def test_create_video(db_session):
    """Should create a video with default fields."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()

    section = Section(title="Week 1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    video = Video(
        title="Intro to NN",
        filename="intro.mp4",
        file_path="/uploads/intro.mp4",
        file_size=1024,
        section_id=section.id,
    )
    db_session.add(video)
    db_session.commit()

    assert video.id is not None
    assert video.title == "Intro to NN"
    assert video.status == "pending"
    assert video.whisper_model == "base"
    assert video.file_size == 1024
    assert video.duration == 0


def test_video_asset_relationship(db_session):
    """Video should have an assets relationship."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()

    section = Section(title="Week 1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    video = Video(title="Intro", filename="intro.mp4", file_path="/tmp/intro.mp4",
                  section_id=section.id)
    db_session.add(video)
    db_session.flush()

    asset = Asset(video_id=video.id, asset_type="summary", content="# Summary")
    db_session.add(asset)
    db_session.commit()

    assert len(video.assets) == 1
    assert video.assets[0].asset_type == "summary"


def test_video_cascade_delete(db_session):
    """Deleting a video should cascade delete its assets."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()

    section = Section(title="Week 1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    video = Video(title="Intro", filename="intro.mp4", file_path="/tmp/intro.mp4",
                  section_id=section.id)
    db_session.add(video)
    db_session.flush()

    asset = Asset(video_id=video.id, asset_type="summary", content="content")
    db_session.add(asset)
    db_session.commit()
    asset_id = asset.id

    db_session.delete(video)
    db_session.commit()

    assert db_session.get(Asset, asset_id) is None


def test_video_status_values(db_session):
    """Video status should accept various processing states."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()

    section = Section(title="Week 1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    for status in ["pending", "transcribing", "generating", "ready", "error"]:
        video = Video(title=f"V-{status}", filename="v.mp4", file_path="/tmp/v.mp4",
                      section_id=section.id, status=status)
        db_session.add(video)

    db_session.commit()
    videos = db_session.query(Video).all()
    statuses = {v.status for v in videos}
    assert "pending" in statuses
    assert "ready" in statuses
    assert "error" in statuses