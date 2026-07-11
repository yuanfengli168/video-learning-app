"""SQLAlchemy database engine and session setup."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# SQLite needs check_same_thread=False for FastAPI
connect_args = (
    {"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {}
)

engine = create_engine(
    settings.database_url, connect_args=connect_args, echo=settings.debug
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables + run lightweight column migrations. Called on app startup (MVP1).

    `create_all` only creates missing tables, never adds new columns to existing
    ones — so a model field added after the DB already exists won't take effect
    without help. `_apply_migrations` issues idempotent `ALTER TABLE ... ADD
    COLUMN` for any column listed in `_MIGRATIONS` that doesn't already exist.
    """
    from app.models import asset, chat, course, section, video  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _apply_migrations()


# ── Lightweight additive migrations ──────────────────────────────────────────
# Each entry: (table, column, DDL fragment). The column is added with the given
# DDL only if it's missing from the live table. We intentionally keep this
# additive-only — destructive changes belong in a real migration tool.
_MIGRATIONS: list[tuple[str, str, str]] = [
    (
        "videos",
        "last_transcribe_job",
        "ALTER TABLE videos ADD COLUMN last_transcribe_job VARCHAR(2048)",
    ),
    (
        "videos",
        "last_generate_job",
        "ALTER TABLE videos ADD COLUMN last_generate_job VARCHAR(2048)",
    ),
    # MVP2.0 — add scope column to chat_sessions so we can have
    # both flashcard-scope (one chat per concept) and video-scope
    # (one chat per video, discusses whole transcript) sessions in
    # the same table. Default 'flashcard' so all existing rows
    # remain valid.
    (
        "chat_sessions",
        "scope",
        "ALTER TABLE chat_sessions ADD COLUMN scope VARCHAR(32) NOT NULL DEFAULT 'flashcard'",
    ),
]


def _apply_migrations() -> None:
    """Run every entry in `_MIGRATIONS` whose column is missing.

    Safe to call on every startup: missing columns get added, present columns
    are skipped. Logs each applied migration for visibility.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, column, ddl in _MIGRATIONS:
            if not inspector.has_table(table):
                continue  # create_all above will handle brand-new tables
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column in existing:
                continue
            try:
                conn.execute(text(ddl))
                print(f"[migrate] {table}.{column}: added")
            except Exception as e:
                # Don't crash the app on a migration failure — log and move on.
                # Worst case the missing column will surface as a 500 on the
                # first request, which the user can report.
                print(f"[migrate] {table}.{column}: FAILED ({e})")