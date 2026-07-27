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

# Pool sizing (MVP2.1.0.1+ hotfix for the 2026-07-24 4.3 GB WebM bug).
#
# The default SQLAlchemy QueuePool is size=5, overflow=10
# (15 connections total). That was fine for the original
# MVP1 design where the worker held a connection for the
# full 5-10 min ffmpeg transcode — but with concurrent UI
# polling, every poll and page view competes for one of
# the remaining 14 slots, and the pool exhausts in seconds,
# throwing QueuePoolTimeoutError on /video/<id>.
#
# Bumping to size=10, overflow=20 (30 total) gives enough
# headroom for:
#   - 3 concurrent plugin workers (the PluginPool limit)
#   - ~10 in-flight FastAPI requests (UI polls, page loads)
#   - a few bursts from background jobs
# plus headroom for transcribe workers etc. SQLite is
# single-writer anyway, so the practical limit is GIL/
# disk contention, not pool exhaustion.
#
# These kwargs only apply to QueuePool (file-backed SQLite
# and non-SQLite backends). The :memory: SQLite used by
# tests gets a SingletonThreadPool that doesn't accept
# these args — we skip them in that case to keep the test
# suite happy.
engine_kwargs: dict = dict(connect_args=connect_args, echo=settings.debug)
if not (
    settings.database_url.startswith("sqlite")
    and ":memory:" in settings.database_url
):
    engine_kwargs.update(
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
    )
engine = create_engine(settings.database_url, **engine_kwargs)

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
    from app.models import (  # noqa: F401
        asset,
        chat,
        course,
        plugin_run,
        section,
        video,
    )

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
    # MVP2.0.4 — transcribe_started_at. Stamped at the top of
    # _run_transcribe_job (BEFORE whisper loads) so that
    # transcribed_at - transcribe_started_at gives the actual
    # transcribe duration WITHOUT queue-wait time. Nullable so
    # legacy rows (uploaded before MVP2.0.4) keep working — the
    # course page template falls back to the old
    # generated_at - created_at formula for those.
    (
        "videos",
        "transcribe_started_at",
        "ALTER TABLE videos ADD COLUMN transcribe_started_at DATETIME",
    ),
    # MVP2.1.0.1 — plugin_runs.status. The PluginPool worker
    # (app/workers/plugin_pool.py) writes this as
    #   queued  → created by submit() before the job is picked up
    #   running → the worker starts the plugin function
    #   done    → plugin returned ok=True
    #   failed  → plugin returned ok=False OR the worker crashed
    # so the UI can poll and show progress without holding the
    # HTTP request open. Default 'done' backfills legacy rows
    # (which were created by the synchronous-in-request code
    # path) as already-finished runs.
    (
        "plugin_runs",
        "status",
        "ALTER TABLE plugin_runs ADD COLUMN status VARCHAR(20) DEFAULT 'done'",
    ),
    # Pocket v0.1.3 — real teaching UX: typed answers, AI feedback,
    # favorites. PocketProgress gains the student's answer text, the
    # favorite flag (indexed), and the last AI grading snapshot so
    # the iOS app can render the verdict + explanation without
    # re-querying Ollama. PocketChunk gets a transcript quote the
    # teacher (Ollama) must cite in its lesson so the student can
    # cross-reference back to the source video.
    (
        "pocket_progress",
        "user_answer",
        "ALTER TABLE pocket_progress ADD COLUMN user_answer TEXT DEFAULT ''",
    ),
    (
        "pocket_progress",
        "is_favorite",
        "ALTER TABLE pocket_progress ADD COLUMN is_favorite BOOLEAN DEFAULT 0",
    ),
    (
        "pocket_progress",
        "last_ai_verdict",
        "ALTER TABLE pocket_progress ADD COLUMN last_ai_verdict VARCHAR(16) DEFAULT ''",
    ),
    (
        "pocket_progress",
        "last_ai_explanation",
        "ALTER TABLE pocket_progress ADD COLUMN last_ai_explanation TEXT DEFAULT ''",
    ),
    (
        "pocket_progress",
        "last_ai_graded_at",
        "ALTER TABLE pocket_progress ADD COLUMN last_ai_graded_at DATETIME",
    ),
    (
        "pocket_chunks",
        "transcript_quote",
        "ALTER TABLE pocket_chunks ADD COLUMN transcript_quote TEXT DEFAULT ''",
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