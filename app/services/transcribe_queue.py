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
_recent_claims: dict[str, float] = {}# 2026-09-20 (duplicate-dispatch guard): video_ids this process has EVER
# dispatched for. If a claim bug ever regresses (rollback reverts the
# row to 'queued', another pass re-claims it), this set makes the
# second dispatch impossible WITHIN a process — one video, one thread.
# Cross-process safety remains the DB claim's job (now with the commit
# fix); this is the per-process belt-and-braces for claim regressions.
_dispatched_videos: set[str] = set()

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
    if row is None:
        return None

    # 2026-09-20 CRITICAL FIX (the duplicate-dispatch storm): the claim's
    # UPDATE must be COMMITTED, not just flushed. This session is opened
    # via `with SessionLocal() as db:` which ROLLS BACK on close — so
    # the status flip silently reverted to 'queued' after every pass,
    # and each of the 4 workers' schedulers re-claimed the SAME row every
    # 3 seconds. Each claim spawned another _staggered_transcribe_job
    # thread that never observed "already claimed": the live incident
    # had ~60 concurrent transcribe threads (15/worker) for ONE video,
    # each holding a pool connection through multi-minute Whisper work
    # — 60 checked-out connections vs a 30-connection QueuePool took
    # the whole site dark (QueuePoolTimeoutError on every request).
    # The commit makes the claim durable across processes immediately.
    db.commit()
    return row[0]


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

        # 2026-09-20 duplicate-dispatch guard: refuse to dispatch the
        # same video twice from THIS process (see _dispatched_videos).
        # If the DB claim ever regresses, the duplicate thread is
        # rejected here instead of stacking another Whisper pipeline.
        if video_id in _dispatched_videos:
            logger.error(
                "mini-queue: duplicate claim of %s detected — refusing "
                "second dispatch (claim rollback regression?)",
                video_id,
            )
            # Leave the row as-is: if the first dispatch is genuinely
            # running, it owns the work; if it died, requeue-stuck /
            # recover-orphans handle it as designed. Not counted as a
            # claim — no dispatch happened, no thread was started.
            break
        _dispatched_videos.add(video_id)
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


# ── Upload caps (audit decision #9, 2026-09-16 ratified) ──────────────────
#
# Two gates, deliberately complementary:
#   DAILY   15 new uploads/day/user   — bounds the ADMISSION RATE
#            (quota semantics: counts rows created today; public
#            queue delays do NOT eat it — cross-day no-rollover)
#   IN-FLIGHT  6 unfinished videos     — bounds the STOCKPILE (the
#            anti-abuse anchor: someone maxing 15/day for days can't
#            keep a permanent mountain of pending work on the box)
#
# Both checked BEFORE accepting an upload; the response's 429 detail
# carries the specific cap so the UI can show an actionable message.

UPLOAD_DAILY_LIMIT = 15
UPLOAD_IN_FLIGHT_LIMIT = 6

# Parameterized join prefix (owner uid bound via :uid — NEVER string-
# interpolated; uid comes from the verified session token but the
# convention here is parameterized everywhere).
_OWNED_JOIN = (
    "SELECT COUNT(*) FROM videos v"
    " JOIN sections s ON v.section_id = s.id"
    " JOIN courses c ON s.course_id = c.id"
    " WHERE c.user_id = :uid"
)


def upload_caps_check(db: Session, owner_uid: str) -> dict[str, Any]:
    """Check the two upload caps for a user. Returns a dict:
      {"allowed": True} or {"allowed": False, "reason": str,
                            "cap": "daily"|"in_flight"}

    The daily cap counts rows created since local-midnight UTC
    (decision #9: created_at semantics, no cross-day rollover — a
    queue delay must not eat the user's quota). The in-flight cap
    counts unfinished work of ANY age.
    """
    if not owner_uid:
        return {"allowed": True}

    # Today's upload count (created_at >= today 00:00 UTC — matches
    # events.ts naive-UTC convention).
    today_start = _now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = int(db.execute(
        text(f"{_OWNED_JOIN} AND v.created_at >= :today"),
        {"uid": owner_uid, "today": today_start},
    ).scalar() or 0)
    if today_count >= UPLOAD_DAILY_LIMIT:
        return {
            "allowed": False,
            "cap": "daily",
            "reason": (
                f"Daily upload limit reached ({UPLOAD_DAILY_LIMIT} videos). "
                "It resets at midnight UTC — your videos in progress are "
                "unaffected."
            ),
            "used_today": today_count,
            "limit": UPLOAD_DAILY_LIMIT,
        }

    # Unfinished stockpile (queued/transcribing/generating all count —
    # decision #9's 6-in-flight anchor).
    in_flight = int(db.execute(
        text(f"{_OWNED_JOIN} AND v.status IN ('queued','transcribing','generating')"),
        {"uid": owner_uid},
    ).scalar() or 0)
    if in_flight >= UPLOAD_IN_FLIGHT_LIMIT:
        return {
            "allowed": False,
            "cap": "in_flight",
            "reason": (
                f"You have {in_flight} videos still processing. Wait for "
                f"some to finish (or delete queued ones you no longer want) "
                f"before uploading more (limit: {UPLOAD_IN_FLIGHT_LIMIT} at once)."
            ),
            "in_flight": in_flight,
            "limit": UPLOAD_IN_FLIGHT_LIMIT,
        }

    return {
        "allowed": True,
        "used_today": today_count,
        "limit": UPLOAD_DAILY_LIMIT,
        "in_flight": in_flight,
    }


def congestion_notice(db: Session) -> dict[str, Any] | None:
    """The soft-guidance tier (decision #9): based on PUBLIC queue
    congestion, never on the user's own usage. Returns None when the
    queue is quiet (<30min expected wait), else a notice the UI shows
    next to the upload button:

      ~30min-2h: 'today is busy — upload the ones you want first'
    Beta deliberately excludes the >2h red tier (decision #9).

    The wait estimate: queue_depth().waiting × measured-average ÷
    slots — the same honest arithmetic as the per-video ETA.
    """
    depth = queue_depth(db)
    if depth["waiting"] <= 0:
        return None
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
    avg_seconds = float(avg) if avg else 90.0
    wait_seconds = depth["waiting"] * avg_seconds / SLOTS
    if wait_seconds < 30 * 60:
        return None  # quiet — no notice
    return {
        "level": "busy",
        "wait_minutes": round(wait_seconds / 60),
        "message": (
            "⏳ 今天处理压力较大，你的视频预计 ~X 小时后完成。"
            "可以先传，也可以挑最想看的先传。"
        ).replace("X 小时", f"{round(wait_seconds/3600, 1)} 小时"),
    }