"""Chunked-upload session service (13a commit B, 2026-09-21).

Implements the registry §3 design: init → chunks → status → complete →
delete. The FILESYSTEM is the source of truth for chunk inventory
(one staging dir per session, one file per chunk); this service keeps
the DB row and the disk in sync, and hands completed uploads to the
existing transcribe queue (nothing downstream changes).

Security posture (every rule enforced server-side, never trusting the
client):
  - capability gate (UPLOAD_VIDEO) at the ROUTER layer
  - ownership check on EVERY call (another user's session → 404, not
    403-with-existence — don't leak session ids)
  - extension allowlist shared with the legacy path
  - per-file limit + quota headroom via the upload_limits resolver —
    the chunk-SUM is what matters, never a per-chunk check (registry
    §1 trap)
  - chunk index bounds + idempotent chunk writes (re-send is always
    safe — the network-retry contract)
  - declared-vs-actual byte verification at complete (a lying client
    gets a 400 and nothing is queued)
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import uuid
from datetime import datetime, timezone as _tz
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import UploadSession, User, Video
from app.services.upload_limits import (
    UploadLimitError,
    check_file_size,
    check_quota_headroom,
)

logger = logging.getLogger(__name__)

# Shared with the legacy upload path (app/routers/videos.py) — one
# allowlist so the two paths can never drift.
ALLOWED_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}

# Session states (registry §3a)
STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"


def _now() -> datetime:
    """Naive UTC — matches the DB's datetime convention everywhere."""
    return datetime.now(_tz.utc).replace(tzinfo=None)


def sessions_root() -> Path:
    """Staging root: <upload_dir>/sessions/<session_id>/chunk_NNNNNN."""
    root = settings.upload_path / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _session_dir(session_id: str) -> Path:
    return sessions_root() / session_id


def _chunk_path(session_id: str, index: int) -> Path:
    # Zero-padded so directory listing order == chunk order (the
    # assembly path concatenates in sorted order without re-parsing).
    return _session_dir(session_id) / f"chunk_{index:06d}"


# ── init ────────────────────────────────────────────────────────────────────


def create_session(
    db: Session,
    user: User,
    section_id: str,
    *,
    filename: str,
    declared_size: int,
    section_owner_ok: bool,
    whisper_model: str | None = None,
    language: str | None = None,
) -> UploadSession:
    """Validate everything UP FRONT, then create the session row + dir.

    `section_owner_ok`: the caller (router) has already verified the
    section exists and belongs to the user — kept out of the service
    so the router owns the HTTP-shaped 403/404 mapping.

    Raises UploadLimitError (file size / quota — the friendly wording
    from the registry) or ValueError (bad extension / one-session rule
    / non-positive size) for the router to map to 4xx.
    """
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"File type '{ext or '(none)'}' not allowed. "
            f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    if declared_size <= 0:
        raise ValueError("Declared file size must be positive.")

    # Per-file limit (registry §1 — the resolver carries the override
    # logic; the message is the friendly, actionable one).
    check_file_size(user, declared_size)

    # One active session per user (registry §3 — uplink self-serializes;
    # blocks the declared-reservation stacking game).
    existing = (
        db.execute(
            select(UploadSession).where(
                UploadSession.user_id == user.user_id,
                UploadSession.status == STATUS_ACTIVE,
            )
        )
        .scalar_one_or_none()
    )
    if existing is not None:
        raise ValueError(
            "You already have an upload in progress. Finish or cancel it "
            "before starting another."
        )

    # Quota headroom AFTER the one-session check (so the error says
    # "won't fit", not "already uploading" when both are true — the
    # more actionable error wins).
    check_quota_headroom(db, user, declared_size)

    chunk_size = settings.upload_chunk_size_mb * 1024 * 1024
    total_chunks = math.ceil(declared_size / chunk_size)

    session = UploadSession(
        id=str(uuid.uuid4()),
        user_id=user.user_id,
        section_id=section_id,
        original_filename=filename[:512],
        extension=ext,
        declared_size=declared_size,
        chunk_size=chunk_size,
        total_chunks=total_chunks,
        whisper_model=whisper_model,
        language=language,
        status=STATUS_ACTIVE,
        last_activity_at=_now(),
    )
    db.add(session)
    db.commit()

    _session_dir(session.id).mkdir(parents=True, exist_ok=True)
    logger.info(
        "upload session %s init: user=%s %d bytes in %d chunks",
        session.id[:8], user.user_id[:8], declared_size, total_chunks,
    )
    return session


# ── chunks ──────────────────────────────────────────────────────────────────


def _get_owned_session(db: Session, session_id: str, uid: str) -> UploadSession:
    """Fetch a session the caller OWNS or raise ValueError (router maps
    to 404 — never leak another user's session existence)."""
    session = db.get(UploadSession, session_id)
    if session is None or session.user_id != uid:
        raise LookupError("Upload session not found.")
    return session


