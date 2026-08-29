"""Course router — CRUD for courses and sections."""

import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.admin import require_capability
from app.auth.dependencies import get_current_user
from app.auth.roles import Capability
from app.config import settings
from app.database import get_db
from app.models import Asset, Course, Section, Video
from app.services.retry import (
    find_failed_generate_videos,
    find_failed_transcribe_videos,
)

router = APIRouter(prefix="/api/courses", tags=["courses"])


# ── Schemas ──


class CourseCreate(BaseModel):
    title: str
    description: str = ""


class CourseUpdate(BaseModel):
    title: str | None = None
    description: str | None = None


class SectionCreate(BaseModel):
    title: str
    order_index: int = 0


# ── Course endpoints ──


@router.get("")
async def list_courses(
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List all courses for the current user."""
    courses = db.execute(
        select(Course).where(Course.user_id == user.get("uid", ""))
    ).scalars().all()

    return [
        {
            "id": c.id,
            "title": c.title,
            "description": c.description,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in courses
    ]


@router.post("")
async def create_course(
    body: CourseCreate,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(require_capability(Capability.MANAGE_OWN_COURSE)),
) -> dict[str, str]:
    """Create a new course."""
    course = Course(
        id=str(uuid.uuid4()),
        title=body.title,
        description=body.description,
        user_id=user.get("uid", ""),
    )
    db.add(course)
    db.commit()

    return {"course_id": course.id}


@router.get("/{course_id}")
async def get_course(
    course_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Get a course with its sections."""
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    return {
        "id": course.id,
        "title": course.title,
        "description": course.description,
        "sections": [
            {
                "id": s.id,
                "title": s.title,
                "order_index": s.order_index,
                "video_count": len(s.videos),
            }
            for s in course.sections
        ],
    }


@router.put("/{course_id}")
async def update_course(
    course_id: str,
    body: CourseUpdate,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    """Update a course."""
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    if body.title is not None:
        course.title = body.title
    if body.description is not None:
        course.description = body.description
    db.commit()

    return {"status": "updated"}


@router.delete("/{course_id}")
async def delete_course(
    course_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete a course and everything in it (cascades to sections,
    videos, assets, chat sessions).

    MVP2.0 — mirrors the video delete pattern. The DB-level cascade
    is already wired up via `ondelete="CASCADE"` on the FKs and
    `cascade="all, delete-orphan"` on the relationships, so deleting
    a course row at the DB level cleans up everything below it.
    The on-disk files are NOT cleaned up by the DB cascade — we
    walk the tree manually first and unlink each one (idempotent:
    missing files are fine).

    Returns 200 with a cascade summary so the UI can confirm
    what was deleted.

    Soft-delete with a trash folder / 30-day restore is a separate
    feature (manualTodo #8) — deferred to MVP3.
    """
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    # Walk the tree, unlink on-disk files, and count cascade targets.
    # We do the file cleanup BEFORE the DB delete so we can still
    # see the file_path strings on each Video row.
    files_deleted = 0
    files_missing = 0
    total_videos = 0
    total_assets = 0

    for section in course.sections:
        for video in section.videos:
            total_videos += 1
            total_assets += len(db.execute(
                select(Asset).where(Asset.video_id == video.id)
            ).scalars().all())
            # Unlink the on-disk file. Idempotent — a missing file
            # is not an error (e.g. admin cleanup, 0-byte rejection).
            file_path = Path(video.file_path)
            try:
                if file_path.exists():
                    file_path.unlink()
                    files_deleted += 1
                else:
                    files_missing += 1
            except OSError:
                files_missing += 1

    # Count chat sessions across all videos in the course
    video_ids = [v.id for s in course.sections for v in s.videos]
    total_sessions = 0
    if video_ids:
        from app.models import ChatSession
        total_sessions = len(db.execute(
            select(ChatSession).where(ChatSession.video_id.in_(video_ids))
        ).scalars().all())

    # Now actually delete — SQLAlchemy cascade handles the DB side
    db.delete(course)
    db.commit()

    return {
        "status": "deleted",
        "course_id": course_id,
        "deleted": {
            "files": files_deleted,
            "files_missing": files_missing,
            "videos": total_videos,
            "assets": total_assets,
            "chat_sessions": total_sessions,
        },
    }


@router.delete("/{course_id}/sections/{section_id}")
async def delete_section(
    course_id: str,
    section_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete a section and all its videos (cascades to assets + chat).

    MVP2.0 — same pattern as delete_course and delete_video.
    Returns 200 with a cascade summary. 404 if the section doesn't
    exist or doesn't belong to the course; 403 if the user doesn't
    own the course.

    Soft-delete deferred to MVP3 (manualTodo #8).
    """
    section = db.get(Section, section_id)
    if not section or section.course_id != course_id:
        raise HTTPException(
            status_code=404,
            detail="Section not found",
        )
    course = db.get(Course, course_id)
    if not course or course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    # Walk the section's videos, unlink files, count cascade targets
    files_deleted = 0
    files_missing = 0
    total_videos = len(section.videos)
    total_assets = 0
    for video in section.videos:
        total_assets += len(db.execute(
            select(Asset).where(Asset.video_id == video.id)
        ).scalars().all())
        file_path = Path(video.file_path)
        try:
            if file_path.exists():
                file_path.unlink()
                files_deleted += 1
            else:
                files_missing += 1
        except OSError:
            files_missing += 1

    # Count chat sessions for these videos
    video_ids = [v.id for v in section.videos]
    total_sessions = 0
    if video_ids:
        from app.models import ChatSession
        total_sessions = len(db.execute(
            select(ChatSession).where(ChatSession.video_id.in_(video_ids))
        ).scalars().all())

    db.delete(section)
    db.commit()

    return {
        "status": "deleted",
        "section_id": section_id,
        "deleted": {
            "files": files_deleted,
            "files_missing": files_missing,
            "videos": total_videos,
            "assets": total_assets,
            "chat_sessions": total_sessions,
        },
    }


# ── Section endpoints ──


@router.post("/{course_id}/sections")
async def create_section(
    course_id: str,
    body: SectionCreate,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(require_capability(Capability.MANAGE_OWN_COURSE)),
) -> dict[str, str]:
    """Create a section in a course."""
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    section = Section(
        id=str(uuid.uuid4()),
        title=body.title,
        order_index=body.order_index,
        course_id=course_id,
    )
    db.add(section)
    db.commit()

    return {"section_id": section.id}


@router.get("/{course_id}/sections/{section_id}/videos")
async def list_section_videos(
    course_id: str,
    section_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List videos in a section."""
    section = db.get(Section, section_id)
    if not section or section.course_id != course_id:
        raise HTTPException(status_code=404, detail="Section not found")

    course = db.get(Course, course_id)
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    return [
        {
            "id": v.id,
            "title": v.title,
            "status": v.status,
            "duration": v.duration,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in section.videos
    ]

@router.post("/{course_id}/sections/{section_id}/retry-failed")
async def retry_failed_section_videos(
    course_id: str,
    section_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Re-queue the failed step (transcribe OR generate) for every
    video in this section whose status is 'error'.

    A video can fail in two places:
    - last_transcribe_job.status='failed' (e.g. 0-byte file,
      unsupported codec) — needs re-transcribing.
    - last_generate_job.status='failed' (e.g. LLM empty body,
      JSON parse error) — needs re-generating.

    We re-queue whichever step failed. For transcribe failures, the
    full auto-pipeline (transcribe → generate) runs again because
    that's what `_run_transcribe_job` does after a fresh upload.
    For generate-only failures, we just call `_run_generate_job`.

    Used by the "Retry all failed" button on the course page. Each
    retry runs as a FastAPI BackgroundTask so the response is
    immediate; the UI polls /status per video to show progress.

    Returns:
        {retried: int, video_ids: [...], transcribe_retried: int,
         generate_retried: int} so the UI can show a useful
        summary like "Retrying 4 videos (3 transcribe, 1 generate)".
    """
    section = db.get(Section, section_id)
    if not section or section.course_id != course_id:
        raise HTTPException(status_code=404, detail="Section not found")

    course = db.get(Course, course_id)
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    # Find failed videos in this section, partitioned by which step
    # failed. A video that has BOTH transcribe and generate failed
    # only goes into the transcribe bucket — re-running transcribe
    # auto-pipelines to generate, so the generate job will be
    # re-run as a side effect.
    all_transcribe_failed = find_failed_transcribe_videos(db)
    all_generate_failed = find_failed_generate_videos(db)

    section_video_ids = {v.id for v in section.videos}
    transcribe_failed = {
        row["video_id"] for row in all_transcribe_failed
        if row["video_id"] in section_video_ids
    }
    generate_failed = {
        row["video_id"] for row in all_generate_failed
        if row["video_id"] in section_video_ids
        # Skip videos that also have a failed transcribe job — the
        # transcribe retry will handle them.
        and row["video_id"] not in transcribe_failed
    }

    if not transcribe_failed and not generate_failed:
        return {
            "retried": 0,
            "transcribe_retried": 0,
            "generate_retried": 0,
            "video_ids": [],
        }

    # Lazy import: the worker uses start_job + the in-memory tracker
    # to know the job is real (otherwise it bails early).
    from app.jobs import start_job, serialize_job
    from app.routers.generation import _run_generate_job
    from app.routers.videos import _run_transcribe_job

    retried_ids: list[str] = []

    # 1. Re-run transcribe for videos whose transcribe step failed.
    #    `_run_transcribe_job` chains into `_run_generate_job`
    #    automatically (it's the same code path as a fresh upload).
    for video_id in transcribe_failed:
        job = start_job(
            video_id, "transcribe",
            message="Retrying transcribe (then generate) via 'Retry all failed'...",
        )
        video = db.get(Video, video_id)
        if video:
            video.last_transcribe_job = serialize_job(job)
            video.status = "transcribing"
            db.commit()
        background_tasks.add_task(_run_transcribe_job, video_id, "base")
        retried_ids.append(video_id)

    # 2. Re-run generate for videos whose generate step failed
    #    (but transcribe already succeeded — the transcript is in
    #    the DB and just needs the LLM step again).
    for video_id in generate_failed:
        job = start_job(
            video_id, "generate",
            message="Retrying generate via 'Retry all failed'...",
        )
        video = db.get(Video, video_id)
        if video:
            video.last_generate_job = serialize_job(job)
            video.status = "generating"
            db.commit()
        # MVP2.1 patch: pass user_id + user_role. Earlier this was
        # called with only (video_id,) which silently failed inside
        # BackgroundTasks (TypeError swallowed). Symptom: status
        # stuck at 'generating', no LLM call, no error event.
        background_tasks.add_task(
            _run_generate_job,
            video_id,
            user.get("uid", ""),
            user.get("role", 2),
        )
        retried_ids.append(video_id)

    return {
        "retried": len(retried_ids),
        "transcribe_retried": len(transcribe_failed),
        "generate_retried": len(generate_failed),
        "video_ids": retried_ids,
    }
