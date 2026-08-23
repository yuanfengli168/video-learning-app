"""Admin router — endpoints gated by Capability checks.

All routes here require a specific capability (not just role=ADMIN),
so future roles like support_admin can access read-only ones without
curating videos. See doc/mvp2-roles-and-access.md.

Endpoints:

POST /api/admin/videos/youtube
  Body: { url, title, description?, visibility }
  Capability: CURATE_CATALOG
  Effect: extracts YouTube ID, fetches real metadata (title, duration,
    thumbnail, channel, caption tracks) via YouTube Data API v3 when
    YOUTUBE_API_KEY is set. Creates Video row + returns enriched
    video_id + enrichment diagnostics.

  Day 2A: no YouTube API call. Just stores admin-typed title.
  Day 2B: enriches with real YouTube metadata (this file).
    Caption DOWNLOAD is Day 3 (yt-dlp).

GET /api/admin/videos/pending
  Capability: CURATE_CATALOG
  Effect: list videos that haven't finished processing yet.

GET /api/admin/dashboard
  Capability: VIEW_ADMIN_DASHBOARD
  Effect: admin stats (video count, user count, etc).
"""

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.admin import (
    ensure_user_row,
    get_user_role_from_db,
    require_capability,
)
from app.auth.dependencies import get_current_user
from app.auth.roles import Capability, UserRole, VideoVisibility
from app.database import get_db
from app.models import Section, Video
from app.services.youtube import extract_youtube_id, is_valid_youtube_id


router = APIRouter(prefix="/api/admin", tags=["admin"])


# ─────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────


class YouTubeVideoCreate(BaseModel):
    """Request body for adding a YouTube video to the catalog."""

    url: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="YouTube URL (any supported format) or bare 11-char ID",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Display title for the video (admin-curated)",
    )
    description: str | None = Field(
        default=None,
        max_length=2048,
        description="Optional description shown on the video detail page",
    )
    # Int matching VideoVisibility enum (0=PUBLIC, 1=PAID_ONLY, 2=ADMIN_ONLY).
    # Use int (not enum) in the schema so the JSON accepts plain numbers
    # like {"visibility": 0} from the admin form.
    visibility: int = Field(
        default=VideoVisibility.PUBLIC,
        description="0=PUBLIC (everyone), 1=PAID_ONLY (paywall), 2=ADMIN_ONLY (drafts)",
    )
    section_id: str | None = Field(
        default=None,
        max_length=36,
        description=(
            "Section UUID to put the video into. Optional — when omitted, "
            "the backend picks the admin's first available section (or "
            "auto-creates 'Default Catalog' / 'Uncategorized' if the admin "
            "has none yet)."
        ),
    )

    @field_validator("visibility")
    @classmethod
    def _validate_visibility(cls, v: int) -> int:
        """Reject unknown visibility values (defense in depth).

        Without this, {"visibility": 99} would silently default to 0 in
        the DB layer. We want loud failure.
        """
        try:
            VideoVisibility(v)
        except ValueError as exc:
            raise ValueError(
                f"visibility must be 0 (PUBLIC), 1 (PAID_ONLY), or 2 (ADMIN_ONLY). "
                f"Got: {v}"
            ) from exc
        return v


class YouTubeVideoResponse(BaseModel):
    """Response body after successfully adding a YouTube video.

    All enrichment fields are populated only when YOUTUBE_API_KEY is set
    AND the API call succeeds. Otherwise they're empty/null and the
    admin can still add the video with admin-provided title.
    """

    video_id: str
    youtube_id: str
    title: str
    visibility: int
    visibility_name: str
    # Day 2B enrichment (from YouTube Data API v3). All nullable.
    duration_seconds: int | None = None
    thumbnail_url: str | None = None
    channel: str | None = None
    caption_languages: list[str] = []
    """JSON array of BCP-47 codes (e.g. ['en','ja','zh'])."""
    # Diagnostics
    enrichment_status: str = "skipped"
    """One of: 'enriched' (API call succeeded), 'failed' (API call failed),
    'skipped' (no API key configured). Admin can see at a glance whether
    YouTube metadata was populated."""


