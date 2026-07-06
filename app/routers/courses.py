"""Course router — CRUD for courses and sections."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import Course, Section, Video

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