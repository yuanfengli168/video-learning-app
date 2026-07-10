#!/usr/bin/env python3
"""Retry all failed generate jobs (one-shot script).

Why this exists
---------------
On 2026-07-09, ~12 of 30 bulk-uploaded videos failed in the LLM step
with "Could not extract valid JSON from LLM response (len=0)" — the
`glm-5.2:cloud` model returned an empty body. The transcripts are
intact in the DB; only the materials generation step needs to be
re-run. This script does exactly that, in bulk, without re-uploading
or re-transcribing.

The same retry logic will be exposed via UI buttons in the future
(#5 and #6 in Todo.md). This CLI version is the escape hatch for
when you have a lot of failed videos and don't want to click N times.

Usage
-----
    # Dry-run: just list what would be retried
    python scripts/retry_failed_generate.py --dry-run

    # Actually retry them all
    python scripts/retry_failed_generate.py

    # Retry only one video
    python scripts/retry_failed_generate.py --video-id <uuid>

    # Retry transcribe failures instead (Whisper failed, not Ollama)
    python scripts/retry_failed_generate.py --job-type transcribe

Exit code is 0 on success, 1 if any video failed to retry.

The script reuses the same `_run_generate_job` from the running
FastAPI app (imported, not duplicated) so the retry path is
identical to the original generation path — no risk of drift.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

# Make the app package importable when this script is run directly
# (`python scripts/retry_failed_generate.py` from the project root).
# The PYTHONPATH is one option; the explicit sys.path manipulation is
# more reliable across shells and IDEs.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.database import SessionLocal
from app.models.video import Video
from app.services.retry import (
    find_failed_generate_videos,
    find_failed_transcribe_videos,
)


def _red(msg: str) -> str:
    return f"\033[31m{msg}\033[0m"


def _green(msg: str) -> str:
    return f"\033[32m{msg}\033[0m"


def _yellow(msg: str) -> str:
    return f"\033[33m{msg}\033[0m"


def _print_failed_table(rows: list[dict[str, Any]], job_type: str) -> None:
    """Print a one-line-per-video table of what's about to be retried."""
    print(_yellow(f"\n{job_type} jobs to retry ({len(rows)}):"))
    print(f"  {'video_id':<38}  {'error':<60}  title")
    print("  " + "-" * 130)
    for r in rows:
        err = (r["error"] or "")[:60]
        print(f"  {r['video_id']:<38}  {err:<60}  {r['title'][:40]}")
    print()


def _retry_one(video_id: str, job_type: str) -> tuple[bool, str | None]:
    """Re-run the LLM step for one video. Returns (success, error_msg).

    Imports the worker function lazily so the script can be run
    without starting the FastAPI app (no need for the whole app
    context just to retry a generation).

    Reuses the same function that the auto-pipeline uses, so the
    retry path is byte-identical to the original path.

    Important: the worker checks `get_job(video_id, job_type)` and
    returns early if no job exists. We must call `start_job` first
    to register the job in the in-memory tracker.
    """
    if job_type == "generate":
        from app.jobs import start_job, finish_job, serialize_job
        from app.routers.generation import _run_generate_job
        worker = _run_generate_job
    elif job_type == "transcribe":
        from app.jobs import start_job, finish_job, serialize_job
        from app.routers.videos import _run_transcribe_job
        worker = _run_transcribe_job
    else:
        return False, f"unknown job_type: {job_type}"

    # Register a job in the in-memory tracker so the worker doesn't
    # bail out at the first `if not job: return` check. The worker
    # updates the same job in-place as it progresses, and persists
    # snapshots of it to the Video row.
    job = start_job(video_id, job_type, message=f"Retrying {job_type} (script)...")
    # Also persist the initial job state to the Video row so the UI
    # can see "retrying" status instead of "failed" while we run.
    db = SessionLocal()
    try:
        video = db.get(Video, video_id)
        if video:
            video.last_generate_job = serialize_job(job)
            if job_type == "transcribe":
                video.last_transcribe_job = serialize_job(job)
            video.status = job_type + "ing"  # "generating" or "transcribing"
            db.commit()
    finally:
        db.close()

    try:
        worker(video_id)
        # After the worker returns, check the job state. The worker
        # updates job.status to "completed" or "failed" before exiting.
        # We don't import `get_job` from app.jobs above because we
        # only want a fresh read after the worker is done.
        from app.jobs import get_job
        final_job = get_job(video_id, job_type)
        if final_job and final_job.get("status") == "failed":
            return False, final_job.get("error", "unknown")
        return True, None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-run failed background jobs (generate or transcribe).",
    )
    parser.add_argument(
        "--job-type",
        choices=["generate", "transcribe"],
        default="generate",
        help="Which job type to retry (default: generate)",
    )
    parser.add_argument(
        "--video-id",
        default=None,
        help="Retry only this one video (default: all failed videos)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be retried, don't actually call Ollama/Whisper",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of videos to retry (default: all)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Step 1: find the failed videos
        if args.job_type == "generate":
            rows = find_failed_generate_videos(db)
        else:
            rows = find_failed_transcribe_videos(db)

        # Filter to a single video if requested
        if args.video_id:
            rows = [r for r in rows if r["video_id"] == args.video_id]
            if not rows:
                print(
                    _red(
                        f"error: video_id {args.video_id} not found in "
                        f"failed {args.job_type} jobs (maybe it's not failed, "
                        f"or maybe it's already been retried successfully)"
                    )
                )
                return 1

        # Apply --limit
        if args.limit is not None:
            rows = rows[: args.limit]

        if not rows:
            print(_green(f"\n✓ No failed {args.job_type} jobs to retry. All clean!"))
            return 0

        # Step 2: show what we're about to do
        _print_failed_table(rows, args.job_type)

        if args.dry_run:
            print(_yellow("DRY-RUN: no changes made. Re-run without --dry-run to actually retry."))
            return 0

        # Step 3: actually retry each one
        # We close the read-only session and re-open a write session
        # per retry so each failure can be committed independently.
        # (If we kept one session for the whole loop, one bad commit
        # would poison the rest.)
        succeeded: list[str] = []
        failed: list[tuple[str, str]] = []

        for r in rows:
            video_id = r["video_id"]
            title = r["title"]
            print(f"Retrying {args.job_type} for {video_id} ({title[:40]})...")
            try:
                ok, err = _retry_one(video_id, args.job_type)
            except Exception:
                ok, err = False, f"unexpected: {traceback.format_exc()}"
            if ok:
                # Verify it actually completed (the worker may have
                # succeeded but updated the DB; we trust the in-process
                # state of the worker)
                print(_green(f"  ✓ {video_id} {args.job_type} re-ran successfully"))
                succeeded.append(video_id)
            else:
                print(_red(f"  ✗ {video_id} {args.job_type} failed: {err}"))
                failed.append((video_id, err))

        # Step 4: summary
        print()
        if failed:
            print(_red(f"✗ {len(failed)} of {len(rows)} videos still failing:"))
            for vid, err in failed:
                print(f"  {vid}: {err}")
            return 1
        print(_green(f"✓ All {len(succeeded)} {args.job_type} jobs retried successfully."))
        return 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
