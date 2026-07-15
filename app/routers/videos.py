"""Video router — upload, list, get, and transcribe videos."""

import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response
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
    SMART_PICKS,
    ALL_MODEL_CHOICES,
    LANGUAGE_CHOICES,
    LANGUAGE_LOCKED_CODES,
    MODEL_REGISTRY,
    detect_audio_language,
    get_default_model_choice,
    resolve_model_choice,
    transcript_to_json,
    transcribe_video,
    transcribe_with_backend,
)

router = APIRouter(prefix="/api/videos", tags=["videos"])

# Allowed video extensions
ALLOWED_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
# MVP3.0 item #1: raised from 2 GB → 10 GB. Single 10 GB video at typical
# 2 Mbps video bitrate is ~80-110 min of audio; the user's manual todo
# [jul11] #3 calls this out explicitly ("4 GB doesn't proceed" was the
# 2 GB cap silently rejecting). No uvicorn/Starlette request-body cap
# to bump — Starlette reads the full body into memory by default, so
# 10 GB peaks at ~10 GB RAM during the upload. Tested with a synthetic
# 10 GB upload in test_videos.py::test_upload_accepts_10_gb_file.
MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB


@router.get("/models")
async def list_whisper_models() -> dict[str, Any]:
    """List available Whisper model choices for the web UI dropdown.

    MVP3.0 #2: returns a richer shape than before — the UI now
    groups choices into a manual group (the 4 originals) and a
    smart-picks group (currently just `local-large-turbo` —
    the distil-large-v3 smart picks were removed in MVP2.0.6
    per manualTodo 2.2 because they're English-biased), so it
    can render an optgroup dropdown with labels. The legacy
    `models` field is preserved for any caller that still wants
    a flat list.

    Response shape:
      {
        "choices": [
          {"key": "tiny", "label": "tiny (fastest)", "group": "manual"},
          {"key": "base", "label": "base (default)", "group": "manual"},
          ...
          {"key": "local-large-turbo", "label": "🚀 MLX Whisper Large V3 Turbo (recommended)", "group": "smart"},
        ],
        "default": "local-large-turbo"  (or "base" on Intel / no MLX),
        "models": ["tiny", "base", "small", "medium"],  # legacy flat list
      }
    """
    choices = [
        {
            "key": key,
            "label": entry["label"],
            "group": entry["group"],
        }
        for key, entry in MODEL_REGISTRY.items()
    ]
    return {
        "choices": choices,
        "default": get_default_model_choice(),
        "models": AVAILABLE_MODELS,  # backwards-compat
        # MVP3.0 #2b — language dropdown options (3 for now per
        # user's Q4 answer: auto, en, zh).
        "languages": LANGUAGE_CHOICES,
        "default_language": "auto",
    }


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
        # MVP3.0 #2: stamp the user's choice (or the default) at
        # upload time so the column is never NULL. The schema
        # declares whisper_model as NOT NULL for legacy reasons
        # (old code path had `default="base"` on the column).
        # Once the legacy rows are migrated to NULL, this can
        # be dropped — but setting it here is safe in both cases.
        whisper_model=get_default_model_choice(),
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

    background_tasks.add_task(_run_auto_pipeline, video_id, get_default_model_choice())

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
            # See the single-upload endpoint above for why we
            # always set this (legacy NOT NULL column).
            whisper_model=get_default_model_choice(),
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

        background_tasks.add_task(_run_auto_pipeline, video_id, get_default_model_choice())

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
    language: str = "auto",
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

    # MVP3.0 #2: accept any of the new model-choice keys (manual
    # picks like "base" + smart picks like "local-best-and-fast").
    # The full registry is the single source of truth — see
    # app/services/transcription.py::MODEL_REGISTRY.
    if model_name not in ALL_MODEL_CHOICES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown model choice '{model_name}'. "
                f"Available: {ALL_MODEL_CHOICES}"
            ),
        )

    # MVP3.0 #2b: validate the language arg. "auto" means
    # "let the worker auto-detect from the first 10 min".
    # Anything in LANGUAGE_LOCKED_CODES is a user override.
    # Anything else is a 400 (typo guard).
    if language != "auto" and language not in LANGUAGE_LOCKED_CODES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown language '{language}'. "
                f"Available: ['auto', {sorted(LANGUAGE_LOCKED_CODES)}]"
            ),
        )

    # Resolve the choice to (backend, model_id) NOW so the worker
    # doesn't have to. If the user picked an MLX choice on a Mac
    # that can't run MLX, resolve_model_choice() will fall back to
    # the "local-best-and-fast" entry — and we record that on the
    # video row so the UI can show a "using fallback" warning.
    resolved = resolve_model_choice(model_name)
    actual_model_id = resolved["model_id"]
    fallback_occurred = resolved["fallback_occurred"]
    fallback_reason = resolved["fallback_reason"]

    # Mark the video as "transcribing" and start tracking the job.
    # We store the *user's* choice in `whisper_model` (so the UI
    # can show "you picked X, actually ran Y" if fallback fired)
    # and the resolved model_id in a new field below.
    video.status = "transcribing"
    video.whisper_model = model_name
    video.whisper_backend = resolved["backend"]
    video.whisper_resolved_model = actual_model_id
    video.whisper_fallback_reason = (
        fallback_reason if fallback_occurred else None
    )
    # MVP3.0 #2b: stamp the language override (or leave NULL for
    # "auto" so the worker runs detect_audio_language). The
    # worker reads this column at the start of the job.
    if language != "auto":
        video.language = language
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


