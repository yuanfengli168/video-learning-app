"""SQLAlchemy database engine and session setup."""

from collections.abc import Generator

from sqlalchemy import create_engine, event
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


# ── WAL journal mode + busy_timeout (2026-09-16, launch hardening #1) ──
#
# Why: the DB ran in journal_mode=delete until now — every write takes a
# DATABASE-WIDE exclusive lock that also blocks READERS. With the
# telemetry beacon writing an events row on every click, 100-user scale
# means hundreds of such locks/minute, each stalling every page render
# behind it (audit doc P0.2/decision #12: the write hot-spot is the
# amplifier of the 9/12 outage class).
#
# WAL changes the contract: writers never block readers, readers never
# block the writer, one writer at a time (fine at this scale — the
# mini-queue will write a handful of rows a minute). Read throughput
# stops being hostage to telemetry writes.
#
# busy_timeout=5000: WAL still serializes WRITES — under a write burst
# a second writer waits up to 5s for the lock instead of failing
# instantly with "database is locked" (the error the upload flow could
# hit when a transcribe progress write collides with a page write).
#
# Scope: file-backed SQLite only. journal_mode is per-database (it
# persists in the file once set — a one-time switch, later connections
# just re-confirm it); busy_timeout is per-connection so it must be set
# on EVERY pooled connection — hence the connect event below, not a
# one-shot call.
def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    try:
        # journal_mode=WAL — persistent in the DB file, cheap to re-assert.
        cursor.execute("PRAGMA journal_mode=WAL")
        # busy_timeout (ms) — per-connection, must be re-set on every
        # connection the pool hands out.
        cursor.execute("PRAGMA busy_timeout=5000")
        # synchronous=NORMAL — the WAL-recommended durability level:
        # fsync on checkpoint instead of every commit. Safe for our data
        # (worst case: lose the last commit on a power cut; the 6-hour
        # DB backup cadence is the real durability anchor) while
        # cutting per-write fsync latency ~5-10x.
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def _is_file_backed_sqlite(url: str) -> bool:
    """True for SQLite URLs that point at a FILE (not memory).

    The original guard (`":memory:" not in url`) misses bare
    `sqlite://` — which IS a memory DB (conftest builds its test
    engine with exactly that URL). WAL is a file-level property and
    the pragmas are pointless (journal_mode=WAL is a no-op on
    :memory:, synchronous/fsync never touches disk) — so register the
    listener only when there's an actual database path.
    """
    if not url.startswith("sqlite"):
        return False
    # sqlite:///path/to.db  → path is everything after the triple slash.
    # sqlite://            → memory (no path). sqlite:///:memory: → memory.
    after_scheme = url.split("sqlite://", 1)[1]
    # Strip an optional host part (sqlite://host/path) — ours are hostless.
    path = after_scheme.lstrip("/")
    # ':memory:' is the documented in-memory marker; an empty path is
    # the OTHER in-memory form (bare sqlite:// — conftest's shape).
    return bool(path) and path != ":memory:"


if _is_file_backed_sqlite(settings.database_url):
    event.listens_for(engine, "connect")(_set_sqlite_pragmas)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ── Pool watchdog (2026-09-20 — the QueuePool-exhaustion outage) ──────────
#
# WHAT HAPPENED: a claim bug in the transcribe queue rolled back status
# flips, letting the 4 per-process schedulers re-claim and re-dispatch the
# same video every 3s. The live incident stacked ~60 concurrent transcribe
# threads (15/worker), each holding a checked-out pool connection through
# multi-minute Whisper work — 60 checked-out vs a 30-capacity pool meant
# EVERY request timed out (the "Internal server error: TimeoutError" users
# saw on 9/19 AND 9/20; the site sat dark for hours between restarts).
#
# WHAT THIS DOES: a daemon thread per process samples the pool every 30s.
# Sustained near-full checked-out counts (>24 of 30 for 3 consecutive
# samples = 90s) mean a leak, not a burst. When tripped it:
#   1. logs pool diagnostics + a thread list (the evidence we had to
#      sudo-sample by hand during the incident)
#   2. calls engine.pool.dispose() — closes ALL checked-out/idle
#      connections; in-use sessions get fresh connections on their next
#      checkout. Leaked-but-dead sessions release here; live threads
#      transparently reconnect. This is the self-heal: pool pressure
#      clears without a restart.
# The root-cause fix is the claim-commit in transcribe_queue.py; this
# watchdog is defense in depth for ANY future session leak.
_WATCHDOG_INTERVAL_SECONDS = 30
_WATCHDOG_TRIP_THRESHOLD = 24  # of 30 — 80% sustained
_WATCHDOG_TRIP_SAMPLES = 3     # consecutive samples → dispose

_pool_watchdog_started = False
_pool_watchdog_lock = __import__("threading").Lock()


