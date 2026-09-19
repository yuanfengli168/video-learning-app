#!/usr/bin/env python3
"""One-shot recovery for the 2026-09-19 orphaned-pipeline incident.

Re-dispatches the 7 videos whose background jobs vanished across
worker restarts (YouTube caption jobs are in-memory BackgroundTasks;
a worker recycle evaporates them mid-run while the row stays
'transcribing'/'pending' forever — see the day's diagnosis).

Splits by type:
  - file upload (LangChain webm) → _run_auto_pipeline (transcribe
    → generate chain, model = default smart pick)
  - YouTube rows → _run_caption_download_job(generate_materials=True)
    sequential, matching the add-video flow's semantics.

Run from the project root with the venv python:
    venv/bin/python scripts/recover_orphans_2026_09_19.py

Idempotence: each YouTube step checks for an existing transcript
Asset and skips; the pipeline step re-runs transcription from zero
only if no transcript asset exists (it does not — verified).

Exit code: 0 if every step reported success, 1 otherwise.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Ensure project root is importable when run as scripts/xxx.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models import Video  # noqa: E402

# The 7 rows from the 2026-09-19 diagnosis. Status updates:
#   - LangChain webm 6ba2a9ee → READY (recovered run 2, job-registration fix)
#   - MIT 10 acff9004 → READY (recovered run 1)
# Remaining: 5 YouTube rows blocked by the per-IP timedtext throttle
# (cleared by ~23:46 — verified by direct CLI probe before this run).
FILE_UPLOAD_IDS = [
    # "6ba2a9ee-…" removed — already ready; re-running would re-transcribe
]
YOUTUBE_IDS = [
    "f0155494-e2ea-4c98-87d5-24a6b2cc407f",  # NVIDIA desktop AI PC
    "5c7d5969-43de-42e3-9292-e444591e644e",  # seoul guide (81 min)
    "1698a116-5df9-4ae5-a2ca-c69a8918db10",  # MIT 8: Deep Learning NLP
    "f07a74ec-280c-41ec-8c57-21256c00b47d",  # MIT 9: Generative AI LLMs
    "d211a869-3850-46a1-b2a5-2624667d79d9",  # MIT 11: Text-to-Image
]

# Materials generate under the importing admin's tier chain. The MIT
# videos live in a channel owned by this admin uid (from the DB).
ADMIN_UID = "3IarLpyUoQgC6gFyBaAkQxMWDwp2"
ADMIN_ROLE = 0


def recover_file_upload(video_id: str) -> bool:
    """Re-run the full auto pipeline for a file-upload row.

    IMPORTANT: _run_transcribe_job early-returns when the in-memory
    job tracker has no entry for (video_id, 'transcribe') — same lesson
    as scripts/retry_failed_generate.py. We register the job first.
    """
    from app.jobs import serialize_job, start_job
    from app.routers.videos import _run_auto_pipeline
    from app.services.transcription import get_default_model_choice

    db = SessionLocal()
    try:
        video = db.get(Video, video_id)
        title = video.title if video else video_id
        print(f"\n▶ file upload: {title[:60]}")
        # Register the job so the worker doesn't early-return
        job = start_job(video_id, "transcribe", total=100)
        video.status = "transcribing"
        video.last_transcribe_job = serialize_job(job)
        db.commit()
        _run_auto_pipeline(video_id, get_default_model_choice())
        # _run_auto_pipeline sets status itself; read back final state
        db.expire_all()
        video = db.get(Video, video_id)
        ok = video is not None and video.status in ("ready", "generating")
        print(f"  → status={getattr(video, 'status', None)} {'✅' if ok else '❌'}")
        return ok
    finally:
        db.close()


def recover_youtube(video_id: str) -> bool:
    """Re-run the caption pipeline (download → parse → generate)."""
    from app.services.youtube_captions_job import _run_caption_download_job

    db = SessionLocal()
    try:
        video = db.get(Video, video_id)
        title = video.title if video else video_id
        print(f"\n▶ youtube: {title[:60]}")
        t0 = time.time()
        result = _run_caption_download_job(
            video_id,
            generate_materials=True,
            generate_user_id=ADMIN_UID,
            generate_user_role=ADMIN_ROLE,
        )
        # _run_caption_download_job returns None (logs to events table);
        # success signal is the row's final status
        db.expire_all()
        video = db.get(Video, video_id)
        ok = video is not None and video.status == "ready"
        print(
            f"  → status={getattr(video, 'status', None)} "
            f"({time.time() - t0:.1f}s) {'✅' if ok else '❌'}"
        )
        return ok
    finally:
        db.close()


def main() -> int:
    print("=" * 70)
    print("Recovering 7 orphaned videos (2026-09-19 incident)")
    print("=" * 70)

    failures: list[str] = []

    # YouTube rows first (fast, caption downloads don't hold a queue
    # slot and the admin-facing backlog is mostly these 6)
    first = True
    for vid in YOUTUBE_IDS:
        # 30s spacing between YouTube fetches — the 2026-09-19 run
        # without spacing hit YouTube's per-IP subtitle throttling on
        # nearly every fetch (20s read timeouts). MIT 10 succeeded on
        # a retry after a natural gap, which is the pattern.
        if not first:
            print("  … waiting 30s before the next YouTube fetch")
            time.sleep(30)
        first = False
        try:
            if not recover_youtube(vid):
                failures.append(vid)
        except Exception as exc:
            print(f"  ❌ crashed: {type(exc).__name__}: {exc}")
            failures.append(vid)

    # Then the file upload (transcription holds a queue slot for the
    # duration — run it last so the YouTube rows aren't waiting)
    for vid in FILE_UPLOAD_IDS:
        try:
            if not recover_file_upload(vid):
                failures.append(vid)
        except Exception as exc:
            print(f"  ❌ crashed: {type(exc).__name__}: {exc}")
            failures.append(vid)

    print("\n" + "=" * 70)
    if failures:
        print(f"DONE with {len(failures)} failure(s): {failures}")
        return 1
    print("DONE — all 7 videos recovered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())