# ─────────────────────────────────────────────────────────────────────────
# POST /api/admin/videos/youtube
# ─────────────────────────────────────────────────────────────────────────


@router.post("/videos/youtube", response_model=YouTubeVideoResponse)
async def admin_add_youtube_video(
    body: YouTubeVideoCreate,
    background_tasks: BackgroundTasks,
    user: dict[str, Any] = Depends(require_capability(Capability.CURATE_CATALOG)),
    db: Session = Depends(get_db),
) -> YouTubeVideoResponse:
    """Add a YouTube video to the catalog (admin only).

    Flow:
      1. Extract YouTube ID from the URL (any supported format).
      2. Validate the extracted ID (must be exactly 11 chars).
      3. Ensure user row exists (auto-create on first login).
      4. Check for duplicate (same youtube_id already in catalog).
      5. Fetch YouTube metadata (Day 2B): title, duration, thumbnail,
         channel, caption track list. Best-effort — falls back to
         admin-provided title if API key missing or call fails.
      6. Insert Video row with status='pending' (admin added it,
         but processing — caption download, embed preview — happens later).
      7. Return the new video_id + enrichment metadata for the admin UI.

    Day 2A: no YouTube API call. We just store admin-provided title.
    Day 2B: enrich with YouTube Data API v3 (title, duration, thumbnail,
      channel, caption language list). Caption DOWNLOAD is Day 3 (yt-dlp).

    Security:
      - Capability check enforced server-side (no client-side trust)
      - Parameterized SQL via SQLAlchemy ORM (no injection)
      - URL is parsed via regex, NOT evaluated (no SSRF risk)
      - YouTube ID is strictly validated to 11 chars
      - YouTube API call is best-effort (failures don't block adding the video)
    """
    uid = user.get("uid", "")
    if not uid:
        # Belt-and-suspenders: require_capability should have caught this,
        # but defense in depth.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No uid in token claims",
        )

    # Step 1: Extract the YouTube ID
    youtube_id = extract_youtube_id(body.url)
    if not youtube_id or not is_valid_youtube_id(youtube_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Could not extract a valid YouTube video ID from URL: "
                f"{body.url!r}. Supported formats: youtube.com/watch?v=ID, "
                f"youtu.be/ID, youtube.com/shorts/ID, youtube.com/embed/ID, "
                f"or bare 11-char ID."
            ),
        )

    # Step 2: Ensure user row exists (first-login bookkeeping)
    ensure_user_row(uid, user.get("email"), db)

    # Step 3: Check for duplicate (same youtube_id already in catalog)
    existing = (
        db.query(Video)
        .filter(Video.youtube_id == youtube_id)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"YouTube video {youtube_id!r} already in catalog "
                f"as '{existing.title}' (id={existing.id}). "
                f"Use a different URL or update the existing row."
            ),
        )

    # Step 4: Fetch YouTube metadata (Day 2B enrichment)
    # Best-effort: failures don't block the admin add. We log the error
    # and fall back to admin-provided title. The response includes
    # enrichment_status so the admin UI can show a warning if needed.
    enrichment_status = "skipped"
    yt_title: str | None = None
    yt_duration: int | None = None
    yt_thumbnail: str | None = None
    yt_channel: str | None = None
    yt_caption_languages: list[str] = []

    from app.services.youtube_api import (
        YouTubeAPIKeyMissing,
        YouTubeAPIClient,
        YouTubeVideoNotFound,
    )

    try:
        yt_client = YouTubeAPIClient()  # uses settings.youtube_api_key
    except YouTubeAPIKeyMissing:
        yt_client = None

    if yt_client is not None:
        try:
            meta = yt_client.get_video_metadata(youtube_id)
            # If the API returned a real title, prefer it over the
            # admin-typed one. Otherwise keep what the admin typed.
            if meta.title:
                yt_title = meta.title
            yt_duration = meta.duration_seconds or None
            yt_thumbnail = meta.thumbnail_url or None
            yt_channel = meta.channel or None
            yt_caption_languages = [c.language for c in meta.caption_tracks]
            enrichment_status = "enriched"
        except YouTubeVideoNotFound:
            # Video was deleted from YouTube between admin paste and our call
            # — surface a 400 so the admin knows the URL is stale.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"YouTube video {youtube_id!r} not found. "
                    f"It may have been deleted or set to private."
                ),
            )
        except Exception as exc:
            # All other YouTube API errors are non-fatal. Log + continue.
            # The admin can manually fix the metadata later.
            import logging
            logging.getLogger(__name__).warning(
                f"YouTube API enrichment failed for {youtube_id}: {exc}"
            )
            enrichment_status = "failed"

    # Step 5: Resolve title (API beats admin if API succeeded)
    final_title = yt_title or body.title

    # Step 6: Create the Video row
    import json as _json
    video = Video(
        title=final_title,
        # YouTube ID stored in its own column
        youtube_id=youtube_id,
        # Day 2B enrichment (None if API call skipped/failed)
        thumbnail_url=yt_thumbnail,
        channel=yt_channel,
        caption_languages=_json.dumps(yt_caption_languages),
        duration=yt_duration or 0.0,
        # Visibility (PUBLIC/PAID_ONLY/ADMIN_ONLY) from request
        visibility=body.visibility,
        # status='pending' until processing completes (Day 3+ caption download)
        status="pending",
        # Pre-pivot: filename/file_path/file_size were required.
        # For YouTube-typed videos these are NULL/dummy.
        # We fill filename with the youtube_id (so legacy queries don't break)
        # and file_path with the canonical watch URL.
        filename=f"youtube:{youtube_id}",
        file_path=f"https://www.youtube.com/watch?v={youtube_id}",
        file_size=0,
    )
    # Resolve which Section this video lands in.
    #
    # Priority:
    #   1. Explicit body.section_id from the admin form (UUID)
    #   2. First Section of the admin's first Course (alphabetical)
    #   3. Auto-create a "Default Catalog" Course + "Uncategorized" Section
    #
    # Why honor the admin's pick but auto-fall-back: lets admins curate
    # intentionally while not blocking adds when they're new and have no
    # courses yet (the "Default Catalog" catch-all is for that case).
    from app.services.section_picker import (
        resolve_section_for_new_video,
    )

    try:
        chosen_section = resolve_section_for_new_video(
            db=db, uid=uid, requested_section_id=body.section_id
        )
    except ValueError as exc:
        # section_id is invalid (missing, belongs to another admin).
        # 400 because the request body is wrong, not 403 (which would
        # leak that other admins have sections).
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    video.section_id = chosen_section.id

    db.add(video)
    db.commit()
    db.refresh(video)

    # Day 3 — kick off caption download in the background. The admin
    # gets the response immediately (status='pending'); the worker
    # updates status to 'transcribing' → 'ready' / 'error' as it goes.
    # Reuses the same _run_caption_download_job() used by the retry
    # endpoint so admin-initiated adds and manual retries share
    # one code path.
    from app.services.youtube_captions_job import (
        _run_caption_download_job,
    )

    background_tasks.add_task(
        _run_caption_download_job,
        video_id=video.id,
    )

    visibility_name = {
        VideoVisibility.PUBLIC: "public",
        VideoVisibility.PAID_ONLY: "paid_only",
        VideoVisibility.ADMIN_ONLY: "admin_only",
    }.get(VideoVisibility(body.visibility), "unknown")

    return YouTubeVideoResponse(
        video_id=video.id,
        youtube_id=youtube_id,
        title=video.title,
        visibility=body.visibility,
        visibility_name=visibility_name,
        duration_seconds=yt_duration,
        thumbnail_url=yt_thumbnail,
        channel=yt_channel,
        caption_languages=yt_caption_languages,
        enrichment_status=enrichment_status,
    )


