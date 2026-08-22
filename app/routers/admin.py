"""Admin router — endpoints gated by Capability checks.

All routes here require a specific capability (not just role=ADMIN),
so future roles like support_admin can access read-only ones without
curating videos. See doc/mvp2-roles-and-access.md.

Endpoints:

POST /api/admin/videos/youtube
  Body: { url, title, description?, visibility }
  Capability: CURATE_CATALOG
  Effect: extracts YouTube ID, creates Video row, returns video_id.

  Day 2A: no YouTube API call (YOUTUBE_API_KEY comes later).
    Just stores the URL-derived ID + admin-provided title.
  Day 2B: will fetch real title/duration/thumbnail/captions from
    YouTube Data API v3 (deferred — needs API key).

GET /api/admin/videos/pending
  Capability: CURATE_CATALOG
  Effect: list videos that haven't finished processing yet.

GET /api/admin/dashboard
  Capability: VIEW_ADMIN_DASHBOARD
  Effect: admin stats (video count, user count, etc).
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.auth.admin import (
    ensure_user_row,
    get_user_role_from_db,
    require_capability,
)
from app.auth.dependencies import get_current_user
from app.auth.roles import Capability, UserRole, VideoVisibility
from app.database import get_db
from app.models import Video
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
    """Response body after successfully adding a YouTube video."""

    video_id: str
    youtube_id: str
    title: str
    visibility: int
    visibility_name: str


# ─────────────────────────────────────────────────────────────────────────
# POST /api/admin/videos/youtube
# ─────────────────────────────────────────────────────────────────────────


@router.post("/videos/youtube", response_model=YouTubeVideoResponse)
async def admin_add_youtube_video(
    body: YouTubeVideoCreate,
    user: dict[str, Any] = Depends(require_capability(Capability.CURATE_CATALOG)),
    db: Session = Depends(get_db),
) -> YouTubeVideoResponse:
    """Add a YouTube video to the catalog (admin only).

    Flow:
      1. Extract YouTube ID from the URL (any supported format).
      2. Validate the extracted ID (must be exactly 11 chars).
      3. Ensure user row exists (auto-create on first login).
      4. Insert Video row with status='pending' (admin added it,
         but processing — caption fetch, embed preview — happens later).
      5. Return the new video_id + youtube_id for the admin UI to
         link to the video detail page.

    Day 2A: no YouTube API call (YOUTUBE_API_KEY comes later). We just
    store the URL-derived ID + admin-provided title. The actual title
    on YouTube is ignored for now; admin types it in.

    Security:
      - Capability check enforced server-side (no client-side trust)
      - Parameterized SQL via SQLAlchemy ORM (no injection)
      - URL is parsed via regex, NOT evaluated (no SSRF risk)
      - YouTube ID is strictly validated to 11 chars
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

    # Step 4: Create the Video row
    # Note: status='pending' — we don't actually fetch the captions yet
    # (Day 2B with YouTube API). The MVP2 admin can paste URLs at any
    # pace; processing happens in the background later.
    video = Video(
        title=body.title,
        # YouTube ID stored in its own column (added in migration)
        youtube_id=youtube_id,
        # Visibility (PUBLIC/PAID_ONLY/ADMIN_ONLY) from request
        visibility=body.visibility,
        # status='pending' until processing completes (future)
        status="pending",
        # Pre-pivot: filename/file_path/file_size were required.
        # For YouTube-typed videos these are NULL/dummy.
        # We fill filename with the youtube_id (so legacy queries don't break)
        # and file_path with the canonical watch URL.
        filename=f"youtube:{youtube_id}",
        file_path=f"https://www.youtube.com/watch?v={youtube_id}",
        file_size=0,
    )
    # section_id required (NOT NULL FK). We put the new video in the
    # first Section of the first Course (admin "scratch" location)
    # until proper Section assignment is added to the UI.
    # TODO (Day 3+): let admin pick a Course+Section when adding.
    from app.models import Course, Section  # local import to avoid cycle
    first_section = db.query(Section).order_by(Section.id).first()
    if first_section is None:
        # No sections exist — create a default "Uncategorized" course
        # + section so the video has a valid FK target.
        course = Course(title="Uncategorized", user_id=uid)
        db.add(course)
        db.flush()
        first_section = Section(
            title="Uncategorized",
            course_id=course.id,
            order_index=0,
        )
        db.add(first_section)
        db.flush()

    video.section_id = first_section.id

    db.add(video)
    db.commit()
    db.refresh(video)

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
    )
