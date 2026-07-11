"""Generation router — trigger LLM generation for a video."""

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.database import SessionLocal, get_db
from app.jobs import (
    finish_job,
    get_job,
    serialize_job,
    set_progress,
    start_job,
)
from app.models import Asset, Course, Section, Video
from app.services.llm import generate_materials
from app.services.transcription import json_to_transcript

router = APIRouter(prefix="/api/generate", tags=["generation"])


@router.post("/{video_id}", status_code=202)
async def generate(
    video_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Kick off LLM generation in the background. Returns 202 immediately.

    Like /transcribe, this used to block for 30-60s while Ollama ran.
    Now it kicks the work to a FastAPI BackgroundTask and returns
    right away. The UI polls /api/videos/{id}/status to see progress.
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

    # Mark the video as "generating" and start tracking the job.
    video.status = "generating"
    job = start_job(
        video_id,
        "generate",
        total=100,
        message="Starting LLM generation...",
    )
    video.last_generate_job = serialize_job(job)
    db.commit()

    background_tasks.add_task(_run_generate_job, video_id)

    return {
        "video_id": video_id,
        "status": "running",
        "job": job,
    }


def _run_generate_job(video_id: str) -> None:
    """Background worker: call Ollama + write all generated assets to DB."""
    job = get_job(video_id, "generate")
    if not job:
        return

    db = SessionLocal()
    try:
        video = db.get(Video, video_id)
        if not video:
            finish_job(job, status="failed", error="Video disappeared during generate")
            return

        # Load the transcript (re-read since the request session is closed).
        transcript_asset = db.execute(
            select(Asset).where(
                Asset.video_id == video_id, Asset.asset_type == "transcript"
            )
        ).scalar_one_or_none()
        if not transcript_asset:
            finish_job(job, status="failed", error="Transcript disappeared")
            video.status = "error"
            video.last_generate_job = serialize_job(job)
            db.commit()
            return

        transcript = json_to_transcript(transcript_asset.content)

        # Build a progress callback bound to this job.
        def _on_progress(done: int, total: int, message: str) -> None:
            set_progress(job, done=done, total=total, message=message)
            try:
                # Persist progress so a page refresh shows it.
                v = db.get(Video, video_id)
                if v:
                    v.last_generate_job = serialize_job(job)
                    db.commit()
            except Exception:
                db.rollback()

        materials = generate_materials(transcript, on_progress=_on_progress)

        set_progress(job, done=95, message="Saving assets to database...")
        asset_map = {
            "summary": materials.get("summary", ""),
            "mindmap": materials.get("mindmap", ""),
            "flashcards": json.dumps(materials.get("flashcards", []), ensure_ascii=False),
            "quiz": json.dumps(materials.get("quiz", []), ensure_ascii=False),
            "topic_timestamps": json.dumps(
                materials.get("topic_timestamps", []), ensure_ascii=False
            ),
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
        # MVP3.0 #8: stamp the generate-step completion so the
        # course page can show "ready · in 9:08" by subtracting
        # video.created_at from this timestamp. Naive UTC to match
        # the created_at column (consistent with how
        # transcribed_at is set in videos._run_transcribe_job).
        video.generated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        finish_job(
            job,
            status="completed",
            message=f"✓ Generated {len(materials.get('flashcards', []))} flashcards, "
                    f"{len(materials.get('quiz', []))} quiz questions, "
                    f"{len(materials.get('topic_timestamps', []))} topic timestamps",
        )
        video.last_generate_job = serialize_job(job)
        db.commit()
    except Exception as exc:
        finish_job(job, status="failed", error=str(exc))
        try:
            video = db.get(Video, video_id)
            if video:
                video.status = "error"
                video.last_generate_job = serialize_job(job)
                db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


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

    valid_types = {"summary", "mindmap", "flashcards", "quiz", "topic_timestamps"}
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
    if asset_type in ("flashcards", "quiz", "topic_timestamps"):
        return {"type": asset_type, "data": json.loads(asset.content)}
    else:
        return {"type": asset_type, "data": asset.content}