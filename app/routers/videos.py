"""Video router — upload, list, get, and transcribe videos."""

import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.config import settings
from app.database import SessionLocal, get_db
from app.jobs import (
    finish_job,
    format_eta,
    get_job,
    serialize_job,
    set_progress,
    start_job,
)
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


@router.post("/{video_id}/transcribe", status_code=202)
async def transcribe(
    video_id: str,
    background_tasks: BackgroundTasks,
    model_name: str = "base",
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Kick off transcription in the background. Returns 202 immediately.

    The actual work (loading Whisper, running it on the audio, writing
    the Asset) happens in a FastAPI BackgroundTask — see
    `_run_transcribe_job` below. The UI polls `GET /api/videos/{id}/status`
    to display the progress bar + ETA.

    Returns 202 with the initial job state so the UI can start polling
    right away. The `status` field will be "running" until the job
    completes or fails.
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

    # Mark the video as "transcribing" and start tracking the job.
    video.status = "transcribing"
    video.whisper_model = model_name
    job = start_job(
        video_id,
        "transcribe",
        total=100,
        message=f"Loading Whisper model '{model_name}'...",
    )
    video.last_transcribe_job = serialize_job(job)
    db.commit()

    # Hand the work to FastAPI's BackgroundTasks. It runs AFTER this
    # response is sent, so the user gets an immediate 202 instead of
    # waiting for the (multi-minute) transcription to finish.
    background_tasks.add_task(_run_transcribe_job, video_id, model_name)

    return {
        "video_id": video_id,
        "status": "running",
        "job": job,
    }


def _run_transcribe_job(video_id: str, model_name: str) -> None:
    """Background worker: transcribe a video and write the result to DB.

    This function runs in the SAME process as the API (no worker
    queue yet — MVP1 single-user). It opens its OWN DB session because
    the request's session is closed by the time BackgroundTasks fires.

    Reports progress via app.jobs.set_progress() so the /status
    endpoint can return it to the UI in real time.
    """
    job = get_job(video_id, "transcribe")
    if not job:
        # Defensive: should never happen (we started the job in the
        # request handler), but if it does, fail loudly.
        return

    db = SessionLocal()
    try:
        video = db.get(Video, video_id)
        if not video:
            finish_job(job, status="failed", error="Video disappeared during transcribe")
            video.last_transcribe_job = serialize_job(job) if video else None
            if video:
                db.commit()
            return

        set_progress(job, done=5, total=100, message=f"Transcribing with '{model_name}'...")
        # Throttle DB writes a bit — every segment for a 1-hour video
        # would be thousands of writes. We update every ~50 segments.
        SEGMENT_REPORT_INTERVAL = 50
        segments_buffered = []

        # We don't have a clean way to get the total segment count up
        # front, so we use a different approach: estimate progress by
        # audio duration. Faster-Whisper exposes `info.duration`
        # (total seconds) and the segment `.end` (seconds processed).
        # Update progress as we go.
        from faster_whisper import WhisperModel
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        set_progress(job, done=10, message="Model loaded, decoding audio...")

        segments_iter, info = model.transcribe(
            str(video.file_path), beam_size=5
        )

        # We don't know total segments in advance, so we estimate from
        # info.duration. Use 100% as total and compute "done" as
        # `current_segment.end / info.duration * 100`.
        total_duration = max(info.duration, 1.0)
        for seg in segments_iter:
            segments_buffered.append({
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip(),
            })
            if len(segments_buffered) % SEGMENT_REPORT_INTERVAL == 0:
                pct = min(95, (seg.end / total_duration) * 100)
                set_progress(
                    job,
                    done=int(pct),
                    total=100,
                    message=f"Transcribed {len(segments_buffered)} segments ({pct:.0f}%)...",
                )
                # Persist progress so a page refresh shows it.
                video.last_transcribe_job = serialize_job(job)
                db.commit()

        # Save transcript as an Asset
        result = {
            "segments": segments_buffered,
            "language": info.language,
            "duration": round(info.duration, 2),
        }
        existing = db.execute(
            select(Asset).where(
                Asset.video_id == video_id, Asset.asset_type == "transcript"
            )
        ).scalar_one_or_none()
        if existing:
            existing.content = transcript_to_json(result)
        else:
            db.add(Asset(
                video_id=video_id,
                asset_type="transcript",
                content=transcript_to_json(result),
            ))

        video.duration = result["duration"]
        video.status = "ready"
        finish_job(
            job,
            status="completed",
            message=f"✓ Transcribed {len(segments_buffered)} segments (detected: {result['language']})",
        )
        video.last_transcribe_job = serialize_job(job)
        db.commit()
    except Exception as exc:
        # Mark the video + job as failed so the UI can show an error.
        finish_job(job, status="failed", error=str(exc))
        try:
            video = db.get(Video, video_id)
            if video:
                video.status = "error"
                video.last_transcribe_job = serialize_job(job)
                db.commit()
        except Exception:
            # If even the error-reporting commit fails, swallow it
            # — the job is already marked failed in memory.
            db.rollback()
    finally:
        db.close()


@router.get("/{video_id}/status")
async def get_video_status(
    video_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Poll endpoint for the transcribe / generate progress bars.

    Returns the latest job state for both transcribe and generate so
    the UI can show both progress bars side-by-side. The frontend
    polls this every 1-2 seconds while a job is running.

    The response includes:
    - `video.status` — the persisted Video.status field
      ("pending" | "transcribing" | "generating" | "ready" | "error")
    - `transcribe_job` — the latest transcribe job (or null)
    - `generate_job` — the latest generate job (or null)
    - `eta_text` — pre-formatted ETA strings for the UI
    """
    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Verify ownership
    section = db.get(Section, video.section_id)
    course = db.get(Course, section.course_id)
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your video")

    transcribe_job = get_job(video_id, "transcribe")
    generate_job = get_job(video_id, "generate")

    return {
        "video_id": video_id,
        "video_status": video.status,
        "transcribe_job": transcribe_job,
        "generate_job": generate_job,
        "eta_text": {
            "transcribe": format_eta(transcribe_job["eta_seconds"]) if transcribe_job else None,
            "generate": format_eta(generate_job["eta_seconds"]) if generate_job else None,
        },
    }


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