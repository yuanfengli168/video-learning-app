"""Frontend router — serves Jinja2 templates for the web UI."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user_optional
from app.config import settings
from app.database import get_db
from app.models import Course, Section, Video

router = APIRouter(tags=["frontend"])

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _ctx(request: Request, user: dict[str, Any] | None, **extra) -> dict[str, Any]:
    """Common template context (without request — passed separately)."""
    ctx = {
        "app_name": settings.app_name,
        "user": user,
        "firebase_config": {
            "apiKey": settings.firebase_api_key,
            "authDomain": settings.firebase_auth_domain,
            "projectId": settings.firebase_project_id,
            "storageBucket": settings.firebase_storage_bucket,
            "messagingSenderId": settings.firebase_messaging_sender_id,
            "appId": settings.firebase_app_id,
        },
    }
    ctx.update(extra)
    return ctx


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: dict[str, Any] | None = Depends(get_current_user_optional),
) -> HTMLResponse:
    """Dashboard / home page."""
    courses = []
    if user:
        courses = db.execute(
            select(Course).where(Course.user_id == user.get("uid", ""))
        ).scalars().all()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        _ctx(request, user, courses=courses),
    )


@router.get("/course/{course_id}", response_class=HTMLResponse)
async def course_view(
    request: Request,
    course_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] | None = Depends(get_current_user_optional),
) -> HTMLResponse:
    """Course view — shows sections and videos."""
    course = db.get(Course, course_id)
    if not course:
        return templates.TemplateResponse(
            request,
            "error.html",
            _ctx(request, user, error="Course not found"),
            status_code=404,
        )

    return templates.TemplateResponse(
        request,
        "course.html",
        _ctx(request, user, course=course),
    )


@router.get("/video/{video_id}", response_class=HTMLResponse)
async def video_view(
    request: Request,
    video_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] | None = Depends(get_current_user_optional),
) -> HTMLResponse:
    """Video player view — the core learning page."""
    video = db.get(Video, video_id)
    if not video:
        return templates.TemplateResponse(
            request,
            "error.html",
            _ctx(request, user, error="Video not found"),
            status_code=404,
        )

    section = db.get(Section, video.section_id)
    course = db.get(Course, section.course_id) if section else None

    return templates.TemplateResponse(
        request,
        "video.html",
        _ctx(request, user, video=video, course=course, section=section),
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    user: dict[str, Any] | None = Depends(get_current_user_optional),
) -> HTMLResponse:
    """Login page with AuthKit."""
    if user:
        # Already logged in — redirect to dashboard
        return templates.TemplateResponse(
            request,
            "redirect.html",
            _ctx(request, user, target="/"),
        )

    return templates.TemplateResponse(
        request,
        "login.html",
        _ctx(request, user),
    )