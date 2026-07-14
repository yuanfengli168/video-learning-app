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
    # MVP3.0 #8 — completion timestamps for the transcribe and
    # generate pipeline steps. Nullable so legacy rows (uploaded
    # before MVP3.0) stay valid; new uploads get the timestamps
    # set by the workers when each step reaches status=ready. The
    # course page renders "ready · in 9:08" by computing
    # generated_at - created_at when both are present.
    (
        "videos",
        "transcribed_at",
        "ALTER TABLE videos ADD COLUMN transcribed_at DATETIME",
    ),
    (
        "videos",
        "generated_at",
        "ALTER TABLE videos ADD COLUMN generated_at DATETIME",
    ),
    # MVP3.0 #2 — whisper backend + resolved-model columns. The
    # original `whisper_model` column is repurposed (now stores the
    # user-facing *choice* key like "base" or "local-best-and-fast"
    # instead of just a model_id), and we add 3 new columns to
    # track the actual backend that ran, the resolved HF model
    # name, and any fallback reason. All 3 are nullable + String,
    # so legacy rows (whisper_model = "base") remain valid.
    (
        "videos",
        "whisper_backend",
        "ALTER TABLE videos ADD COLUMN whisper_backend VARCHAR(32)",
    ),
    (
        "videos",
        "whisper_resolved_model",
        "ALTER TABLE videos ADD COLUMN whisper_resolved_model VARCHAR(64)",
    ),
    (
        "videos",
        "whisper_fallback_reason",
        "ALTER TABLE videos ADD COLUMN whisper_fallback_reason VARCHAR(512)",
    ),
    # MVP3.0 #2b — primary language for the video. NULL means
    # "not yet detected"; once set, the transcribe worker passes
    # it as `language=` to whisper so the model is LOCKED for the
    # whole file (prevents per-window drift on long audio — the
    # 2026-07-13 "Thank you" hallucination on the 2.5h Mandarin
    # file). Set automatically by auto-detection (samples the
    # first 10 min) or manually by the user via the language
    # dropdown on the video page. Stored as a 2-8 char whisper
    # code (e.g. "zh", "en", "ja").
    (
        "videos",
        "language",
        "ALTER TABLE videos ADD COLUMN language VARCHAR(8)",
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