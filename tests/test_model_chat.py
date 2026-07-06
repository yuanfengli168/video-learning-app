"""Tests for Chat models."""

from app.models import ChatMessage, ChatSession, Course, Section, Video


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


def test_create_chat_session(db_session):
    """Should create a chat session."""
    video = _create_video(db_session)
    session = ChatSession(
        user_id="user-123",
        video_id=video.id,
        concept="RAG",
        system_prompt="You are a helpful tutor.",
    )
    db_session.add(session)
    db_session.commit()

    assert session.id is not None
    assert session.user_id == "user-123"
    assert session.concept == "RAG"
    assert session.system_prompt == "You are a helpful tutor."
    assert session.created_at is not None


def test_chat_session_message_relationship(db_session):
    """ChatSession should have a messages relationship."""
    video = _create_video(db_session)
    session = ChatSession(user_id="u1", video_id=video.id, concept="RAG")
    db_session.add(session)
    db_session.flush()

    msg1 = ChatMessage(session_id=session.id, role="user", content="What is RAG?")
    msg2 = ChatMessage(session_id=session.id, role="assistant", content="RAG is...")
    db_session.add_all([msg1, msg2])
    db_session.commit()

    assert len(session.messages) == 2
    assert session.messages[0].role == "user"
    assert session.messages[1].role == "assistant"


def test_chat_message_order(db_session):
    """Messages should be ordered by created_at."""
    import time

    video = _create_video(db_session)
    session = ChatSession(user_id="u1", video_id=video.id, concept="RAG")
    db_session.add(session)
    db_session.flush()

    msg1 = ChatMessage(session_id=session.id, role="user", content="First")
    db_session.add(msg1)
    db_session.commit()
    time.sleep(0.01)

    msg2 = ChatMessage(session_id=session.id, role="assistant", content="Second")
    db_session.add(msg2)
    db_session.commit()

    assert session.messages[0].content == "First"
    assert session.messages[1].content == "Second"


def test_chat_session_cascade_delete(db_session):
    """Deleting a chat session should cascade delete its messages."""
    video = _create_video(db_session)
    session = ChatSession(user_id="u1", video_id=video.id, concept="RAG")
    db_session.add(session)
    db_session.flush()

    msg = ChatMessage(session_id=session.id, role="user", content="Hello")
    db_session.add(msg)
    db_session.commit()
    msg_id = msg.id

    db_session.delete(session)
    db_session.commit()

    assert db_session.get(ChatMessage, msg_id) is None


def test_chat_session_default_system_prompt(db_session):
    """ChatSession system_prompt should default to empty string."""
    video = _create_video(db_session)
    session = ChatSession(user_id="u1", video_id=video.id, concept="RAG")
    db_session.add(session)
    db_session.commit()

    assert session.system_prompt == ""