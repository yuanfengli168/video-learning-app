"""Re-run guards for transcribe/generate (2026-09-21, abuse prevention).

The problem (owner report + live audit): the transcribe/generate
endpoints had NO in-flight guard — a user (or a script) clicking
"Transcribe" on a video that's already queued/transcribing stacked
ANOTHER pipeline on it. 10 clicks = 10 concurrent Whisper runs
contending for the 2 queue slots: a cheap self-DoS degrading every
user's processing (the user-triggerable twin of the 9/20 duplicate-
dispatch storm).

Three ratified layers (doc trail: the 2026-09-21 session):
  A. Server-side idempotence: 409 "Already <status>" while
     video.status is in-flight — with the STALENESS ESCAPE: rows
     whose job record proves >30 min of no progress are allowed
     (they're the orphan class; _is_stuck_video's taxonomy — the
     stuck-row escape hatch so a dead pipeline can be retried).
  C. Manual re-run cap: max 3/video/day (events-table counted,
     worker-independent — the same trust basis as the usage page).

Layer B (UI disabled states + live progress) lives in video.html.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as _tz

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

# Layer C: manual re-runs per video per UTC day (ratified 2026-09-21).
# Generous for humans (3 retries is plenty), fatal for scripts.
MAX_MANUAL_RERUNS_PER_DAY = 3

# The in-flight statuses where a re-run request must 409 (Layer A).
IN_FLIGHT_STATUSES = {"queued", "transcribing", "generating"}


def _job_stale_minutes(video) -> int | None:
    """How stale the current job record is, in minutes — None if the
    video has no job record (can't prove staleness → not stale)."""
    import json as _json

    job_json = video.last_transcribe_job
    if not job_json:
        return None
    try:
        started = _json.loads(job_json).get("started_at")
    except (ValueError, TypeError):
        return None
    if not started:
        return None
    now = datetime.now(_tz.utc).replace(tzinfo=None)
    started_dt = datetime.fromtimestamp(float(started), _tz.utc).replace(
        tzinfo=None
    )
    return int((now - started_dt).total_seconds() // 60)


def guard_rerun(db: Session, video, *, action: str) -> None:
    """Raise 409/429 if a manual re-run of `action` must be blocked.

    Called by BOTH the transcribe and generate endpoints after their
    ownership checks — the single enforcement point. Raises nothing
    when the run is allowed.

    Layer A — in-flight idempotence with the staleness escape:
      409 "Already <status> — processing continues" when the video is
      in-flight AND its job record is fresh (<30 min). A row whose job
      record is >30 min stale (the orphan class — restart ate the
      worker; see the 9/19 + 9/20 incidents) ESCAPES the guard: the
      re-run is the recovery, exactly like the retry-stuck button.

    Layer C — the abuse cap:
      429 "Manual limit reached (3/day)" when this video already saw
      3 manual re-runs (transcribe or generate, combined) in the
      current UTC day — events-table counted (every worker writes
      there), midnight-UTC reset (the same convention as the usage
      page's daily claim).

    action: "transcribe" | "generate" — only used for the 409 wording
    (transcribe is blocked during generating; generate during
    transcribing is the normal chain).
    """
    # ── Layer A: in-flight guard with the staleness escape ──────────
    if video.status in IN_FLIGHT_STATUSES:
        stale = _job_stale_minutes(video)
        if stale is not None and stale < 30:
            # Fresh job record → genuinely in flight; 409. (A MISSING
            # job record escapes: nothing provably running — legacy
            # rows + test fixtures create status-in-flight with no job
            # JSON, and the honest read is 'can't prove it's working'.
            # The >30-min-stale case escapes the same way — the orphan
            # class needs the re-run as its recovery path.)
            # Special case: generate during transcribing is the normal
            # auto-chain shape — but a MANUAL generate click while
            # transcribing would desync the chain; block it too.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Already {video.status} — your video is still "
                    "processing. Watch the progress on this page; "
                    "no need to start again."
                ),
            )

    # ── Layer C: the manual re-run cap ───────────────────────────────
    # NOTE: counted via the RERUN ACCEPTED audit rows below — NOT the
    # frontend's `ui action <x>` telemetry beacons (those fire on every
    # click including rejected ones, so counting them would both double-
    # count and let rejected spam consume the cap). The distinct
    # 'manual rerun accepted' message is the truth: one row per re-run
    # the guard actually allowed.
    day_start = datetime.now(_tz.utc).replace(
        tzinfo=None, hour=0, minute=0, second=0, microsecond=0
    )
    reruns_today = db.execute(
        text(
            "SELECT COUNT(*) FROM events "
            "WHERE video_id = :vid "
            "  AND source = 'services.rerun_guards' "
            "  AND message = 'manual rerun accepted' "
            "  AND ts >= :day_start"
        ),
        {"vid": video.id, "day_start": day_start},
    ).scalar() or 0
    if reruns_today >= MAX_MANUAL_RERUNS_PER_DAY:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Manual limit reached ({MAX_MANUAL_RERUNS_PER_DAY} "
                "re-runs per video per day). This protects processing "
                "for everyone. Resets at midnight UTC."
            ),
        )

    # Audit row (also feeds Layer C's count on the NEXT call).
    from app.utils.events import log_event

    log_event(
        db,
        level="INFO",
        source="services.rerun_guards",
        message="manual rerun accepted",
        video_id=video.id,
        context={"action": action, "status_before": video.status},
    )
    db.commit()