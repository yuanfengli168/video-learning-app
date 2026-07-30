"""MVP0.2: per-video material selection (the many-to-many join).

Endpoints:
  GET  /api/videos/{video_id}/materials   — current selection + available list
  PUT  /api/videos/{video_id}/materials   — replace selection (idempotent)

The GET response includes BOTH:
  - selected_ids (the user's current selection)
  - available (the full list of materials the user could pick from)
so the picker UI can render in one round-trip.

The PUT body is a list of material_ids; we delete the rows not in the
list and create rows for the new ones. Idempotent: re-PUTting the same
list is a no-op.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import (
    PocketMaterial,
    PocketVideoMaterial,
    Section,
    Video,
)
from app.schemas.material import VideoMaterialLink, VideoMaterialsOut

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/videos", tags=["video-materials"])


class SetVideoMaterialsIn(BaseModel):
    """Body for PUT /api/videos/{video_id}/materials."""

    material_ids: list[str]


@router.get("/{video_id}/materials", response_model=VideoMaterialsOut)
def get_video_materials(
    video_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VideoMaterialsOut:
    """Return the user's current selection + the available pool for this video."""
    video = db.execute(select(Video).where(Video.id == video_id)).scalar_one_or_none()
    if video is None:
        raise HTTPException(status_code=404, detail=f"Video {video_id} not found")

    # Current selection (PocketVideoMaterial rows)
    selected_rows = db.execute(
        select(PocketVideoMaterial)
        .where(PocketVideoMaterial.video_id == video_id)
    ).scalars().all()
    selected_ids = [r.material_id for r in selected_rows]

    # Available pool for this video:
    #   - section-scope materials in this video's section
    #   - video-scope materials for videos in this section (siblings)
    sibling_video_ids_q = select(Video.id).where(Video.section_id == video.section_id)
    available_q = (
        select(PocketMaterial)
        .where(PocketMaterial.user_id == user["uid"])
        .where(
            or_(
                PocketMaterial.section_id == video.section_id,
                PocketMaterial.video_id.in_(sibling_video_ids_q),
            )
        )
        .order_by(PocketMaterial.created_at.desc())
    )
    available_rows = db.execute(available_q).scalars().all()

    available = [
        VideoMaterialLink(
            material_id=r.id,
            filename=r.filename,
            size_bytes=r.size_bytes,
            char_count=r.char_count,
            added_at=r.created_at,
            extraction_method=r.extraction_method,  # MVP0.2 followup
        )
        for r in available_rows
    ]

    return VideoMaterialsOut(
        video_id=video_id,
        selected_ids=selected_ids,
        available=available,
    )


@router.put("/{video_id}/materials", response_model=VideoMaterialsOut)
def set_video_materials(
    video_id: str,
    body: SetVideoMaterialsIn,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VideoMaterialsOut:
    """Replace the user's selection for this video with `material_ids`.

    Idempotent: existing rows not in the new list are deleted; missing
    rows in the new list are created. Available materials not in the
    list are unchanged.

    Validates that all `material_ids` belong to the user.
    """
    video = db.execute(select(Video).where(Video.id == video_id)).scalar_one_or_none()
    if video is None:
        raise HTTPException(status_code=404, detail=f"Video {video_id} not found")

    # Verify ownership of all materials
    if body.material_ids:
        owned = db.execute(
            select(PocketMaterial.id).where(
                PocketMaterial.user_id == user["uid"],
                PocketMaterial.id.in_(body.material_ids),
            )
        ).scalars().all()
        owned_set = set(owned)
        bad = [mid for mid in body.material_ids if mid not in owned_set]
        if bad:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Some materials don't belong to this user: {bad}",
            )

    # Delete existing rows not in the new list
    existing = db.execute(
        select(PocketVideoMaterial).where(PocketVideoMaterial.video_id == video_id)
    ).scalars().all()
    new_set = set(body.material_ids)
    for row in existing:
        if row.material_id not in new_set:
            db.delete(row)

    # Create missing rows
    existing_ids = {r.material_id for r in existing}
    for mid in body.material_ids:
        if mid not in existing_ids:
            db.add(PocketVideoMaterial(video_id=video_id, material_id=mid))

    db.commit()

    # Return the new state (re-use the GET logic)
    return get_video_materials(video_id=video_id, user=user, db=db)
