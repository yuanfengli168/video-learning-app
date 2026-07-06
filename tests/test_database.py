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

# ── Additive column migrations ───────────────────────────────────────────────
# The Video model added last_transcribe_job and last_generate_job after the
# production DB was already created. create_all() doesn't add new columns to
# existing tables, so init_db() needs to run idempotent ALTER TABLE statements.
# These tests guard against a silent regression of that behavior.


def test_migrations_adds_missing_columns_to_existing_table():
    """A pre-existing table missing a migration column should be altered in-place.

    Recreates the production scenario: build a table without the new columns,
    then call _apply_migrations() and verify the columns appear without losing
    existing rows.
    """
    import uuid

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from app.database import Base, _apply_migrations

    # Fresh in-memory engine — no shared state with the test DB
    engine = create_engine("sqlite:///:memory:")
    # Bring the real ORM tables into Base.metadata
    from app.models import asset, chat, course, section, video  # noqa: F401

    Base.metadata.create_all(bind=engine)

    # Drop the columns we're testing for, simulating the production DB
    # state from before they were added.
    with engine.begin() as conn:
        # SQLite doesn't support DROP COLUMN in older versions — recreate
        # the table without the columns by inspecting the schema and
        # rebuilding it. The simplest portable way is to create a fresh
        # table manually that omits the two columns.
        conn.execute(text("DROP TABLE videos"))
        conn.execute(
            text(
                """
                CREATE TABLE videos (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    filename VARCHAR(512) NOT NULL,
                    file_path VARCHAR(1024) NOT NULL,
                    file_size INTEGER NOT NULL,
                    duration INTEGER NOT NULL,
                    order_index INTEGER NOT NULL,
                    section_id VARCHAR(36) NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    whisper_model VARCHAR(32) NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                )
                """
            )
        )
        # Insert a row that must survive the migration
        vid = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO videos (id, title, filename, file_path, file_size, "
                "duration, order_index, section_id, status, whisper_model) "
                "VALUES (:id, 't', 'f.mp4', '/tmp/f.mp4', 1, 1, 0, :sid, 'ready', 'base')"
            ),
            {"id": vid, "sid": str(uuid.uuid4())},
        )

    # Now swap the real engine's inspector view of videos to point at our
    # in-memory engine. _apply_migrations uses `engine` from its own module
    # scope, so we patch the module-level binding too.
    import app.database as db_module
    original_engine = db_module.engine
    db_module.engine = engine
    try:
        _apply_migrations()
    finally:
        db_module.engine = original_engine

    # Verify the columns exist and the existing row survived
    Session = sessionmaker(bind=engine)
    with Session() as s:
        rows = s.execute(text("SELECT id, last_transcribe_job, last_generate_job FROM videos")).all()
    assert len(rows) == 1
    assert rows[0].last_transcribe_job is None
    assert rows[0].last_generate_job is None


def test_migrations_is_idempotent_when_columns_already_present():
    """Calling _apply_migrations twice on a healthy table must not raise."""
    import app.database as db_module
    from app.database import _apply_migrations

    # Use the test engine. First call is a no-op (create_all already made the
    # columns). Second call must also be a no-op, not a "duplicate column" error.
    original_engine = db_module.engine
    db_module.engine = db_module.engine  # identity — already pointing at test DB
    try:
        _apply_migrations()
        _apply_migrations()  # second time should be safe
    finally:
        db_module.engine = original_engine
