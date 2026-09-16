"""Transcription mini-queue — DB-backed scheduler with a 2-slot cap.

2026-09-16, launch hardening #4 (audit decisions #7/#10/#12).

WHAT THIS REPLACES: uploads dispatched their transcribe→generate
pipeline as an in-memory FastAPI BackgroundTask. Two fatal shapes
resulted:
  1. A server restart evaporated the task while the row said
     'queued' → the stuck-video saga (audit P0.2).
  2. N uploads = N simultaneous Whisper pipelines (the 9/12 outage:
     8 staggered transcribes deadlocked every worker thread; the
     site hung for 4 hours).

DESIGN (all ratified in the audit decision log):
  - The videos TABLE is the queue: status='queued' rows ARE the
    backlog. A scheduler loop claims them — no new table, restart-
    safe by construction (form-A orphans become self-recovering).
  - TWO SLOTS globally (decision #12: memory is not the constraint
    post-cache-lock; GPU bandwidth saturation + in-process stability
    are; 2 is the ratified ceiling — do not raise without a live
    throughput test).
  - PER-USER FAIRNESS, two-pass scan (decision #10):
      Pass 1: claim only videos whose owner has ZERO in-flight
              transcriptions → every user's FIRST video starts fast.
      Pass 2: backfill FIFO for anyone when slots idle (a sole
              uploader gets both slots).
    'In flight' = status='transcribing' (generation runs in the
    cloud and does not hold a slot; completed videos don't block).
  - ATOMIC CLAIM (multi-worker safe): gunicorn runs 4 processes,
    each with its own scheduler thread — a claim is a single
    conditional UPDATE ('claimed' flag flip) so two workers can
    never claim the same row.
  - STAGGER: claimed videos start 5s apart so two slots never load
    Whisper in the same instant.

STATUS CONTRACT: a claimed row flips to 'transcribing' INSIDE the
claim transaction — if the claim survives, the work will start; if
the process dies before starting, the row is a form-B orphan that
the existing retry-stuck button already recovers (job JSON age >30
min). Nothing else changes: _staggered_transcribe_job still runs the
full transcribe→generate chain (the 9/13 fix).

INTEGRATION: uploads (single + bulk) now ONLY write the row — the
scheduler picks it up within POLL_SECONDS. All other dispatch sites
(retry-failed / retry-stuck / admin requeue) still dispatch
immediately: they are explicit user actions for small counted sets,
already staggered, and routing them through the queue would change
their tested semantics mid-launch-window.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone as _tz
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Ratified constants (audit decisions #10/#12) ─────────────────────────

SLOTS = 2            # global concurrent transcriptions (do not raise
                     # without a live 1/2/3-concurrency throughput test)
POLL_SECONDS = 3.0   # scheduler wake-up cadence
STAGGER_SECONDS = 5  # min spacing between two slot acquisitions

# The in-memory state below is per-PROCESS (4 gunicorn workers each
# run one scheduler thread; the DB row-claim arbitrates between them).
_state_lock = threading.Lock()
_scheduler_thread: threading.Thread | None = None
_scheduler_started = False
_last_claim_monotonic = 0.0
# owner_uid -> monotonic time the last slot was claimed for that user
# (per-process; the DB is the cross-process truth — this only staggers
# claims made by THIS process within one pass)
_recent_claims: dict[str, float] = {}


def _now_utc() -> datetime:
    return datetime.now(_tz.utc).replace(tzinfo=None)


def _claim_one(db: Session, *, pass_two: bool) -> str | None:
    """Atomically claim the next eligible queued video.

    Returns the video_id or None. The claim is ONE conditional UPDATE
    so two gunicorn workers racing this row can never both win:
    SQLite serializes the write, the second UPDATE matches 0 rows.

    Pass 1: only videos whose owner has nothing in 'transcribing'
    Pass 2: any queued video (FIFO by created_at) — backfill.
    """
    owner_gate = "" if pass_two else (
        "AND NOT EXISTS ("
        "  SELECT 1 FROM videos v2"
        "  JOIN sections s2 ON v2.section_id = s2.id"
        "  JOIN courses c2 ON s2.course_id = c2.id"
        "  WHERE c2.user_id = c.user_id"
        "    AND v2.status = 'transcribing'"
        ")"
    )
    sql = text(
        f"""
        UPDATE videos
        SET status = 'transcribing'
        WHERE id = (
            SELECT v.id FROM videos v
            JOIN sections s ON v.section_id = s.id
            JOIN courses c ON s.course_id = c.id
            WHERE v.status = 'queued'
              AND v.youtube_id IS NULL      -- file uploads only;
                                             -- YouTube rows use the
                                             -- caption pipeline
              {owner_gate}
            ORDER BY v.created_at ASC, v.id ASC
            LIMIT 1
        ) AND status = 'queued'
        RETURNING id
        """
    )
    row = db.execute(sql).first()
    return row[0] if row else None


def _owner_of(db: Session, video_id: str) -> str:
    """Resolve the owner uid for a claimed video (section→course)."""
    row = db.execute(
        text(
            "SELECT c.user_id FROM videos v"
            " JOIN sections s ON v.section_id = s.id"
            " JOIN courses c ON s.course_id = c.id"
            " WHERE v.id = :vid"
        ),
        {"vid": video_id},
    ).first()
    return row[0] if row else ""


def _free_slots(db: Session) -> int:
    """Slots available = SLOTS - in-flight transcriptions. Cross-process
    truth: the DB row status IS the slot ledger (claims flip the row
    to 'transcribing' before returning, releases happen when the
    pipeline finishes — status moves on to 'generating'/'ready')."""
    in_flight = db.execute(
        text("SELECT COUNT(*) FROM videos WHERE status = 'transcribing'")
    ).scalar()
    return max(0, SLOTS - int(in_flight or 0))


def _in_flight_for_user(db: Session, owner_uid: str) -> int:
    """Per-user in-flight count for the per-process stagger and the
    two-pass gate (the DB claim enforces pass-1; this mirrors it for
    logging/telemetry)."""
    if not owner_uid:
        return 0
    n = db.execute(
        text(
            "SELECT COUNT(*) FROM videos v"
            " JOIN sections s ON v.section_id = s.id"
            " JOIN courses c ON s.course_id = c.id"
            " WHERE c.user_id = :uid AND v.status = 'transcribing'"
        ),
        {"uid": owner_uid},
    ).scalar()
    return int(n or 0)


def _scheduler_pass(db: Session) -> int:
    """One scheduling pass: fill free slots using the two-pass scan.
    Returns the number of videos claimed this pass.

    Exception-proof by contract: the loop calls this every tick on
    WHATEVER the SessionLocal currently points at (under test suites
    that's a fresh per-test engine — a pass may see no tables at
    all). Errors are logged and returned as 0 claims; a scheduling
    error must never crash the loop OR leak into request handling.
    """
    global _last_claim_monotonic

    try:
        return _scheduler_pass_inner(db)
    except Exception:
        logger.exception("mini-queue scheduler pass failed")
        return 0


def _scheduler_pass_inner(db: Session) -> int:
    """The real pass body (see _scheduler_pass for the guard)."""
    global _last_claim_monotonic

    claimed = 0
    while True:
        free = _free_slots(db)
        if free <= 0:
            break

        # Honor the global stagger: never claim twice within
        # STAGGER_SECONDS of this process's last claim (the two slots
        # loading Whisper in the same instant was 9/12's resource
        # spike — 5s is the ratified spacing, same as every requeue
        # path already uses).
        since = time.monotonic() - _last_claim_monotonic
        if _last_claim_monotonic and since < STAGGER_SECONDS:
            break

        # Pass 1: fairness (owner must have zero in-flight).
        video_id = _claim_one(db, pass_two=False)
        pass_two_now = False
        if not video_id:
            # Pass 2: backfill FIFO for anyone.
            video_id = _claim_one(db, pass_two=True)
            pass_two_now = True
        if not video_id:
            break  # queue empty

        _last_claim_monotonic = time.monotonic()
        claimed += 1

        # Dispatch the full chain (same worker as the existing retry
        # paths — the pipeline itself is unchanged and battle-tested
        # since 3843fcf; only WHO starts it and WHEN changed).
        from app.routers.courses import _staggered_transcribe_job

        threading.Thread(
            target=_staggered_transcribe_job,
            args=(video_id, 0),
            daemon=True,
            name=f"transcribe-queue-{video_id[:8]}",
        ).start()

        owner = _owner_of(db, video_id)
        logger.info(
            "mini-queue claimed %s (pass %s, owner=%s, free=%d)",
            video_id, 2 if pass_two_now else 1, owner or "?", free - 1,
        )

        # After each claim the loop re-reads free slots from the DB —
        # another worker may have claimed in between; correctness
        # never trusts this process's private view.
    return claimed


def _scheduler_loop() -> None:
    """The per-process scheduler thread: wake every POLL_SECONDS and
    run a pass. Runs in EVERY gunicorn worker (4 total) — the atomic
    DB claim makes that safe (workers can only steal rows the others
    haven't claimed; there is no double-dispatch)."""
    from app.database import SessionLocal

    while True:
        try:
            with SessionLocal() as db:
                _scheduler_pass(db)
        except Exception:
            # Never let the loop die: a scheduling error must not take
            # the request-serving worker with it. Log + next tick.
            logger.exception("mini-queue scheduler pass failed")
        time.sleep(POLL_SECONDS)


def start_scheduler() -> None:
    """Start this process's scheduler thread (idempotent). Called at
    app startup; also safe to call again (e.g. tests)."""
    global _scheduler_thread, _scheduler_started
    with _state_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
        _scheduler_thread = threading.Thread(
            target=_scheduler_loop,
            daemon=True,
            name="transcribe-scheduler",
        )
        _scheduler_thread.start()
        logger.info("mini-queue scheduler started (slots=%d)", SLOTS)


def queue_position(db: Session, video_id: str) -> dict[str, Any]:
    """Where is my video? → {position, ahead, eta_seconds}.

    Position is the FIFO rank among queued file-upload videos. The
    two-pass fairness means actual start order can differ (a pass-1
    eligible video behind you may jump ahead) — position is an honest
    ESTIMATE, and the ETA (decision #10) uses the measured average
    transcribe duration from the videos table itself (recent 30
    completed rows) × ahead ÷ SLOTS.
    """
    row = db.execute(
        text(
            "SELECT COUNT(*) FROM videos a, videos b"
            " JOIN sections sb ON b.section_id = sb.id"
            " WHERE a.id = :vid AND b.status = 'queued'"
            "   AND b.youtube_id IS NULL"
            "   AND (b.created_at, b.id) < (a.created_at, a.id)"
        ),
        {"vid": video_id},
    ).scalar()
    ahead = int(row or 0)

    avg = db.execute(
        text(
            "SELECT AVG("
            "  (strftime('%s', transcribed_at) - "
            "   strftime('%s', transcribe_started_at)))"
            " FROM videos WHERE status IN ('ready', 'generating')"
            "   AND transcribe_started_at IS NOT NULL"
            "   AND transcribed_at IS NOT NULL"
            "   AND transcribed_at > datetime('now', '-30 days')"
        )
    ).scalar()
    avg_seconds = float(avg) if avg else 90.0  # fallback: measured ~60-90s

    eta = round(ahead * avg_seconds / SLOTS)
    return {
        "position": ahead + 1,
        "ahead": ahead,
        "eta_seconds": eta,
        "queue_depth": ahead + 1,
    }


def queue_depth(db: Session) -> dict[str, Any]:
    """Cockpit gauge: how loaded is the queue right now?"""
    waiting = int(db.execute(
        text(
            "SELECT COUNT(*) FROM videos"
            " WHERE status = 'queued' AND youtube_id IS NULL"
        )
    ).scalar() or 0)
    running = int(db.execute(
        text("SELECT COUNT(*) FROM videos WHERE status = 'transcribing'")
    ).scalar() or 0)
    return {
        "waiting": waiting,
        "running": running,
        "slots": SLOTS,
        "free": max(0, SLOTS - running),
    }