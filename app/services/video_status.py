"""Video status reconciliation (Day 5 hotfix2).

Why this exists:
  The `videos.status` field can get out of sync with reality. Specifically:

    video.status='error' even though the Assets table has all 6
    material types for this video (transcript, summary, mindmap,
    flashcards, quiz, topic_timestamps).

  This happened on production data:
    - Day 3: caption job succeeded → video.status='ready', Asset='transcript' written
    - Day 4: generate job was kicked → called Groq with llama-3.3-70b-versatile
            (the now-deprecated model) → provider_unavailable → status='error'
    - But: _store_transcript_asset (Day 3) AND _run_generate_job (Day 4) had
      partial success — the transcript was real, the summary/mindmap/etc.
      were never written (because the generate job failed at the
      call_llm step, before _store_assets), so... wait, then no materials
      would exist for that video. Let me re-check.

  Re-checked: this video's generate job DID complete in 2026-08-25
  (BEFORE llama-3.3-70b-versatile broke). So assets exist from that earlier
  successful run, but later re-generate attempts (after Groq broke) failed
  and re-set video.status='error'.

  Either way, the reconciliation helper is defensive: if a video is in
  'error' but has all required assets, flip it back to 'ready' so the UI
  renders materials correctly.

This is a one-way reconciliation (error → ready) — we never auto-flip
ready to error.
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, Video

# Asset types that together constitute a "ready" video.
# topic_timestamps is optional (Day 3 was added later).
REQUIRED_ASSET_TYPES: tuple[str, ...] = (
    "transcript",
    "summary",
    "mindmap",
    "flashcards",
    "quiz",
)


def has_all_required_assets(db: Session, video_id: str) -> bool:
    """Return True iff the video has at least one Asset row of each
    REQUIRED_ASSET_TYPES type.

    Does NOT consider topic_timestamps (optional; was added in a later
    Day 3 commit and isn't always generated).
    """
    found_types: set[str] = set(
        db.execute(
            select(Asset.asset_type).where(Asset.video_id == video_id)
        ).scalars().all()
    )
    return all(t in found_types for t in REQUIRED_ASSET_TYPES)


def reconcile_video_status(db: Session, video: Video) -> bool:
    """If video.status is 'error' but all required assets exist, flip
    it to 'ready' and commit. Returns True if a change was made.

    Never auto-flips ready → error. Never touches 'pending', 'transcribing',
    'generating', etc. (those are transient worker states).
    """
    if video.status != "error":
        return False
    if not has_all_required_assets(db, video.id):
        return False
    video.status = "ready"
    db.commit()
    return True


def reconcile_all_error_videos(db: Session) -> list[str]:
    """Reconcile every video currently in 'error' state. Returns the list
    of video_ids that were flipped to 'ready'.

    Used by the fixup script and as a safety net.
    """
    error_videos: Iterable[Video] = db.execute(
        select(Video).where(Video.status == "error")
    ).scalars().all()

    flipped: list[str] = []
    for video in error_videos:
        if reconcile_video_status(db, video):
            flipped.append(video.id)
    return flipped