# ─────────────────────────────────────────────────────────────────────────
# POST /api/admin/videos/{id}/captions/retry  (Day 3)
# ─────────────────────────────────────────────────────────────────────────


@router.post("/videos/{video_id}/captions/retry")
async def admin_retry_captions(
    video_id: str,
    user: dict[str, Any] = Depends(require_capability(Capability.CURATE_CATALOG)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Force a re-download of captions for a YouTube video.

    Used by the admin UI "Retry captions" button when the background
    caption download failed (e.g. transient network error). Unlike the
    auto-fire on add (which skips if a transcript Asset already exists),
    this endpoint always overwrites.

    Runs synchronously in the request handler. Returns a summary dict
    that the UI shows as a toast:
      {status: 'completed'|'failed', segments: int, language: str,
       source: str, duration: float, error?: str}

    Capability: CURATE_CATALOG — admins only.

    Security: parameterized SQL only (no injection); the video_id is
    validated by SQLAlchemy ORM before any I/O.
    """
    from app.services.youtube_captions_job import (
        retry_caption_download,
    )

    # Cheap defense: return 404 for non-existent videos rather than
    # leaking that the ID format is valid. The retry helper also
    # handles this internally; we do it here for a cleaner error code.
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Video {video_id} not found",
        )

    result = retry_caption_download(video_id, db)
    if result["status"] == "failed":
        # 200 anyway — the retry endpoint never 500s. The UI shows the
        # error in the toast and stays on the page.
        return result
    return result


# ─────────────────────────────────────────────────────────────────────────
# GET /api/admin/videos/{id}/captions/status  (Day 3)
# ─────────────────────────────────────────────────────────────────────────


@router.get("/videos/{video_id}/captions/status")
async def admin_get_caption_status(
    video_id: str,
    user: dict[str, Any] = Depends(require_capability(Capability.CURATE_CATALOG)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Read the current caption-download state for a YouTube video.

    Polled by the admin UI after a 'Add Video' response to know when
    the background caption job finishes (transitions from
    'transcribing' to 'ready' / 'error').

    Returns:
      {
        "video_id": str,
        "status":   str,        # "pending"|"transcribing"|"ready"|"error"
        "transcript_segments": int | None,  # segment count if ready
        "language":  str | None,            # locked language if set
        "transcribed_at": str | None,       # ISO timestamp if ready
        "job":       dict | None,           # in-flight job state (from app.jobs)
        "error":     str | None,            # human-readable failure reason
      }
    """
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Video {video_id} not found",
        )

    # Count segments if the transcript Asset exists
    segments_count: int | None = None
    from app.models import Asset

    transcript = db.execute(
        select(Asset).where(
            Asset.video_id == video_id,
            Asset.asset_type == "transcript",
        )
    ).scalar_one_or_none()
    if transcript is not None:
        try:
            import json as _json

            payload = _json.loads(transcript.content)
            segs = payload.get("segments", [])
            segments_count = len(segs) if isinstance(segs, list) else None
        except _json.JSONDecodeError:
            segments_count = None

    # Look up the in-flight job (None if no job is running for this
    # video — common case: job already finished, state is on video.status)
    from app.jobs import get_job as _get_job

    job = _get_job(video_id, "transcribe")

    # Translate whisper_fallback_reason (reused for caption errors)
    # into the response's `error` field when status='error'
    error_msg: str | None = None
    if video.status == "error":
        # Prefer the live job's error message (more recent); fall
        # back to the DB-stamped reason
        if job is not None and job.get("error"):
            error_msg = job["error"]
        elif video.whisper_fallback_reason:
            # Strip the "Captions: " prefix we add in _set_video_error_status
            error_msg = video.whisper_fallback_reason.replace(
                "Captions: ", "", 1
            )

    return {
        "video_id": video_id,
        "status": video.status,
        "transcript_segments": segments_count,
        "language": video.language,
        "transcribed_at": (
            video.transcribed_at.isoformat() if video.transcribed_at else None
        ),
        "job": job,
        "error": error_msg,
    }
