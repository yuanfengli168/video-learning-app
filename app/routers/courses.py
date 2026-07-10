"""Course router — CRUD for courses and sections."""

import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import Course, Section, Video
from app.services.retry import find_failed_generate_videos

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
    user: dict[str, Any] = Depends(get_current_user),
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
) -> dict[str, str]:
    """Delete a course (cascades to sections and videos)."""
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    db.delete(course)
    db.commit()

    return {"status": "deleted"}


# ── Section endpoints ──


@router.post("/{course_id}/sections")
async def create_section(
    course_id: str,
    body: SectionCreate,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
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
    """Re-queue the LLM step for every video in this section whose
    last_generate_job ended in 'failed' state.

    Used by the "Retry all failed" button on the course page. Each
    retry runs as a FastAPI BackgroundTask so the response is
    immediate; the UI polls /status per video to show progress.

    Returns:
        {retried: int, video_ids: [...]} so the UI can show
        "Retrying N videos" and the user can click into any one
        to see the per-video progress.
    """
    section = db.get(Section, section_id)
    if not section or section.course_id != course_id:
        raise HTTPException(status_code=404, detail="Section not found")

    course = db.get(Course, course_id)
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    # Find all failed-generate videos in this section
    all_failed = find_failed_generate_videos(db)
    section_video_ids = {v.id for v in section.videos}
    failed_in_section = [
        row for row in all_failed if row["video_id"] in section_video_ids
    ]

    if not failed_in_section:
        return {"retried": 0, "video_ids": []}

    # Lazy import: the worker uses start_job + the in-memory tracker
    # to know the job is real (otherwise it bails early).
    from app.jobs import start_job, serialize_job
    from app.routers.generation import _run_generate_job

    retried_ids: list[str] = []
    for row in failed_in_section:
        video_id = row["video_id"]
        # Mark the job as "retrying" in the in-memory tracker AND
        # in the DB so the UI can show progress instead of "failed".
        job = start_job(video_id, "generate", message="Retrying via section 'Retry all failed'...")
        video = db.get(Video, video_id)
        if video:
            video.last_generate_job = serialize_job(job)
            video.status = "generating"
            db.commit()
        background_tasks.add_task(_run_generate_job, video_id)
        retried_ids.append(video_id)

    return {
        "retried": len(retried_ids),
        "video_ids": retried_ids,
    }
