"""Video router — upload, list, get, and transcribe videos."""

import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.config import settings
from app.database import get_db
from app.models import Asset, Course, Section, Video
from app.services.transcription import (
    AVAILABLE_MODELS,
    transcript_to_json,
    transcribe_video,
)

router = APIRouter(prefix="/api/videos", tags=["videos"])

# Allowed video extensions
ALLOWED_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB


@router.get("/models")
async def list_whisper_models() -> dict[str, list[str]]:
    """List available Whisper models for the web UI dropdown."""
    return {"models": AVAILABLE_MODELS}


@router.post("/upload/{section_id}")
async def upload_video(
    section_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    """Upload a video file to a section.

    Saves the file to the uploads directory and creates a Video record
    with status 'pending'.
    """
    # Validate section exists and belongs to user's course
    section = db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    course = db.get(Course, section.course_id)
    if not course or course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    # Validate file extension
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not allowed. Allowed: {ALLOWED_EXTENSIONS}",
        )

    # Generate unique filename
    video_id = str(uuid.uuid4())
    saved_filename = f"{video_id}{ext}"
    file_path = settings.upload_path / saved_filename

    # Save file
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    file_size = os.path.getsize(file_path)
    if file_size > MAX_FILE_SIZE:
        os.remove(file_path)
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max size: {MAX_FILE_SIZE // (1024**3)} GB",
        )

    # Create video record
    video = Video(
        id=video_id,
        title=Path(file.filename).stem,
        filename=file.filename,
        file_path=str(file_path),
        file_size=file_size,
        section_id=section_id,
        status="pending",
    )
    db.add(video)
    db.commit()

    return {"video_id": video_id, "status": "uploaded"}


@router.post("/{video_id}/transcribe")
async def transcribe(
    video_id: str,
    model_name: str = "base",
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Transcribe a video using Faster-Whisper.

    Stores the transcript as an Asset of type 'transcript'.
    Updates the video status to 'ready' after transcription.
    """
    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Verify ownership
    section = db.get(Section, video.section_id)
    course = db.get(Course, section.course_id)
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your video")

    if model_name not in AVAILABLE_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model. Available: {AVAILABLE_MODELS}",
        )

    # Update status
    video.status = "transcribing"
    video.whisper_model = model_name
    db.commit()

    try:
        result = transcribe_video(video.file_path, model_name)

        # Save transcript as asset
        existing = db.execute(
            select(Asset).where(
                Asset.video_id == video_id, Asset.asset_type == "transcript"
            )
        ).scalar_one_or_none()

        if existing:
            existing.content = transcript_to_json(result)
        else:
            asset = Asset(
                video_id=video_id,
                asset_type="transcript",
                content=transcript_to_json(result),
            )
            db.add(asset)

        video.duration = result["duration"]
        video.status = "ready"
        db.commit()

        return {
            "video_id": video_id,
            "status": "ready",
            "segments": len(result["segments"]),
            "language": result["language"],
            "duration": result["duration"],
        }

    except Exception as exc:
        video.status = "error"
        db.commit()
        raise HTTPException(
            status_code=500, detail=f"Transcription failed: {exc}"
        ) from exc


@router.get("/{video_id}")
async def get_video(
    video_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Get video details including transcript."""
    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Verify ownership
    section = db.get(Section, video.section_id)
    course = db.get(Course, section.course_id)
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your video")

    transcript_asset = db.execute(
        select(Asset).where(
            Asset.video_id == video_id, Asset.asset_type == "transcript"
        )
    ).scalar_one_or_none()

    return {
        "id": video.id,
        "title": video.title,
        "filename": video.filename,
        "status": video.status,
        "duration": video.duration,
        "whisper_model": video.whisper_model,
        "has_transcript": transcript_asset is not None,
    }


@router.get("/{video_id}/transcript")
async def get_transcript(
    video_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Get the transcript for a video."""
    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Verify ownership
    section = db.get(Section, video.section_id)
    course = db.get(Course, section.course_id)
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your video")

    transcript_asset = db.execute(
        select(Asset).where(
            Asset.video_id == video_id, Asset.asset_type == "transcript"
        )
    ).scalar_one_or_none()

    if not transcript_asset:
        raise HTTPException(status_code=404, detail="Transcript not found")

    from app.services.transcription import json_to_transcript

    return json_to_transcript(transcript_asset.content)


@router.get("/{video_id}/file")
async def get_video_file(
    video_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> FileResponse:
    """Serve the video file for playback."""
    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Verify ownership
    section = db.get(Section, video.section_id)
    course = db.get(Course, section.course_id)
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your video")

    file_path = Path(video.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found on disk")

    return FileResponse(str(file_path), media_type="video/mp4")