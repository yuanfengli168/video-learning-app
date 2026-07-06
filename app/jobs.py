"""In-memory job tracker for long-running background tasks (MVP1).

Why this exists
---------------
Transcribing a 1-hour video with Whisper 'medium' takes 5-15 minutes.
Generating learning materials with Ollama takes 30-60 seconds. We
don't want the HTTP request to block that long, so we kick the work
off as a FastAPI BackgroundTask and let the UI poll for progress.

Design constraints
------------------
- Single-user, single-process MVP1. The job state lives in a process-
  level dict (module singleton below). If you kill the server, in-
  flight jobs are lost (acceptable for MVP1; for MVP2 we'll move to
  Redis or RabbitMQ).
- The dict is keyed by (video_id, job_type) so two jobs for the
  same video don't collide (e.g. transcribe can run while generate
  is queued — although in practice you'd generate after transcribe
  finishes, the API allows both).
- A snapshot of every job is also persisted to the Video model
  (last_transcribe_job / last_generate_job) so the UI survives a
  page refresh.

Public API
----------
- `start_job(video_id, job_type, total_units=100)` — call once at the
  beginning of a background task. Returns a `Job` object the worker
  uses to call `set_progress` periodically.
- `set_progress(job, *, done, total=None, message=None)` — call
  inside the worker to update progress. Auto-derives pct + ETA.
- `finish_job(job, *, status="completed", error=None)` — call at
  the end. status is one of "completed" or "failed".
- `get_job(video_id, job_type)` — read by the /status endpoint and
  the UI poll. Returns None if no job exists.
- `serialize_job(job)` / `deserialize_job(json_str)` — for persisting
  to the Video model.

Job dict shape
--------------
{
    "video_id":   str,        # which video this job is for
    "job_type":   str,        # "transcribe" or "generate"
    "status":     str,        # "running" | "completed" | "failed"
    "progress":   int,        # 0..total
    "total":      int,        # total work units (e.g. total segments)
    "pct":        float,      # 0.0..100.0, derived
    "eta_seconds": float,     # estimated seconds remaining
    "message":    str,        # human-readable status
    "started_at": float,      # epoch seconds
    "completed_at": float|None,
    "error":      str|None,   # error message if status == "failed"
}
"""

from __future__ import annotations

import json
import math
import time
from typing import Any, Literal

JobType = Literal["transcribe", "generate"]
JobStatus = Literal["running", "completed", "failed"]


# ── Module-level singleton ───────────────────────────────────────────────────
# In a multi-process or multi-server MVP2 deployment, replace this with
# Redis (or RabbitMQ + Celery). The public API below stays the same.
_jobs: dict[tuple[str, str], dict[str, Any]] = {}


def _now() -> float:
    """Monotonic-ish wall clock for ETA calculation. We use time.time()
    (not monotonic) because the job dict is JSON-serialized and the
    client may need to interpret started_at/completed_at as wall time
    for a "started at 14:32" tooltip."""
    return time.time()


def start_job(
    video_id: str,
    job_type: JobType,
    *,
    total: int = 100,
    message: str | None = None,
) -> dict[str, Any]:
    """Begin tracking a new job. Returns the Job dict for the worker.

    If a job of the same (video_id, job_type) is already running, it
    is REPLACED — the new call wins. We do this (instead of refusing)
    because the only way to get here is via a fresh POST to
    /transcribe or /generate, and the user clearly wants the new one.
    """
    job: dict[str, Any] = {
        "video_id": video_id,
        "job_type": job_type,
        "status": "running",
        "progress": 0,
        "total": total,
        "pct": 0.0,
        "eta_seconds": None,
        "message": message or f"Starting {job_type}...",
        "started_at": _now(),
        "completed_at": None,
        "error": None,
    }
    _jobs[(video_id, job_type)] = job
    return job


def set_progress(
    job: dict[str, Any],
    *,
    done: int,
    total: int | None = None,
    message: str | None = None,
) -> None:
    """Update progress for an in-flight job.

    Call this periodically from the worker (e.g. after every Whisper
    segment, or every LLM call). Idempotent — safe to call multiple
    times with the same `done` value.
    """
    job["progress"] = max(0, int(done))
    if total is not None:
        job["total"] = max(1, int(total))
    # Derive percentage, clamped to [0, 100].
    job["pct"] = round(min(100.0, (job["progress"] / job["total"]) * 100.0), 1)

    # Compute ETA. If progress is 0 we can't predict; return None so
    # the UI shows "Estimating..." instead of a misleading number.
    elapsed = _now() - job["started_at"]
    if job["progress"] > 0 and elapsed > 0:
        rate = job["progress"] / elapsed  # units per second
        remaining = job["total"] - job["progress"]
        job["eta_seconds"] = math.ceil(remaining / rate) if rate > 0 else None
    else:
        job["eta_seconds"] = None

    if message is not None:
        job["message"] = message


def finish_job(
    job: dict[str, Any],
    *,
    status: JobStatus = "completed",
    error: str | None = None,
    message: str | None = None,
) -> None:
    """Mark the job as completed or failed.

    Sets progress to total and pct to 100 (even on failure — the work
    is done, just unsuccessfully). eta_seconds is set to 0.
    """
    job["status"] = status
    job["progress"] = job["total"]
    job["pct"] = 100.0
    job["eta_seconds"] = 0
    job["completed_at"] = _now()
    job["error"] = error
    if message is not None:
        job["message"] = message
    elif status == "completed" and not job.get("message", "").startswith("✓"):
        job["message"] = f"✓ {job['job_type'].title()} completed in {_format_duration(_now() - job['started_at'])}"


def get_job(video_id: str, job_type: JobType) -> dict[str, Any] | None:
    """Read the current job state. Returns None if no job exists."""
    return _jobs.get((video_id, job_type))


# ── Serialization (for persisting to the Video model) ───────────────────────

def serialize_job(job: dict[str, Any]) -> str:
    """JSON-encode a job dict for storage in Video.last_transcribe_job etc."""
    return json.dumps(job, ensure_ascii=False)


def deserialize_job(json_str: str | None) -> dict[str, Any] | None:
    """Reverse of serialize_job. Returns None on invalid input."""
    if not json_str:
        return None
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return None


# ── Helpers ─────────────────────────────────────────────────────────────────

def _format_duration(seconds: float) -> str:
    """Format seconds as a human-friendly '1m 23s' string for the UI."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def format_eta(eta_seconds: float | None) -> str:
    """Format an ETA (in seconds) for display in the UI.

    Examples:
        None         → "Estimating..."
        0            → "Almost done"
        45           → "About 45 seconds remaining"
        125          → "About 2 minutes remaining"
    """
    if eta_seconds is None:
        return "Estimating..."
    if eta_seconds <= 0:
        return "Almost done"
    if eta_seconds < 60:
        return f"About {int(eta_seconds)} seconds remaining"
    if eta_seconds < 3600:
        m = int(eta_seconds // 60)
        s = int(eta_seconds % 60)
        if s == 0:
            return f"About {m} minute{'s' if m != 1 else ''} remaining"
        return f"About {m}m {s}s remaining"
    h = int(eta_seconds // 3600)
    m = int((eta_seconds % 3600) // 60)
    return f"About {h}h {m}m remaining"


# ── Test hooks ─────────────────────────────────────────────────────────────

def _reset_for_tests() -> None:
    """Clear all in-memory jobs. For tests only — NEVER call from app code."""
    _jobs.clear()