def _start_pool_watchdog() -> None:
    """Start the per-process pool watchdog (idempotent)."""
    global _pool_watchdog_started
    import logging
    import threading

    with _pool_watchdog_lock:
        if _pool_watchdog_started:
            return
        _pool_watchdog_started = True

    log = logging.getLogger("database.pool_watchdog")

    def _watchdog_loop() -> None:
        import time

        consecutive_high = 0
        while True:
            time.sleep(_WATCHDOG_INTERVAL_SECONDS)
            try:
                # NOTE (2026-09-20 hotfix): pool.status() returns a
                # human-readable STRING in this SQLAlchemy version
                # ("QueuePool id:... size: N"), not a metrics tuple —
                # the first watchdog release indexed it and TypeError'd
                # on every tick, crashing the thread. The real metrics
                # are the checkedout()/checkedin()/overflow() calls.
                checked_out = engine.pool.checkedout()
                overflow = engine.pool.overflow()
                if checked_out >= _WATCHDOG_TRIP_THRESHOLD:
                    consecutive_high += 1
                    log.warning(
                        "pool pressure: checked_out=%d overflow=%d "
                        "sample %d/%d",
                        checked_out, overflow,
                        consecutive_high, _WATCHDOG_TRIP_SAMPLES,
                    )
                    if consecutive_high >= _WATCHDOG_TRIP_SAMPLES:
                        log.error(
                            "pool watchdog DISPOSING pool — %d connections "
                            "checked out for >%ds (session leak?). "
                            "Threads: %s",
                            checked_out,
                            _WATCHDOG_INTERVAL_SECONDS * _WATCHDOG_TRIP_SAMPLES,
                            [
                                f"{t.name}({'daemon' if t.daemon else 'user'})"
                                for t in threading.enumerate()
                            ],
                        )
                        engine.pool.dispose()
                        consecutive_high = 0
                else:
                    consecutive_high = 0
            except Exception:
                # Never die; a watchdog crash must not take the worker.
                log.exception("pool watchdog tick failed")

    t = threading.Thread(
        target=_watchdog_loop,
        daemon=True,
        name="pool-watchdog",
    )
    t.start()
    log.info(
        "pool watchdog started (threshold=%d/%d for %d samples)",
        _WATCHDOG_TRIP_THRESHOLD, 30, _WATCHDOG_TRIP_SAMPLES,
    )