def _run_transcribe_job(video_id: str, model_choice: str) -> None:
    """Background worker: transcribe a video and write the result to DB.

    MVP3.0 #2: this worker now accepts a *user choice key* (e.g.
    "base", "local-best-and-fast") instead of a raw model_id. The
    actual dispatch to faster-whisper vs mlx-whisper happens inside
    transcribe_with_backend() based on the resolved entry from
    MODEL_REGISTRY. The previous behaviour (always faster-whisper)
    is preserved for the manual picks (tiny/base/small/medium).

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

        # MVP2.0.4 — stamp transcribe_started_at NOW (BEFORE whisper
        # loads and the model runs). This is the moment this video's
        # transcribe worker actually began work, AFTER any queue wait
        # for prior videos in the same bulk upload. Combined with
        # transcribed_at (stamped at the end), this lets the course
        # page show the true per-video transcribe duration instead of
        # the wall-clock-from-upload time (which previously included
        # the queue wait and made video #34 of a 34-video batch
        # show "36:55" instead of its real ~55s processing time).
        # Re-stamped on every fresh transcribe run (manual retry,
        # etc.) so the duration always reflects the most recent
        # transcribe work for this video.
        video.transcribe_started_at = datetime.now(timezone.utc).replace(tzinfo=None)

        set_progress(
            job, done=5, total=100,
            message=f"Transcribing with '{model_choice}'...",
        )
        db.commit()

        # MVP3.0 #2b (jul 13 2026): anti-drift language lock.
        # If the user didn't pick a language in the UI, the
        # request handler left video.language as NULL — so we
        # auto-detect from the first 10 min here (cheap, ~6-12s
        # on M1 Max mlx-whisper), stamp the result on the video
        # row, and pass it to transcribe_with_backend(). If
        # detection fails (no speechy windows, ffmpeg error, …)
        # we fall back to "unknown" which means "let whisper do
        # its own per-window detection" (the legacy behaviour,
        # with all its drift risks — but at least the job still
        # completes).
        if video.language:
            locked_language = video.language
            detection_info = None
        else:
            set_progress(
                job, done=8, total=100,
                message="Detecting primary language (first 10 min)...",
            )
            db.commit()
            detection_info = detect_audio_language(str(video.file_path))
            # Stamp on the video row so the UI can show
            # "Detected: 中文 (auto, 92% confidence)" on the
            # video page. If detection returned "unknown", we
            # leave the column NULL so the UI knows it was a
            # fallback.
            if detection_info.get("language") and detection_info["language"] != "unknown":
                video.language = detection_info["language"]
                db.commit()
            locked_language = (
                detection_info.get("language")
                if detection_info.get("language") != "unknown"
                else None
            )
        set_progress(
            job, done=10, total=100,
            message=(
                f"Transcribing with '{model_choice}'"
                + (f" (language: {locked_language})" if locked_language else "")
                + "..."
            ),
        )
        db.commit()

        # MVP3.0 #2: dispatch through the new backend-aware entry
        # point. It handles MLX detection, fallback, and
        # backend-specific progress reporting (when the mlx-whisper
        # path is wired up in a follow-up commit). The returned
        # dict has the same shape as before, plus a `_meta` key
        # for diagnostic logging.
        result = transcribe_with_backend(
            str(video.file_path),
            model_choice,
            on_progress=lambda done, total, msg: set_progress(
                job, done=done, total=total, message=msg
            ),
            language=locked_language,
        )
        meta = result.pop("_meta", {})
        segments_buffered = result["segments"]
        language = result["language"]
        duration = result["duration"]

        set_progress(job, done=90, message="Saving transcript to database...")

        # Save transcript as an Asset
        result_dict = {
            "segments": segments_buffered,
            "language": language,
            "duration": duration,
        }
        existing = db.execute(
            select(Asset).where(
                Asset.video_id == video_id, Asset.asset_type == "transcript"
            )
        ).scalar_one_or_none()
        if existing:
            existing.content = transcript_to_json(result_dict)
        else:
            db.add(Asset(
                video_id=video_id,
                asset_type="transcript",
                content=transcript_to_json(result_dict),
            ))

        video.duration = result_dict["duration"]
        # Persist the resolved (backend, model) so the UI can show
        # what actually ran (especially important when fallback
        # happened, e.g. user picked MLX on Intel Mac).
        video.whisper_backend = meta.get("backend")
        video.whisper_resolved_model = meta.get("model_id")
        if not meta.get("fallback_occurred"):
            # Clear any stale fallback reason on success.
            video.whisper_fallback_reason = None
        video.status = "ready"
        # MVP3.0 #8: stamp the transcribe-step completion so the
        # course page can show "ready · in 9:08" by comparing
        # generated_at - created_at once generation finishes. Naive
        # UTC to match the existing created_at column (avoids
        # tzinfo round-trip surprises with SQLite).
        video.transcribed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        finish_job(
            job,
            status="completed",
            message=(
                f"✓ Transcribed {len(segments_buffered)} segments "
                f"(detected: {language}, model: {meta.get('model_id', model_choice)})"
            ),
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


def _run_auto_pipeline(video_id: str, model_name: str | None = None) -> None:
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

    MVP3.0 #2: `model_name` is now a user *choice* key from
    MODEL_REGISTRY (e.g. "base", "local-best-and-fast"), not a raw
    model_id. When None, falls back to get_default_model_choice()
    (MLX smart pick if available, else the faster-whisper smart
    pick, else "base").
    """
    if model_name is None:
        model_name = get_default_model_choice()
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


