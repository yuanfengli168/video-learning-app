"""Generation router — trigger LLM generation for a video."""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import Asset, Course, Section, Video
from app.services.llm import generate_materials
from app.services.transcription import json_to_transcript

router = APIRouter(prefix="/api/generate", tags=["generation"])


@router.post("/{video_id}")
async def generate(
    video_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Generate learning materials (summary, mindmap, flashcards, quiz) for a video.

    Requires the video to have a transcript already.
    """
    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Verify ownership
    section = db.get(Section, video.section_id)
    course = db.get(Course, section.course_id)
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your video")

    # Get transcript
    transcript_asset = db.execute(
        select(Asset).where(
            Asset.video_id == video_id, Asset.asset_type == "transcript"
        )
    ).scalar_one_or_none()

    if not transcript_asset:
        raise HTTPException(
            status_code=400,
            detail="No transcript found. Transcribe the video first.",
        )

    transcript = json_to_transcript(transcript_asset.content)

    # Update status
    video.status = "generating"
    db.commit()

    try:
        materials = generate_materials(transcript)

        # Save each asset type
        asset_map = {
            "summary": materials.get("summary", ""),
            "mindmap": materials.get("mindmap", ""),
            "flashcards": json.dumps(materials.get("flashcards", []), ensure_ascii=False),
            "quiz": json.dumps(materials.get("quiz", []), ensure_ascii=False),
        }

        for asset_type, content in asset_map.items():
            existing = db.execute(
                select(Asset).where(
                    Asset.video_id == video_id, Asset.asset_type == asset_type
                )
            ).scalar_one_or_none()

            if existing:
                existing.content = content
            else:
                db.add(Asset(
                    video_id=video_id,
                    asset_type=asset_type,
                    content=content,
                ))

        video.status = "ready"
        db.commit()

        return {
            "video_id": video_id,
            "status": "ready",
            "summary_length": len(asset_map["summary"]),
            "flashcard_count": len(materials.get("flashcards", [])),
            "quiz_count": len(materials.get("quiz", [])),
        }

    except Exception as exc:
        video.status = "error"
        db.commit()
        raise HTTPException(
            status_code=500, detail=f"Generation failed: {exc}"
        ) from exc


@router.get("/{video_id}/assets/{asset_type}")
async def get_asset(
    video_id: str,
    asset_type: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Get a specific generated asset (summary, mindmap, flashcards, quiz)."""
    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Verify ownership
    section = db.get(Section, video.section_id)
    course = db.get(Course, section.course_id)
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your video")

    valid_types = {"summary", "mindmap", "flashcards", "quiz"}
    if asset_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid asset type. Valid: {valid_types}",
        )

    asset = db.execute(
        select(Asset).where(
            Asset.video_id == video_id, Asset.asset_type == asset_type
        )
    ).scalar_one_or_none()

    if not asset:
        raise HTTPException(status_code=404, detail=f"{asset_type} not generated yet")

    # Return structured data for JSON types, raw text for markdown types
    if asset_type in ("flashcards", "quiz"):
        return {"type": asset_type, "data": json.loads(asset.content)}
    else:
        return {"type": asset_type, "data": asset.content}