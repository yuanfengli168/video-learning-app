"""Helpers for re-running failed background jobs.

The auto-pipeline (`upload → transcribe → generate`) runs as FastAPI
BackgroundTasks. Sometimes the LLM step fails (e.g. the `glm-5.2:cloud`
model returns an empty body, or the LLM server times out). The user
needs a way to retry those failed generations without re-uploading the
video or re-running the (slow, expensive) Whisper step.

This module provides the pure query helper that the retry script and
the future "Retry" buttons (#5, #6 in Todo.md) all share.

Public API
----------
- `find_failed_generate_videos(db)` — list of (video_id, title, error)
  tuples for every video whose last_generate_job is in the "failed"
  state.
- `find_failed_transcribe_videos(db)` — same, for transcribe.

Both functions are pure (no side effects, no Ollama calls, no Whisper)
so they're cheap to call and easy to unit test. The actual re-run
happens elsewhere (in `scripts/retry_failed_generate.py` for the
one-shot, and in the new `/retry-failed` endpoints for the UI buttons).

Why a separate module and not a method on the Video model?
The Video model is the data shape. Retry logic is a workflow concern,
not a data concern. Keeping them separate also lets the scripts
directory import only what it needs without dragging SQLAlchemy into
a CLI script that just wants a list of IDs.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.video import Video


def find_failed_generate_videos(db: Session) -> list[dict[str, Any]]:
    """Return all videos whose last_generate_job ended in 'failed' state.

    Returns a list of dicts (one per video) with keys:
        - video_id: str — the video's UUID
        - title: str — the video's title
        - error: str | None — the error message from the failed job
        - failed_at: float | None — epoch seconds when the job failed

    Sorted by title for stable, alphabetical output (so retries happen
    in a predictable order across runs).

    The query uses SQLite's json_extract to read the JSON-serialized
    last_generate_job column. Other databases (PostgreSQL in MVP3) will
    need json_extract or the equivalent JSON operator. For SQLite this
    is the standard pattern.
    """
    stmt = (
        select(Video.id, Video.title, Video.last_generate_job)
        .where(Video.last_generate_job.is_not(None))
        .order_by(Video.title)
    )
    results: list[dict[str, Any]] = []
    for row in db.execute(stmt):
        job = _safe_parse(row.last_generate_job)
        if not job:
            continue
        if job.get("status") != "failed":
            continue
        results.append({
            "video_id": row.id,
            "title": row.title,
            "error": job.get("error"),
            "failed_at": job.get("completed_at"),
        })
    return results


def find_failed_transcribe_videos(db: Session) -> list[dict[str, Any]]:
    """Same as find_failed_generate_videos, but for transcribe jobs.

    Kept separate because the two failure modes are usually different
    (Whisper crashes vs LLM empty body) and may want different retry
    strategies in the future.
    """
    stmt = (
        select(Video.id, Video.title, Video.last_transcribe_job)
        .where(Video.last_transcribe_job.is_not(None))
        .order_by(Video.title)
    )
    results: list[dict[str, Any]] = []
    for row in db.execute(stmt):
        job = _safe_parse(row.last_transcribe_job)
        if not job:
            continue
        if job.get("status") != "failed":
            continue
        results.append({
            "video_id": row.id,
            "title": row.title,
            "error": job.get("error"),
            "failed_at": job.get("completed_at"),
        })
    return results


def _safe_parse(json_str: str | None) -> dict[str, Any] | None:
    """Parse a JSON job string, returning None on any error.

    Tolerant of: missing input, None, empty string, malformed JSON.
    The retry helpers should never raise on a single bad row — they
    are read-only queries meant to be safe to run interactively.
    """
    if not json_str:
        return None
    try:
        result = json.loads(json_str)
        return result if isinstance(result, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None
