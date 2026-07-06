"""Tests for Asset model."""

from app.models import Asset, Course, Section, Video


def _create_video(db_session):
    """Helper: create a video for testing."""
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
    return video


def test_create_asset(db_session):
    """Should create an asset."""
    video = _create_video(db_session)
    asset = Asset(video_id=video.id, asset_type="summary", content="# Summary")
    db_session.add(asset)
    db_session.commit()

    assert asset.id is not None
    assert asset.asset_type == "summary"
    assert asset.content == "# Summary"
    assert asset.created_at is not None


def test_asset_types(db_session):
    """Should support all asset types."""
    video = _create_video(db_session)
    types = ["summary", "transcript", "flashcards", "quiz", "mindmap"]
    for t in types:
        asset = Asset(video_id=video.id, asset_type=t, content="{}")
        db_session.add(asset)
    db_session.commit()

    assert len(video.assets) == 5
    asset_types = {a.asset_type for a in video.assets}
    assert asset_types == set(types)


def test_asset_back_populates_video(db_session):
    """Asset should back-populate its video."""
    video = _create_video(db_session)
    asset = Asset(video_id=video.id, asset_type="flashcards", content="[]")
    db_session.add(asset)
    db_session.commit()

    assert asset.video.title == "Intro"


def test_asset_default_content(db_session):
    """Asset content should default to empty string."""
    video = _create_video(db_session)
    asset = Asset(video_id=video.id, asset_type="summary")
    db_session.add(asset)
    db_session.commit()

    assert asset.content == ""