def append_chunk(
    db: Session, session: UploadSession, index: int, data: bytes
) -> int:
    """Write one chunk. Idempotent: re-sending overwrites the same file.

    Returns the chunk count now on disk (the client's resume signal).
    Raises ValueError on bounds/status (router maps to 400/409).
    """
    if session.status != STATUS_ACTIVE:
        # §3a's explicit race answer: a session claimed by the sweeper
        # (or completed/cancelled) rejects chunks — the client restarts.
        raise PermissionError(
            "This upload session is no longer active "
            f"(status: {session.status}). Start a new upload."
        )
    if not 0 <= index < session.total_chunks:
        raise ValueError(
            f"Chunk index {index} out of range (0..{session.total_chunks - 1})."
        )
    expected = session.chunk_size
    if index == session.total_chunks - 1:
        # Last chunk: exactly the remainder (or full size if even).
        remainder = session.declared_size - (index * session.chunk_size)
        expected = remainder
    if len(data) != expected:
        raise ValueError(
            f"Chunk {index} is {len(data)} bytes; expected {expected}. "
            "The upload client and server disagree on chunk size."
        )

    path = _chunk_path(session.id, index)
    tmp = path.with_suffix(".tmp")  # atomic-ish: write then rename
    tmp.write_bytes(data)
    tmp.replace(path)

    session.last_activity_at = _now()  # the sweeper's TTL clock
    db.commit()
    return len(received_chunks(session.id))


def received_chunks(session_id: str) -> list[int]:
    """Which chunks exist on disk — filesystem-as-truth (§3a)."""
    d = _session_dir(session_id)
    if not d.is_dir():
        return []
    out: list[int] = []
    for p in d.glob("chunk_*"):
        try:
            out.append(int(p.name.removeprefix("chunk_")))
        except ValueError:
            continue  # stray file — the sweeper owns the dir lifecycle
    return sorted(out)


# ── status ──────────────────────────────────────────────────────────────────


def get_status(db: Session, session: UploadSession) -> dict:
    """The resume/status payload (same shape the /usage card uses)."""
    got = received_chunks(session.id)
    return {
        "session_id": session.id,
        "status": session.status,
        "total_chunks": session.total_chunks,
        "received_chunks": got,
        "received_bytes": sum(
            _chunk_path(session.id, i).stat().st_size for i in got
        ),
        "declared_size": session.declared_size,
        "complete": len(got) == session.total_chunks,
    }


# ── complete ───────────────────────────────────────────────────────────────


def complete_session(
    db: Session, session: UploadSession
) -> Video:
    """Assemble chunks → verify → create the Video row → queue handoff.

    The Video row is created 'queued' and the transcribe job is
    registered BEFORE anything else touches it — the 9/19 lesson (the
    queue's dispatcher requires the job tracker entry; registration
    lives with the row creator, same as the legacy upload path).
    The staging dir is consumed (renamed to the final UUID name) —
    no copy, one rename, then the session row flips 'completed'.
    """
    if session.status != STATUS_ACTIVE:
        raise PermissionError(
            f"Session is already {session.status}."
        )

    got = received_chunks(session.id)
    if len(got) != session.total_chunks:
        missing = sorted(
            set(range(session.total_chunks)) - set(got)
        )[:5]
        raise ValueError(
            f"Upload incomplete: {session.total_chunks - len(got)} chunk(s) "
            f"missing (e.g. {missing}). Send the remaining chunks first."
        )

    # Byte verification (a lying declared_size gets nothing queued).
    assembled_size = sum(
        _chunk_path(session.id, i).stat().st_size for i in got
    )
    if assembled_size != session.declared_size:
        raise ValueError(
            f"Assembled {assembled_size} bytes but the session declared "
            f"{session.declared_size}. Re-upload required."
        )

    # Assemble: concatenate chunk files IN ORDER into the final path.
    from app.services.transcription import get_default_model_choice

    video_id = str(uuid.uuid4())
    final_name = f"{video_id}{session.extension}"
    final_path = settings.upload_path / final_name
    with open(final_path, "wb") as out:
        for i in got:
            with open(_chunk_path(session.id, i), "rb") as chunk:
                shutil.copyfileobj(chunk, out)

    # Create the Video row — status 'queued', the row IS the queue
    # entry (identical to the legacy upload path).
    from app.models import Section, Course

    video = Video(
        id=video_id,
        title=Path(session.original_filename).stem or "upload",
        filename=session.original_filename,
        file_path=str(final_path),
        file_size=assembled_size,
        section_id=session.section_id,
        status="queued",
        whisper_model=session.whisper_model or get_default_model_choice(),
        language=session.language,
    )
    # Register the transcribe job FIRST (the 9/19 registration lesson)
    # so the queue's dispatcher can't silently no-op.
    from app.jobs import serialize_job, start_job

    job = start_job(
        video_id,
        "transcribe",
        total=100,
        message="Queued for auto-processing (transcribe → generate)...",
    )
    video.last_transcribe_job = serialize_job(job)
    db.add(video)

    session.status = STATUS_COMPLETED
    session.completed_at = _now()
    db.commit()

    # Staging consumed — remove the dir (chunks already concatenated).
    shutil.rmtree(_session_dir(session.id), ignore_errors=True)

    logger.info(
        "upload session %s completed → video %s (%d bytes)",
        session.id[:8], video_id[:8], assembled_size,
    )
    return video


