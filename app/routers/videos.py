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


@router.post("/upload/{section_id}", status_code=202)
async def upload_video(
    section_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Upload a video and queue auto-transcribe + auto-generate.

    Saves the file to the uploads directory, creates a Video record
    with status 'queued', and immediately chains transcribe → generate
    as a BackgroundTask. Returns 202 with the video_id so the UI
    can redirect to the video page and start polling /status.

    MVP2.0 #1: auto-pipeline (always ON, default Whisper model 'base').
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
    if file_size == 0:
        # Edge case: browser sent an empty file (cancelled upload, network
        # reset, or the user picked the wrong file). Without this check
        # we'd happily save a 0-byte .webm and then the auto-pipeline
        # would crash with "[Errno 1094995529] Invalid data found when
        # processing input" because Whisper can't decode empty audio.
        # Discovered 2026-07-09 when 1 of 30 bulk-uploaded videos was
        # 0 bytes — see doc/Blockers.md.
        os.remove(file_path)
        raise HTTPException(
            status_code=400,
            detail="File is empty (0 bytes). The upload may have been cancelled or the source file is broken.",
        )
    if file_size > MAX_FILE_SIZE:
        os.remove(file_path)
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max size: {MAX_FILE_SIZE // (1024**3)} GB",
        )

    # Create video record and queue auto-pipeline
    video = Video(
        id=video_id,
        title=Path(file.filename).stem,
        filename=file.filename,
        file_path=str(file_path),
        file_size=file_size,
        section_id=section_id,
        status="queued",
    )
    # Start the transcribe job tracker so the UI can poll /status
    # immediately after the upload completes, before the background
    # task has a chance to update it.
    transcribe_job = start_job(
        video_id,
        "transcribe",
        total=100,
        message="Queued for auto-processing (transcribe → generate)...",
    )
    video.last_transcribe_job = serialize_job(transcribe_job)
    db.add(video)
    db.commit()

    background_tasks.add_task(_run_auto_pipeline, video_id, "base")

    return {"video_id": video_id, "status": "queued", "auto_process": True}


@router.post("/upload-bulk/{section_id}")
async def upload_bulk_videos(
    section_id: str,
    files: list[UploadFile],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Upload multiple videos at once and queue auto-processing for each.

    Per-file 2 GB cap — files that exceed it are skipped with a warning;
    other files in the batch continue processing. The response lists
    every file's outcome so the UI can show per-file status.

    MVP2.0 #3: multi-file / non-blocking upload.

    NOTE on route ordering: this route is intentionally registered
    BEFORE `/{video_id}/transcribe` below. FastAPI matches routes in
    declaration order — if this came after `/{video_id}/transcribe`,
    a request to `/upload-bulk/<section_id>` would be shadowed by
    `/{video_id}/transcribe` with video_id="upload-bulk" and 404.
    See doc/Blockers.md for the postmortem.
    """
    section = db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    course = db.get(Course, section.course_id)
    if not course or course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    results: list[dict[str, Any]] = []
    queued = 0
    skipped = 0

    for upload_file in files:
        filename = upload_file.filename or ""
        ext = Path(filename).suffix.lower()

        if ext not in ALLOWED_EXTENSIONS:
            results.append({
                "filename": filename,
                "status": "skipped",
                "error": f"File type '{ext}' not allowed. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
            })
            skipped += 1
            continue

        video_id = str(uuid.uuid4())
        saved_filename = f"{video_id}{ext}"
        file_path = settings.upload_path / saved_filename

        try:
            with open(file_path, "wb") as buf:
                shutil.copyfileobj(upload_file.file, buf)
        except Exception as exc:
            results.append({
                "filename": filename,
                "status": "skipped",
                "error": f"Failed to save file: {exc}",
            })
            skipped += 1
            continue

        file_size = os.path.getsize(file_path)
        if file_size == 0:
            # 0-byte file — same edge case as in upload_video. Skipped
            # rather than 400 because bulk upload reports per-file
            # outcomes. See doc/Blockers.md for context.
            os.remove(file_path)
            results.append({
                "filename": filename,
                "status": "skipped",
                "error": "File is empty (0 bytes). Upload may have been cancelled or the source file is broken.",
            })
            skipped += 1
            continue
        if file_size > MAX_FILE_SIZE:
            os.remove(file_path)
            gb = file_size / (1024 ** 3)
            results.append({
                "filename": filename,
                "status": "skipped",
                "error": f"File too large ({gb:.1f} GB). Max: {MAX_FILE_SIZE // (1024 ** 3)} GB",
            })
            skipped += 1
            continue

        video = Video(
            id=video_id,
            title=Path(filename).stem,
            filename=filename,
            file_path=str(file_path),
            file_size=file_size,
            section_id=section_id,
            status="queued",
        )
        transcribe_job = start_job(
            video_id,
            "transcribe",
            total=100,
            message="Queued for auto-processing (transcribe → generate)...",
        )
        video.last_transcribe_job = serialize_job(transcribe_job)
        db.add(video)
        db.commit()

        background_tasks.add_task(_run_auto_pipeline, video_id, "base")

        results.append({
            "filename": filename,
            "video_id": video_id,
            "status": "queued",
        })
        queued += 1

    return {
        "results": results,
        "total": len(files),
        "queued": queued,
        "skipped": skipped,
    }


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


def _run_auto_pipeline(video_id: str, model_name: str = "base") -> None:
    """Background chain: transcribe → generate for a newly uploaded video.

    MVP2.0 #1: called as a BackgroundTask from upload_video. The
    transcribe job is already registered in the DB before this runs
    (so the UI can poll /status immediately). This function:
      1. Calls _run_transcribe_job (same module) — sets up Whisper,
         writes the transcript Asset, marks the job completed.
      2. If transcription succeeded, sets up the generate job tracker
         and calls _run_generate_job (lazy-imported from generation.py
         to avoid coupling at module load time).

    Runs in the same process (BackgroundTasks, no worker queue yet —
    that's #11 / MVP2.0.3).
    """
    # Step 1: Run transcription. The job was already started in
    # upload_video, so _run_transcribe_job can find it via get_job().
    _run_transcribe_job(video_id, model_name)

    # Check if transcription succeeded before attempting generation.
    transcribe_job = get_job(video_id, "transcribe")
    if not transcribe_job or transcribe_job.get("status") != "completed":
        return  # Transcription failed — skip generation

    # Step 2: Set up the generate job tracker and hand off to the worker.
    db = SessionLocal()
    try:
        video = db.get(Video, video_id)
        if not video:
            return
        transcript_asset = db.execute(
            select(Asset).where(
                Asset.video_id == video_id, Asset.asset_type == "transcript"
            )
        ).scalar_one_or_none()
        if not transcript_asset:
            return  # Transcript not found — should not happen after a completed transcribe

        video.status = "generating"
        gen_job = start_job(
            video_id,
            "generate",
            total=100,
            message="Auto-pipeline: starting LLM generation...",
        )
        video.last_generate_job = serialize_job(gen_job)
        db.commit()
    finally:
        db.close()

    # Lazy import avoids coupling videos.py to generation.py at module
    # load time while still sharing the implementation cleanly.
    from app.routers.generation import _run_generate_job
    _run_generate_job(video_id)

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