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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.admin import (
    ensure_user_row,
    get_user_role_from_db,
    require_capability,
)
from app.auth.dependencies import get_current_user
from app.auth.roles import Capability, UserRole, VideoVisibility
from app.database import get_db
from app.models import Channel, Course, Section, Video
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
    # ── 2026-09-08 channel catalog fields ──
    # The admin upload page can now target CHANNEL content: pick an
    # existing channel, create a new one inline, pick/create a playlist
    # (Course) inside it, or leave both unset (personal course flow,
    # exactly today's behavior). All three fields are optional and
    # independently so — the resolver below decides precedence.
    channel_id: str | None = Field(
        default=None,
        max_length=36,
        description=(
            "Existing channel UUID. When set (and no new_channel_name), "
            "the video lands in a playlist under this channel."
        ),
    )
    new_channel_name: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "Create a NEW channel with this display name (slug derived "
            "server-side). Takes precedence over channel_id."
        ),
    )
    new_playlist_title: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "Create a NEW playlist (Course) under the resolved channel "
            "with this title. Only meaningful together with a channel."
        ),
    )
    # 2026-09-08 fix (user report): pick an EXISTING playlist in the
    # channel — previously only "new playlist" or "latest" existed,
    # so a channel created yesterday couldn't be targeted directly.
    # Resolution precedence: new_playlist_title > existing_playlist_id
    # > newest playlist. The video lands in the picked playlist's
    # FIRST section.
    existing_playlist_id: str | None = Field(
        default=None,
        max_length=36,
        description=(
            "Existing Course UUID under the resolved channel — add the "
            "video into that playlist."
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


class YouTubeBulkImportRequest(BaseModel):
    """Bulk-add videos to a channel playlist (2026-09-08).

    Two input modes (validated by the endpoint):
      A) urls: list of individual video URLs/IDs (order preserved =
         order_index)
      B) playlist_url: a YouTube PLAYLIST URL — auto-expanded in
         playlist order via the Data API (playlistItems.list)

    Duplicate youtube_ids already in the catalog are SKIPPED (idempotent
    — safe to re-run). Order is preserved via Video.order_index.
    """

    urls: list[str] = Field(
        default_factory=list,
        description="Individual video URLs/IDs, in the desired order.",
    )
    playlist_url: str | None = Field(
        default=None,
        max_length=2048,
        description="A YouTube playlist URL — expands to its videos in order.",
    )
    channel_id: str | None = Field(
        default=None, max_length=36,
        description="Existing channel UUID (required unless new_channel_name).",
    )
    new_channel_name: str | None = Field(
        default=None, max_length=255,
        description="Create a NEW channel with this name (takes precedence).",
    )
    new_playlist_title: str | None = Field(
        default=None, max_length=255,
        description="Create a NEW playlist in the channel for these videos.",
    )
    existing_playlist_id: str | None = Field(
        default=None, max_length=36,
        description="Existing Course UUID in the channel to add into.",
    )
    visibility: int = Field(default=VideoVisibility.PUBLIC)

    @field_validator("visibility")
    @classmethod
    def _validate_visibility(cls, v: int) -> int:
        try:
            VideoVisibility(v)
        except ValueError as exc:
            raise ValueError(
                f"visibility must be 0, 1, or 2. Got: {v}"
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
    # 2026-09-08 channel catalog info — where the video landed.
    # All None when the personal-course flow was used.
    channel_name: str | None = None
    channel_slug: str | None = None
    playlist_title: str | None = None
    created_channel: bool = False
    created_playlist: bool = False


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
    # 2026-09-08 channel flow (takes precedence when any channel
    # field is set):
    #   1. new_channel_name → create/reuse channel by name
    #   2. else channel_id  → that channel
    #   3. new_playlist_title → new playlist (Course) in the channel
    #   4. else newest playlist's first section ("Main Playlist"
    #      auto-created if the channel is empty)
    #
    # Legacy personal-course flow (no channel fields):
    #   1. Explicit body.section_id from the admin form (UUID)
    #   2. First Section of the admin's first Course (alphabetical)
    #   3. Auto-create a "Default Catalog" Course + "Uncategorized" Section
    from app.services.section_picker import (
        resolve_section_for_new_video,
    )
    from app.services.channel_upload import resolve_channel_target

    created_channel: object | None = None
    created_playlist: object | None = None
    try:
        # Only a NEW-channel-name flow can create a channel. When the
        # form picks an existing channel_id, it obviously existed.
        _created_new_channel = False
        if body.new_channel_name and body.new_channel_name.strip():
            from app.models import Channel as _Channel
            from sqlalchemy import select as _select
            _existed_before = db.execute(
                _select(_Channel.id).where(
                    _Channel.name == body.new_channel_name.strip()
                )
            ).scalar_one_or_none() is not None
            _created_new_channel = not _existed_before

        channel, new_playlist, chosen_section = resolve_channel_target(
            db=db,
            uid=uid,
            channel_id=body.channel_id,
            new_channel_name=body.new_channel_name,
            new_playlist_title=body.new_playlist_title,
            existing_playlist_id=body.existing_playlist_id,
        )
        if _created_new_channel:
            created_channel = channel
        created_playlist = new_playlist
        if chosen_section is None:
            # No channel context — the personal-course flow, unchanged.
            chosen_section = resolve_section_for_new_video(
                db=db, uid=uid, requested_section_id=body.section_id
            )
    except ValueError as exc:
        # channel_id/section_id invalid (missing, belongs to another
        # admin). 400 because the request body is wrong, not 403.
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
        # Channel catalog info (None-values when personal-course flow)
        channel_name=channel.name if channel is not None else None,
        channel_slug=channel.slug if channel is not None else None,
        playlist_title=(
            (created_playlist.title if created_playlist is not None
             else chosen_section.course.title)
            if channel is not None else None
        ),
        created_channel=created_channel is not None,
        created_playlist=created_playlist is not None,
    )


# ─────────────────────────────────────────────────────────────────────────
# GET /api/admin/channels/{id}/playlists  (2026-09-08 — upload form picker)
# ─────────────────────────────────────────────────────────────────────────


@router.get("/channels/{channel_id}/playlists")
async def admin_list_channel_playlists(
    channel_id: str,
    name: str | None = None,
    user: dict[str, Any] = Depends(require_capability(Capability.CURATE_CATALOG)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Playlists in a channel, for the upload form's dropdown.

    Newest first (matches the "blank = newest" default the resolver
    uses, so the top option is what a blank pick means).

    Two lookup modes:
      * channel_id = a Channel UUID → that channel
      * channel_id = 'by-name' + ?name=… → exact-name match (used when
        the admin TYPED a channel name that may already exist; mirrors
        the resolver's reuse-by-name semantics). 404 if no such channel.
    """
    if channel_id == "by-name":
        if not name or not name.strip():
            raise HTTPException(
                status_code=400, detail="?name= is required with by-name"
            )
        channel = db.execute(
            select(Channel).where(Channel.name == name.strip())
        ).scalar_one_or_none()
        if channel is None:
            raise HTTPException(
                status_code=404,
                detail=f"No channel named {name!r} yet — it will be "
                       f"created when you submit.",
            )
    else:
        channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    courses = db.execute(
        select(Course)
        .where(Course.channel_id == channel.id)
        .order_by(Course.created_at.desc())
    ).scalars().all()
    return {
        "channel": channel.name,
        "channel_id": channel.id,
        "playlists": [
            {"id": c.id, "title": c.title} for c in courses
        ],
    }


# ─────────────────────────────────────────────────────────────────────────
# POST /api/admin/videos/youtube/bulk  (2026-09-08 — bulk channel import)
# ─────────────────────────────────────────────────────────────────────────


@router.post("/videos/youtube/bulk", response_model=None)
async def admin_bulk_import_youtube(
    body: YouTubeBulkImportRequest,
    background_tasks: BackgroundTasks,
    user: dict[str, Any] = Depends(require_capability(Capability.CURATE_CATALOG)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Bulk-add YouTube videos to a channel playlist.

    Modes (exactly one required):
      * urls:         individual URLs/IDs, order preserved
      * playlist_url: expands a YouTube playlist via playlistItems.list

    Channel resolution mirrors the single-add endpoint (new_channel_name
    > channel_id). new_playlist_title creates a Course; blank = newest
    playlist. Videos land in the playlist's FIRST section with
    order_index = base + i, preserving playlist order.

    Idempotent: videos whose youtube_id is already in the catalog are
    skipped and reported as {"status": "skipped", ...}. Re-running with
    the same list is safe.

    Caption jobs are STAGGERED (10s apart) — adding 9 videos at once
    otherwise triggers YouTube's HTTP 429 rate limit on yt-dlp caption
    downloads (hit in practice on 2026-09-08; the retry endpoint
    recovers, but staggering avoids the storm entirely).

    Enrichment: playlist_url mode gets titles/thumbnails from the
    playlistItems response (no per-video videos.list calls — quota-
    friendly). urls mode does one get_video_metadata per video
    (existing helper, best-effort).
    """
    import json as _json
    import time as _time

    from app.services.youtube import extract_youtube_id
    from app.services.channel_upload import resolve_channel_target
    from app.services.youtube_captions_job import _run_caption_download_job

    uid = user.get("uid", "")
    if not uid:
        raise HTTPException(status_code=401, detail="No uid in token claims")
    ensure_user_row(uid, user.get("email"), db)

    # ── 1. Resolve the input list ──────────────────────────────────
    entries: list[dict[str, Any]] = []  # {youtube_id, title?, thumbnail?}
    if body.playlist_url:
        from app.services.youtube_api import (
            YouTubeAPIKeyMissing,
            YouTubeAPIClient,
            YouTubePlaylistNotFound,
        )
        try:
            client = YouTubeAPIClient()
        except YouTubeAPIKeyMissing:
            raise HTTPException(
                status_code=400,
                detail="Playlist import needs YOUTUBE_API_KEY (metadata "
                       "comes from playlistItems.list).",
            )
        try:
            pl_videos = client.list_playlist_videos(body.playlist_url)
        except YouTubePlaylistNotFound as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        entries = [
            {
                "youtube_id": v.youtube_id,
                "title": v.title,
                "thumbnail": v.thumbnail_url,
            }
            for v in pl_videos
        ]
    elif body.urls:
        for raw in body.urls:
            vid = extract_youtube_id(raw.strip() if raw else "")
            if not vid:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not extract a video ID from {raw!r}",
                )
            entries.append({"youtube_id": vid})
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide either 'urls' (list of video URLs) or 'playlist_url'.",
        )

    if not entries:
        return {
            "added": 0, "skipped": 0, "results": [],
            "message": "No videos found in the input.",
        }

    # ── 2. Resolve channel + playlist target ───────────────────────
    created_channel_flag = False
    if body.new_channel_name and body.new_channel_name.strip():
        # Honest creation reporting: only a NEW-name flow can create a
        # channel; compare pre-existence (reuse-by-name semantics —
        # exact-name matches are reused, not created).
        from app.models import Channel as _Channel
        from sqlalchemy import select as _select
        _existed = db.execute(
            _select(_Channel.id).where(
                _Channel.name == body.new_channel_name.strip()
            )
        ).scalar_one_or_none() is not None
        created_channel_flag = not _existed

    try:
        channel, new_playlist, section = resolve_channel_target(
            db=db,
            uid=uid,
            channel_id=body.channel_id,
            new_channel_name=body.new_channel_name,
            new_playlist_title=body.new_playlist_title,
            existing_playlist_id=body.existing_playlist_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if section is None:
        # Bulk import requires a channel target (the personal-course
        # flow's section resolution doesn't make sense for 9 videos).
        raise HTTPException(
            status_code=400,
            detail="Bulk import needs a channel: set channel_id or "
                   "new_channel_name (optionally new_playlist_title).",
        )

    course = db.get(Course, section.course_id)

    # ── 3. Insert (skip duplicates), preserving order ───────────────
    # Base order_index = current max + 1, so appended videos keep the
    # playlist's existing order intact.
    base = int(
        db.execute(
            select(func.max(Video.order_index)).where(
                Video.section_id == section.id
            )
        ).scalar()
        or 0
    )

    # Pre-fetch existing youtube_ids in ONE query (not N).
    ids_in = [e["youtube_id"] for e in entries]
    existing_rows = (
        db.execute(
            select(Video.youtube_id).where(Video.youtube_id.in_(ids_in))
        )
        .scalars()
        .all()
    )
    existing = set(existing_rows)

    results: list[dict[str, Any]] = []
    added_ids: list[str] = []
    next_index = base
    for e in entries:
        vid = e["youtube_id"]
        if vid in existing:
            results.append(
                {"youtube_id": vid, "status": "skipped",
                 "reason": "already in catalog"}
            )
            continue
        video = Video(
            title=(e.get("title") or vid)[:255],
            youtube_id=vid,
            thumbnail_url=e.get("thumbnail"),
            duration=0,
            visibility=body.visibility,
            status="pending",
            filename=f"youtube:{vid}",
            file_path=f"https://www.youtube.com/watch?v={vid}",
            file_size=0,
            section_id=section.id,
            order_index=next_index,
        )
        db.add(video)
        added_ids.append(vid)
        existing.add(vid)  # also dedupes within the request itself
        next_index += 1
        results.append(
            {"youtube_id": vid, "status": "added", "title": video.title}
        )

    db.commit()

    # ── 4. Kick caption jobs, staggered 10s apart ──────────────────
    # background_tasks run AFTER the response; the sleep staggers the
    # yt-dlp calls so YouTube doesn't 429 us.
    delay = 0
    for vid in added_ids:
        row = (
            db.execute(select(Video).where(Video.youtube_id == vid))
            .scalars()
            .first()
        )
        if row is None:
            continue  # race: deleted between commit and here
        background_tasks.add_task(
            _staggered_caption_job, row.id, delay
        )
        delay += 10

    channel_name = channel.name if channel else None
    channel_slug = channel.slug if channel else None
    return {
        "added": len(added_ids),
        "skipped": len(entries) - len(added_ids),
        "results": results,
        "channel_name": channel_name,
        "channel_slug": channel_slug,
        "playlist_title": course.title if course else None,
        "created_channel": created_channel_flag,
        "created_playlist": new_playlist is not None,
        "message": (
            f"Added {len(added_ids)} video(s)"
            f"{' (skipped ' + str(len(entries) - len(added_ids)) + ' duplicate(s))' if len(entries) != len(added_ids) else ''}"
            f" to '{channel_name} · {course.title if course else '?'}'. "
            f"Transcripts download in the background (staggered 10s apart)."
        ),
    }


def _staggered_caption_job(video_id: str, delay_seconds: int) -> None:
    """Caption download with a start delay — avoids the YouTube 429
    storm when several videos are bulk-added (2026-09-08)."""
    if delay_seconds > 0:
        import time as _time
        _time.sleep(min(delay_seconds, 300))  # cap at 5 min safety net
    from app.services.youtube_captions_job import _run_caption_download_job
    _run_caption_download_job(video_id=video_id)


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


# ─────────────────────────────────────────────────────────────────────────
# GET /api/admin/llm/budget  (Day 4 — observability)
# ─────────────────────────────────────────────────────────────────────────


@router.get("/llm/budget")
async def admin_get_llm_budget(
    user: dict[str, Any] = Depends(require_capability(Capability.CURATE_CATALOG)),
) -> dict[str, Any]:
    """Read current LLM-call budget / rate-limit state.

    Returns:
      {
        "ollama": {
          "calls_5h": int, "limit_5h": int,
          "calls_week": int, "limit_week": int,
          "near_cap": bool,
          "next_reset_seconds": int
        },
        "alert_pct": 0.9,
        "providers": {
          "groq":   "groq/compound-mini",
          "ollama": "glm-5.2:cloud",
          "openai": "gpt-4o-mini"
        },
        "chains": {
          "free":  ["groq"],
          "paid":  ["ollama", "openai"],
          "admin": ["ollama", "openai"]
        }
      }

    The admin can use this to verify that:
      - Ollama is not near cap (else it'll auto-fallback to OpenAI)
      - The right provider chains are configured per tier
      - The right models are being used per provider

    Capability: CURATE_CATALOG — admins only.
    """
    from app.services.llm_quota import ollama_quota
    from app.config import settings as app_settings

    ollama_usage = ollama_quota.current_usage()

    return {
        "ollama": ollama_usage,
        "alert_pct": app_settings.ollama_quota_alert_pct,
        "providers": {
            "groq": app_settings.llm_model_groq,
            "ollama": app_settings.llm_model_ollama,
            "openai": app_settings.llm_model_openai,
        },
        "chains": {
            "free": app_settings.get_provider_chain(2),  # UserRole.FREE
            "paid": app_settings.get_provider_chain(1),  # UserRole.PAID
            "admin": app_settings.get_provider_chain(0),  # UserRole.ADMIN
        },
    }