@router.delete("/{video_id}")
async def delete_video(
    video_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete a video and all of its associated data.

    User asked for this in `doc/manualTodo.txt` item #5 (2026-07-10).
    Useful for cleaning up:
    - 0-byte uploads (the 0-byte video in section 3 of the AI 提示词
      course has been sitting there since 2026-07-09)
    - Failed transcriptions / generations
    - Test videos

    Cascade:
    - Deletes the on-disk file (idempotent — missing file is fine)
    - Deletes all Asset rows for this video (transcript, summary,
      mindmap, flashcards, quiz, topic_timestamps) — handled by
      SQLAlchemy's `cascade="all, delete-orphan"` on the relationship
    - Deletes all ChatSession rows (and their ChatMessages via the
      chat_sessions cascade) — same mechanism
    - The DB-level `ondelete="CASCADE"` on the FKs is a backup if
      SQLAlchemy is bypassed (e.g. raw SQL)

    Returns 200 with a small summary so the UI can confirm what was
    cleaned up. 404 if the video doesn't exist, 403 if the user
    doesn't own the course.

    The video's `status` may be "transcribing" or "generating" when
    delete is called. We allow this — the BackgroundTask worker
    will fail on its next `db.get(Video, ...)` call (the video is
    gone), and the in-memory job stays in the _jobs dict until
    the worker process restarts. Not a problem for MVP1 single-user.
    """
    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Verify ownership (same pattern as GET /api/videos/{id})
    section = db.get(Section, video.section_id)
    course = db.get(Course, section.course_id)
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your video")

    # ── 1. Delete the on-disk file ──────────────────────────────────
    # Use missing_ok=True so a file that's already gone (e.g. admin
    # cleanup, or never written for a 0-byte rejection) doesn't crash
    # the delete. Path is stored as a string in the DB; wrap in Path()
    # so it works whether the original upload was relative or absolute.
    file_path = Path(video.file_path)
    file_deleted = False
    try:
        if file_path.exists():
            file_path.unlink()
            file_deleted = True
    except OSError:
        # Permission denied, file in use, etc. Don't crash the delete —
        # the DB row removal is the critical part.
        file_deleted = False

    # ── 2. Count cascade targets (for the response) ─────────────────
    # We do this BEFORE the delete so we can report the counts.
    # The actual delete is handled by SQLAlchemy's cascade on
    # the relationships.
    assets_count = db.execute(
        select(Asset).where(Asset.video_id == video_id)
    ).scalars().all()
    assets_count_n = len(assets_count)

    from app.models import ChatSession
    sessions_count = db.execute(
        select(ChatSession).where(ChatSession.video_id == video_id)
    ).scalars().all()
    sessions_count_n = len(sessions_count)

    # ── 3. Delete the video (cascades to assets + chat_sessions) ────
    db.delete(video)
    db.commit()

    return {
        "status": "deleted",
        "video_id": video_id,
        "deleted": {
            "file": file_deleted,
            "assets": assets_count_n,
            "chat_sessions": sessions_count_n,
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
        "language": video.language,
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


# IMPORTANT: this route must be declared BEFORE the `/{video_id}/file`
# route below. FastAPI matches routes in declaration order, and the
# generic `/{video_id}/...` pattern would otherwise shadow
# `/{video_id}/transcript/export` with video_id="video_id".
# See doc/Blockers.md "bulk-upload route shadowing" for the pattern
# this guards against — same root cause.
@router.get("/{video_id}/transcript/export")
async def export_transcript(
    video_id: str,
    format: str = "md",
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    """Download the transcript as .md, .json, or .txt.

    Used by the "Download transcript" button on the video page. The
    browser saves the file with `Content-Disposition: attachment`
    so the user gets a real file (not a JSON blob in the browser).

    Args:
        video_id: the video UUID
        format: one of "md", "json", "txt" (default: "md")
    """
    from app.services.transcript_export import (
        VALID_FORMATS,
        export_extension,
        format_transcript,
    )

    if format not in VALID_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid format {format!r}. Must be one of: {', '.join(VALID_FORMATS)}",
        )

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
        raise HTTPException(
            status_code=404,
            detail="Transcript not found. Wait for transcription to finish, then try again.",
        )

    from app.services.transcription import json_to_transcript

    transcript = json_to_transcript(transcript_asset.content)
    body = format_transcript(
        transcript,
        format,
        video_title=video.title,
    )

    # Sanitize the filename: replace characters that browsers /
    # filesystems may not handle. Real filenames from user uploads
    # can have spaces, CJK, dots, etc. — all fine, but `/`, `\`, `:`
    # are reserved on at least one of Windows / macOS / Linux.
    #
    # We also collapse runs of 2+ underscores into a single space.
    # Bilibili (and some other downloaders) auto-rename files with
    # a `_______` separator (e.g. `1.-Foo_______-10-07-2026.mp4`),
    # which looks ugly as a download filename. The DB title is
    # preserved — this transformation only affects the export
    # filename shown to the browser. Use a regex to catch the
    # full run in one go (greedy `_+`).
    import re as _re_underscore
    safe_title = (
        video.title.replace("/", "-")
        .replace("\\", "-")
        .replace(":", "-")
        .replace("\0", "")
    )
    safe_title = _re_underscore.sub(r"_+", " ", safe_title)
    # Trim trailing whitespace so the result doesn't end in a space
    safe_title = safe_title.rstrip()
    ext = export_extension(format)
    filename = f"{safe_title}.{ext}"

    # Content-Type per format. The browser uses this to decide
    # whether to download (attachment) or display inline.
    content_types = {
        "md": "text/markdown; charset=utf-8",
        "json": "application/json; charset=utf-8",
        "txt": "text/plain; charset=utf-8",
    }

    # Build Content-Disposition. The basic `filename="..."` form is
    # restricted to latin-1 by HTTP — non-ASCII characters (CJK,
    # emoji, accented letters) fail to encode. The RFC 5987 form
    # `filename*=UTF-8''<percent-encoded>` is the universal fallback
    # and is supported by every browser since ~2014. We send BOTH
    # so old clients get something usable, modern clients get the
    # full unicode name.
    from urllib.parse import quote
    ascii_fallback = (
        filename.encode("ascii", "replace")
        .decode("ascii")
        .replace("?", "_")
    )
    disposition = (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )

    return Response(
        content=body,
        media_type=content_types[format],
        headers={
            "Content-Disposition": disposition,
        },
    )


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