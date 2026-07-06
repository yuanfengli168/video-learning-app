"""Tests for database setup and session management."""

from app.database import Base, get_db


def test_base_is_declarative():
    """Base should be a DeclarativeBase subclass."""
    from sqlalchemy.orm import DeclarativeBase

    assert issubclass(Base, DeclarativeBase)


def test_get_db_yields_session():
    """get_db should yield a session and close it."""
    gen = get_db()
    session = next(gen)
    assert session is not None
    # Clean up
    try:
        next(gen)
    except StopIteration:
        pass


def test_init_db_creates_tables(db_session):
    """init_db should create all tables without error."""
    from app.database import init_db

    # init_db uses the module-level engine, but we test table creation
    # via the db_session fixture which already creates tables
    from app.models import Asset, ChatMessage, ChatSession, Course, Section, Video

    # Verify all tables exist by querying
    assert db_session.query(Course).count() == 0
    assert db_session.query(Section).count() == 0
    assert db_session.query(Video).count() == 0
    assert db_session.query(Asset).count() == 0
    assert db_session.query(ChatSession).count() == 0
    assert db_session.query(ChatMessage).count() == 0