# ── delete (user cancel / instant release) ──────────────────────────────────


def delete_session(db: Session, session: UploadSession) -> None:
    """The instant-release valve (registry §2): staging dir gone,
    row 'cancelled' — quota space back immediately."""
    shutil.rmtree(_session_dir(session.id), ignore_errors=True)
    if session.status != STATUS_ACTIVE:
        # Completed sessions keep their Video row (deleting a video
        # is a separate action with its own disk logic); the sweep
        # path calls this only on active sessions anyway.
        return
    session.status = STATUS_CANCELLED
    session.cancelled_at = _now()
    db.commit()
    logger.info("upload session %s cancelled by user", session.id[:8])


# ── sweeper support (commit C) ───────────────────────────────────────────────


# ── sweeper thread (13a commit C, 2026-09-21) ──────────────────────────────

_SWEEP_INTERVAL_SECONDS = 600  # pass every ~10 min (registry §3a)

_sweeper_started = False
_sweeper_lock = __import__("threading").Lock()


def start_upload_sweeper() -> None:
    """Start this process's sweeper thread (idempotent, per-process).

    The transcribe-queue pattern (app/services/transcribe_queue.py):
    one daemon thread per gunicorn worker, started from main.py's
    lifespan. Cross-process safety is the sweeper's atomic claim
    (sweep_expired_sessions commits the claim IMMEDIATELY — the
    c827a7b lesson by construction), so N workers running N sweepers
    is safe: the second claim of a row matches 0 rows.
    """
    global _sweeper_started
    import logging
    import threading

    with _sweeper_lock:
        if _sweeper_started:
            return
        _sweeper_started = True

    log = logging.getLogger(__name__)

    def _loop() -> None:
        import time

        while True:
            time.sleep(_SWEEP_INTERVAL_SECONDS)
            try:
                with __import__("app.database", fromlist=["SessionLocal"]).SessionLocal() as db:
                    sweep_expired_sessions(db)
            except Exception:
                # Never die — a sweeper crash must not take the worker
                # (the same contract as the queue's scheduler loop).
                log.exception("upload sweeper pass failed")

    threading.Thread(
        target=_loop, daemon=True, name="upload-sweeper"
    ).start()
    log.info(
        "upload sweeper started (ttl=%.1fh, interval=%ds)",
        float(settings.upload_session_ttl_hours),
        _SWEEP_INTERVAL_SECONDS,
    )


def sweep_expired_sessions(db: Session, *, ttl_hours: float | None = None) -> dict:
    """One sweeper pass (registry §3a, the ratified Round-2 design).

    Atomic claim → COMMIT IMMEDIATELY (the c827a7b lesson by
    construction) → idempotent staging deletion → row 'cancelled' →
    events row. Returns {claimed, bytes_freed} for the batch log.

    The claim is a single conditional UPDATE so the 4 per-process
    sweepers can never double-free; the commit makes it durable
    cross-process at claim time (not at pass end).
    """
    from sqlalchemy import text

    ttl = ttl_hours if ttl_hours is not None else settings.upload_session_ttl_hours
    cutoff = _now().timestamp() - (ttl * 3600)
    # Compare as unix time in SQL to avoid naive/aware pitfalls —
    # last_activity_at is naive UTC; julianday arithmetic keeps it
    # all inside SQLite (the 2026-09-13 plugin-sweep lesson).
    sql = text(
        """
        UPDATE upload_sessions
        SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP
        WHERE status = 'active'
          AND (julianday('now') - julianday(last_activity_at)) * 24.0 >= :ttl
        RETURNING id, declared_size
        """
    )
    rows = db.execute(sql, {"ttl": ttl}).all()
    db.commit()  # THE claim commit — immediately durable

    bytes_freed = 0
    for row in rows:
        shutil.rmtree(_session_dir(row[0]), ignore_errors=True)  # idempotent
        bytes_freed += int(row[1])

    if rows:
        from app.utils.events import log_event

        log_event(
            db,
            level="INFO",
            source="services.upload_sweeper",
            message=f"swept {len(rows)} abandoned upload session(s)",
            context={
                "count": len(rows),
                "bytes_freed": bytes_freed,
                "ttl_hours": ttl,
                "session_ids": [r[0] for r in rows],
            },
        )
        db.commit()
        logger.info(
            "upload sweeper: %d session(s) swept, %d bytes freed",
            len(rows), bytes_freed,
        )
    return {"claimed": len(rows), "bytes_freed": bytes_freed}