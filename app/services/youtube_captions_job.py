"""YouTube caption download — background worker (Day 3).

This is the worker that runs after an admin adds a YouTube video to
the catalog. It downloads the captions (manual or auto-generated),
parses them into Whisper-compatible segments, and persists them as an
Asset (type='transcript') on the Video row.

Mirrors the pattern in app/routers/videos.py::_run_transcribe_job:
  - Opens its own DB session (request session is closed by then)
  - Reports progress via app.jobs.{start_job, set_progress, finish_job}
  - Sets video.status to drive UI progress bars
  - Stamps video.transcribed_at on success

Difference from the Whisper path:
  - Faster (~1-3s vs 5-15min per hour of video)
  - No GPU required
  - Doesn't fill in video.language (YouTube caption's language IS
    authoritative; we stamp it on success so future Whisper retries
    lock to it)
  - On failure (no captions / private video / yt-dlp error), we
    leave status='pending' + set caption_status='error' on the
    Asset row so the admin can manually retry with Whisper.

Public API
----------
  _run_caption_download_job(video_id: str) -> None
      Background worker. Call via FastAPI BackgroundTasks.add_task.
      Idempotent: if the video already has a transcript Asset,
      skips (use the retry endpoint to force a re-download).

  retry_caption_download(video_id: str, db: Session) -> dict
      Synchronous retry endpoint logic. Used by the admin "Retry
      captions" button.

Why not just call fetch_youtube_captions() in-line in the route?
  - Caption download takes 1-3s (network), Whisper takes 5-15min.
  - We want the admin's POST to return immediately regardless, so
    the UI can show "pending" + poll.
  - BackgroundTasks gives us this for free; no need for a queue.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.jobs import (
    JobType,
    finish_job,
    get_job,
    serialize_job,
    set_progress,
    start_job,
)
from app.models import Asset, Video
from app.services.transcription import transcript_to_json
from app.services.youtube_captions import (
    YouTubeCaptionsError,
    YouTubeCaptionsFailed,
    YouTubeCaptionsUnavailable,
    fetch_youtube_captions,
)
from app.utils.events import log_event

logger = logging.getLogger(__name__)


# Allowed caption_status values for the Asset row. The Asset model
# doesn't have a dedicated status column, so we encode it in a
# JSON metadata blob (Asset.content starts with `{"_caption_status": ...}`
# when relevant, then segments follow). Why this ugly hack instead of a
# proper column:
#   - Asset is shared with summary/quiz/flashcards/mindmap which don't
#     need this status — adding a column would force all of them to set
#     it to NULL.
#   - The metadata prefix is a single line of JSON inside the content
#     column; readers can detect it with content.startswith('{"_caption_').
# Future refactor (MVP3+): add a proper `status` column on Asset.
CAPTION_STATUS_PENDING = "pending"
CAPTION_STATUS_READY = "ready"
CAPTION_STATUS_ERROR = "error"
CAPTION_STATUS_UNAVAILABLE = "unavailable"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _store_transcript_asset(
    db: Session,
    video_id: str,
    result_dict: dict[str, Any],
) -> Asset:
    """Upsert the transcript Asset with the given segments.

    Mirrors the pattern in app/routers/videos.py (line ~558):
    update if a transcript already exists, insert otherwise.

    Returns the saved Asset (refreshed).
    """
    content_json = transcript_to_json(result_dict)
    existing = db.execute(
        select(Asset).where(
            Asset.video_id == video_id,
            Asset.asset_type == "transcript",
        )
    ).scalar_one_or_none()
    if existing:
        existing.content = content_json
        db.flush()
        db.refresh(existing)
        return existing
    asset = Asset(
        video_id=video_id,
        asset_type="transcript",
        content=content_json,
    )
    db.add(asset)
    db.flush()
    db.refresh(asset)
    return asset


def _set_video_error_status(db: Session, video_id: str, error_msg: str) -> None:
    """Mark the video as errored + record the reason.

    Used when caption download fails irrecoverably (private video,
    network error, parse error). The admin can see this on the
    dashboard and either retry or fall back to Whisper.
    """
    video = db.get(Video, video_id)
    if video is None:
        return
    # Keep status='pending' so the UI knows this isn't done yet;
    # we record the failure reason in whisper_fallback_reason which
    # the existing admin UI already reads for MLX fallback messages.
    # (Reusing an existing column instead of adding a new one.)
    video.whisper_fallback_reason = f"Captions: {error_msg[:500]}"
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Main background worker
# ─────────────────────────────────────────────────────────────────────────────


def _run_caption_download_job(video_id: str) -> None:
    """Download YouTube captions for the given video and persist them.

    Called by FastAPI BackgroundTasks after the admin adds a video.
    Opens its own DB session because the request session is closed.

    Status transitions:
      pending       → admin just added the video
      transcribing  → we're downloading + parsing captions now
      ready         → transcript Asset written, transcribed_at stamped
      error         → download failed (whisper_fallback_reason set)

    Args:
        video_id: UUID of the Video row.

    Side effects:
      - Mutates video.status
      - Stamps video.transcribed_at on success
      - Stamps video.language on success (locks future Whisper retries)
      - Upserts Asset(type='transcript') with the parsed segments
      - Records job state via app.jobs (UI polls GET /api/videos/{id}/status)
    """
    job = start_job(video_id, "transcribe", total=100)
    set_progress(
        job, done=2, total=100,
        message="Fetching captions from YouTube...",
    )

    # Open a fresh session — the request handler's session is closed
    # by the time BackgroundTasks fires.
    db = SessionLocal()
    try:
        video = db.get(Video, video_id)
        if video is None:
            finish_job(
                job, status="failed",
                error=f"Video {video_id} disappeared before captions ran",
            )
            return

        # Sanity: skip if video has no youtube_id (defensive — should
        # never happen since the admin endpoint requires it, but a
        # legacy video row from a different flow might not have one).
        if not video.youtube_id:
            finish_job(
                job, status="failed",
                error="Video has no youtube_id; cannot fetch captions",
            )
            return

        # Sanity: skip if a transcript Asset already exists.
        # Idempotent re-runs (admin clicked "Retry" while we were
        # already running) should not double-write. The retry
        # endpoint handles the explicit "force overwrite" case.
        existing = db.execute(
            select(Asset).where(
                Asset.video_id == video_id,
                Asset.asset_type == "transcript",
            )
        ).scalar_one_or_none()
        if existing is not None:
            finish_job(
                job, status="completed",
                message="Transcript already exists; skipped",
            )
            return

        video.status = "transcribing"
        video.transcribe_started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        # Parse caption_languages (stored as JSON string from Day 2B).
        # May be empty string ('[]') if the YouTube Data API didn't
        # return any caption tracks at the time of admin add.
        try:
            available_languages = json.loads(video.caption_languages or "[]")
        except json.JSONDecodeError:
            logger.warning(
                "Video %s has invalid caption_languages JSON %r, "
                "treating as empty", video_id, video.caption_languages,
            )
            log_event(
                db, level="WARNING",
                source="services.youtube_captions_job",
                message=f"invalid caption_languages JSON for {video_id}",
                video_id=video_id,
                context={"raw": video.caption_languages},
            )
            available_languages = []

        set_progress(
            job, done=10, total=100,
            message=(
                f"Downloading captions "
                f"({len(available_languages)} tracks available)..."
            ),
        )

        # Import here so a missing yt-dlp doesn't break the admin
        # endpoint itself (the route stays usable; only the background
        # job fails with a clear error in finish_job).
        # (Imports are at module top so tests can patch this module's
        # attribute directly.)

        caption: Any = None
        try:
            # First attempt: with our language preference (user_locked
            # or first of available_languages).
            caption = fetch_youtube_captions(
                youtube_id=video.youtube_id,
                available_languages=available_languages,
                user_locked_language=video.language,
            )
        except (YouTubeCaptionsUnavailable, YouTubeCaptionsFailed):
            # Two reasons to retry without preferences:
            #   1. Unavailable — chosen language wasn't on YouTube
            #      (e.g. user locked "en-GB" when only "en" exists)
            #   2. Failed (HTTP 429) — YouTube rate-limited because
            #      our chosen language triggered yt-dlp to also try
            #      all ".*" matches (which hits 100+ language tracks
            #      and trips the limiter). Letting yt-dlp pick
            #      whatever's first in the manifest skips the wide
            #      match. Live test 2026-08-23 caught this.
            logger.info(
                "Caption first attempt failed for %s, "
                "retrying without language preference",
                video_id,
            )
            log_event(
                db, level="INFO",
                source="services.youtube_captions_job",
                message=(
                    f"caption first attempt failed for {video_id}; "
                    f"retrying without language preference"
                ),
                video_id=video_id,
            )
            try:
                caption = fetch_youtube_captions(
                    youtube_id=video.youtube_id,
                    available_languages=[],
                    user_locked_language=None,
                )
            except YouTubeCaptionsError:
                # Truly nothing or still rate-limited — re-raise the
                # ORIGINAL error so the handler below gets the right
                # exception type (Unavailable vs Failed)
                raise
        except YouTubeCaptionsUnavailable as exc:
            # Video has no captions. Set status='error' with a clear
            # message so the admin can fall back to Whisper (the
            # existing /api/videos/{id}/transcribe endpoint works
            # for legacy uploaded videos; we extend it to YouTube
            # videos too in a follow-up).
            video.status = "error"
            _set_video_error_status(db, video_id, str(exc))
            finish_job(
                job, status="failed",
                error=(
                    f"No captions available for this video on YouTube. "
                    f"Falling back to Whisper is recommended. ({exc})"
                ),
            )
            video.last_transcribe_job = serialize_job(job)
            db.commit()
            return
        except YouTubeCaptionsFailed as exc:
            video.status = "error"
            _set_video_error_status(db, video_id, str(exc))
            finish_job(
                job, status="failed",
                error=f"Caption download failed: {exc}",
            )
            video.last_transcribe_job = serialize_job(job)
            db.commit()
            return
        except Exception as exc:
            # Defense in depth — yt-dlp occasionally raises unexpected
            # errors. Log full traceback, fail the job, keep video alive.
            logger.exception(
                "Unexpected error fetching captions for %s", video_id,
            )
            log_event(
                db, level="ERROR",
                source="services.youtube_captions_job",
                message=f"unexpected error fetching captions for {video_id}",
                video_id=video_id,
                context={"error": str(exc), "type": type(exc).__name__},
            )
            video.status = "error"
            _set_video_error_status(
                db, video_id, f"Unexpected: {exc}"
            )
            finish_job(
                job, status="failed",
                error=f"Unexpected caption error: {exc}",
            )
            video.last_transcribe_job = serialize_job(job)
            db.commit()
            return

        set_progress(
            job, done=80, total=100,
            message=(
                f"Got {len(caption.segments)} segments "
                f"({caption.source}, lang={caption.language}); saving..."
            ),
        )

        # Persist the transcript Asset (shape matches Whisper output)
        _store_transcript_asset(
            db,
            video_id=video_id,
            result_dict=caption.to_dict(),
        )

        # Stamp success metadata
        video.duration = caption.duration
        # Lock the language so future Whisper retries use the same
        # language (anti-drift, mirrors MVP3.0 #2b logic).
        if not video.language:
            video.language = caption.language
        # whisper_backend / whisper_resolved_model are left NULL —
        # this wasn't Whisper. Admin UI shows "YouTube captions
        # ({lang}, {source})" from the transcript Asset's metadata.
        video.status = "ready"
        video.transcribed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        log_event(
            db, level="INFO",
            source="services.youtube_captions_job",
            message=f"caption download completed for {video_id}",
            video_id=video_id,
            context={
                "language": caption.language,
                "source": caption.source,
                "segments": len(caption.segments),
                "duration": caption.duration,
            },
        )
        finish_job(
            job,
            status="completed",
            message=(
                f"✓ {len(caption.segments)} caption segments "
                f"(source={caption.source}, lang={caption.language})"
            ),
        )
        video.last_transcribe_job = serialize_job(job)
        db.commit()

    except Exception as exc:
        # Final defense — anything we missed above lands here.
        logger.exception(
            "Caption job for video %s crashed", video_id,
        )
        log_event(
            db, level="ERROR",
            source="services.youtube_captions_job",
            message=f"caption job crashed for {video_id}",
            video_id=video_id,
            context={"error": str(exc), "type": type(exc).__name__},
        )
        try:
            video = db.get(Video, video_id)
            if video is not None:
                video.status = "error"
                _set_video_error_status(
                    db, video_id, f"Crashed: {exc}"
                )
                finish_job(
                    job, status="failed",
                    error=f"Worker crashed: {exc}",
                )
                video.last_transcribe_job = serialize_job(job)
                db.commit()
        except Exception:
            # At this point the DB might be in a bad state.
            # Roll back so the session can be closed cleanly.
            db.rollback()
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Retry helper (called by the admin UI "Retry captions" button)
# ─────────────────────────────────────────────────────────────────────────────


def retry_caption_download(video_id: str, db: Session) -> dict[str, Any]:
    """Force a re-download of captions, overwriting any existing transcript.

    Unlike _run_caption_download_job (which skips if a transcript
    already exists), this always overwrites. Used by the admin UI's
    "Retry captions" button when the previous attempt errored.

    Runs synchronously in the request handler. Returns a summary dict
    the UI shows as a toast.

    Args:
        video_id: UUID of the Video row.
        db: SQLAlchemy session (the request handler's session).

    Returns:
        Dict with 'status' ('completed' | 'failed'), 'segments',
        'language', 'source', 'duration' on success, or 'error' on failure.

    Raises:
        Nothing — all exceptions are caught and reported in the dict.
            This keeps the retry endpoint simple: it never 500s, it
            always returns a JSON the UI can show as a toast.
    """
    video = db.get(Video, video_id)
    if video is None:
        return {"status": "failed", "error": "Video not found"}
    if not video.youtube_id:
        return {
            "status": "failed",
            "error": "Video has no youtube_id; cannot fetch captions",
        }

    # Parse available languages
    try:
        available_languages = json.loads(video.caption_languages or "[]")
    except json.JSONDecodeError:
        available_languages = []

    from app.services.youtube_captions import (
        YouTubeCaptionsError,
    )
    # fetch_youtube_captions is imported at module top

    # Mark as in-flight (so concurrent retries don't double-run).
    # Race-safe enough for MVP (single admin); production would use
    # an advisory lock or SELECT ... FOR UPDATE.
    video.status = "transcribing"
    db.commit()

    try:
        caption = fetch_youtube_captions(
            youtube_id=video.youtube_id,
            available_languages=available_languages,
            user_locked_language=video.language,
        )
    except YouTubeCaptionsError as exc:
        video.status = "error"
        _set_video_error_status(db, video_id, str(exc))
        db.commit()
        return {"status": "failed", "error": str(exc)}
    except Exception as exc:
        logger.exception("retry_caption_download crashed for %s", video_id)
        log_event(
            db, level="ERROR",
            source="services.youtube_captions_job",
            message=f"retry_caption_download crashed for {video_id}",
            video_id=video_id,
            context={"error": str(exc), "type": type(exc).__name__},
        )
        video.status = "error"
        _set_video_error_status(
            db, video_id, f"Unexpected: {exc}"
        )
        db.commit()
        return {"status": "failed", "error": str(exc)}

    # Persist + finalize
    _store_transcript_asset(
        db, video_id=video_id, result_dict=caption.to_dict()
    )
    video.duration = caption.duration
    if not video.language:
        video.language = caption.language
    video.status = "ready"
    video.transcribed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()

    return {
        "status": "completed",
        "segments": len(caption.segments),
        "language": caption.language,
        "source": caption.source,
        "duration": caption.duration,
    }