_start_pool_watchdog()


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
        event,
        paid_waitlist,
        plugin_run,
        section,
        upload_session,
        user,
        video,
    )

    Base.metadata.create_all(bind=engine)
    _apply_migrations()
    # 2026-09-22: data scrub (idempotent — see its docstring). Runs
    # after the schema pass so the table/columns always exist.
    _scrub_plugin_run_message_paths()


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
    # MVP2.0 (read-heavy pivot) — videos.visibility. Int enum matching
    # VideoVisibility (see app/auth/roles.py):
    #   0 = PUBLIC      → FREE, PAID, ADMIN all see
    #   1 = PAID_ONLY   → PAID, ADMIN see (paywall for FREE)
    #   2 = ADMIN_ONLY  → ADMIN only (drafts, internal)
    # Default 0 (PUBLIC) backfills all existing rows so no admin
    # action is needed; every video uploaded before this migration
    # becomes free-tier content, which matches the v1.0 launch
    # posture (no paid tier yet). See doc/mvp2-roles-and-access.md.
    (
        "videos",
        "visibility",
        "ALTER TABLE videos ADD COLUMN visibility INTEGER NOT NULL DEFAULT 0",
    ),
    # MVP2.0 (read-heavy pivot) — videos.youtube_id. The 11-char YouTube
    # video ID (e.g. "dQw4w9WgXcQ") extracted from the pasted URL. NULL
    # allowed for legacy rows (uploaded videos that didn't go through
    # YouTube — they keep working, just won't appear in MVP2 catalog
    # because admin only sees youtube_id-typed rows in the new flow).
    # VARCHAR(11) is exact for YouTube video IDs (we extract strictly).
    (
        "videos",
        "youtube_id",
        "ALTER TABLE videos ADD COLUMN youtube_id VARCHAR(11)",
    ),
    # MVP2.0 (Day 2B) — videos.thumbnail_url. Populated from YouTube
    # Data API v3 (maxres/default thumbnail). Used by the catalog grid
    # (dashboard.html) to show a real preview instead of the 🎬 emoji.
    # Nullable + no default — legacy rows stay valid (no thumbnail).
    # URL length: YouTube's maxres thumbnails are ~120 chars, but we
    # size up to 512 for headroom.
    (
        "videos",
        "thumbnail_url",
        "ALTER TABLE videos ADD COLUMN thumbnail_url VARCHAR(512)",
    ),
    # MVP2.0 (Day 2B) — videos.channel. YouTube channel title from the
    # API (e.g. "Rick Astley", "3Blue1Brown"). Display in the catalog
    # card so users know what they're clicking. Nullable; legacy rows
    # have no channel info. VARCHAR(255) is plenty for human-readable
    # channel names.
    (
        "videos",
        "channel",
        "ALTER TABLE videos ADD COLUMN channel VARCHAR(255)",
    ),
    # MVP2.0 (Day 2B) — videos.caption_languages. JSON array of BCP-47
    # language codes for available captions (e.g. ["en","ja","zh"]).
    # Populated from captions.list. Used by Day 3 to pick which track
    # to download first. Empty list (not NULL) for videos with no
    # captions. Stored as TEXT (JSON) to avoid creating a join table
    # for what is read-only metadata.
    (
        "videos",
        "caption_languages",
        "ALTER TABLE videos ADD COLUMN caption_languages TEXT DEFAULT '[]'",
    ),
    # 2026-09-08 — plugin_runs.user_id. Who submitted the run.
    # submit() has always received the caller's uid from the HTTP
    # endpoint but only held it in the in-memory queue; storing it
    # enables /admin/analytics distinct-user Tools-usage stats and
    # future per-user audit. Nullable: legacy rows + the synchronous
    # test mode didn't record it.
    (
        "plugin_runs",
        "user_id",
        "ALTER TABLE plugin_runs ADD COLUMN user_id VARCHAR(128)",
    ),
    # 2026-09-08 — courses.channel_id. The catalog hierarchy becomes
    # Channel → Course(=playlist) → Section → Video. NULL = personal
    # course (pre-channel behavior); NOT NULL = channel-owned catalog
    # playlist. The channels table itself is created by create_all
    # (new table — no migration needed for it).
    (
        "courses",
        "channel_id",
        "ALTER TABLE courses ADD COLUMN channel_id VARCHAR(36) "
        "REFERENCES channels(id) ON DELETE SET NULL",
    ),
    # 2026-09-09 — videos.view_count. YouTube view count at last
    # refresh, for the dashboard 'Top Viewed' tab. Snapshot refreshed
    # daily at 00:00 SGT by scripts/refresh_youtube_views.py (launchd);
    # single-video enrichment fills it at import time. Nullable:
    # legacy rows + playlist-mode bulk imports (playlistItems.list has
    # no statistics part) start NULL until the first nightly refresh.
    (
        "videos",
        "view_count",
        "ALTER TABLE videos ADD COLUMN view_count INTEGER",
    ),
    # 2026-09-21 (13a) — per-user upload limit overrides. NULL = tier
    # default (env var); a set value BEATS the tier default. This is
    # the paid-add-on infrastructure (doc/limits-registry.md §1/§2):
    # "they paid more → flip their override" via the flip-kit. Both
    # nullable so legacy rows keep the tier defaults.
    (
        "users",
        "max_file_bytes",
        "ALTER TABLE users ADD COLUMN max_file_bytes BIGINT",
    ),
    (
        "users",
        "storage_quota_bytes",
        "ALTER TABLE users ADD COLUMN storage_quota_bytes BIGINT",
    ),
    # 2026-09-22 (model preference, doc/model-preference-design.md) —
    # per-user ollama model override. NULL = tier default
    # (LLM_MODEL_PAID_DEFAULT / LLM_MODEL_ADMIN_DEFAULT); a set value
    # must ALSO be in LLM_MODEL_CATALOG at read time (removed-from-
    # catalog values fall back gracefully). Only the ADMIN's row is
    # written today (via /admin/settings); the MVP3 PAID picker is
    # this same column.
    (
        "users",
        "llm_model_pref",
        "ALTER TABLE users ADD COLUMN llm_model_pref VARCHAR(128)",
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


def _scrub_plugin_run_message_paths() -> None:
    """2026-09-22 one-time data scrub: strip absolute paths from
    plugin_runs.message rows.

    The old webm_to_mp4 success message embedded the output path
    ("…You can find the new file at: /Volumes/…") and that message is
    echoed to ANY role via the runs endpoints and the green box —
    rows written before the fix keep leaking the server's volume
    layout. This scrub removes the trailing path sentence, matching
    what a fresh run now writes. Idempotent: the LIKE clause only
    matches rows that still carry the sentence, so re-running on
    every startup is a no-op after the first pass.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        updated = conn.execute(
            text(
                "UPDATE plugin_runs SET message = TRIM("
                "  SUBSTR(message, 1, "
                "    INSTR(message || ' You can find', ' You can find') - 1)) "
                "WHERE message LIKE '% You can find the new file at:%'"
            )
        )
        if updated.rowcount:
            print(
                f"[migrate] plugin_runs.message: scrubbed absolute "
                f"paths from {updated.rowcount} row(s)"
            )