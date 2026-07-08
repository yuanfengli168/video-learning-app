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
from app.models import Asset, Course, Section, Video
from app.services.markdown import simple_markdown
from markupsafe import Markup

router = APIRouter(tags=["frontend"])

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _md_filter(value: str) -> Markup:
    """Jinja filter that converts markdown to HTML and marks the result
    as safe (skips Jinja's autoescape).

    Why Markup: without it, Jinja sees the filter's return value as a
    plain string and HTML-escapes any `<` and `>` in it, which would
    show the user `<h2>...</h2>` as literal text instead of rendered
    headings. Wrapping in Markup tells Jinja "this string is
    intentionally HTML; do not escape it".

    Safety: the markdown comes from the LLM via /api/generate, which
    is itself user-influenced (the user can pick which video to
    transcribe). To keep this XSS-safe in the future, swap the
    filter for one that runs the result through a sanitizer
    (e.g. bleach) before returning Markup. For MVP1.1 the assumption
    is that the LLM output is trusted (the user's own content) and
    the rendering matches the JS simpleMarkdown byte-for-byte (see
    tests/test_frontend.py::test_simple_markdown_matches_js_implementation).
    """
    return Markup(simple_markdown(value or ""))


# Register the `md` Jinja filter used by video.html to pre-render the
# summary HTML. Mirrors the JS `simpleMarkdown` function in
# app/templates/video.html — the byte-equality test in
# tests/test_frontend.py locks both implementations in lockstep.
templates.env.filters["md"] = _md_filter


def _ctx(
    request: Request,
    user: dict[str, Any] | None,
    db: Session | None = None,
    **extra,
) -> dict[str, Any]:
    """Common template context (without request — passed separately).

    If `db` is provided AND the user is signed in, the user's courses
    are also added to the context so the sidebar can render them on
    every page.
    """
    ctx: dict[str, Any] = {
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
        "sidebar_courses": [],
    }
    if user and db is not None:
        ctx["sidebar_courses"] = (
            db.execute(
                select(Course)
                .where(Course.user_id == user.get("uid", ""))
                .order_by(Course.title.asc())
            )
            .scalars()
            .all()
        )
        # Also expose the user's courses (with sections) for the dashboard
        # upload picker. SQLAlchemy's lazy='select' loads sections on
        # access; we touch them here so the template can render them
        # without an N+1 in the Jinja for-loop.
        for c in ctx["sidebar_courses"]:
            _ = list(c.sections)
    ctx.update(extra)
    return ctx


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: dict[str, Any] | None = Depends(get_current_user_optional),
) -> HTMLResponse:
    """Dashboard / home page.

    The sidebar course list (with sections loaded for the upload
    picker) is built in `_ctx`. We pass it through as `courses` so
    the existing template for-loop works without changes.
    """
    ctx = _ctx(request, user, db=db)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {**ctx, "courses": ctx.get("sidebar_courses", [])},
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
            _ctx(request, user, db=db, error="Course not found"),
            status_code=404,
        )

    return templates.TemplateResponse(
        request,
        "course.html",
        _ctx(request, user, db=db, course=course),
    )


@router.get("/video/{video_id}", response_class=HTMLResponse)
async def video_view(
    request: Request,
    video_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] | None = Depends(get_current_user_optional),
) -> HTMLResponse:
    """Video player view — the core learning page.

    SSR pre-render of the Summary tab (see MVP1.0-PostRelease §
    Optimization #2). When a summary `Asset` exists for this video we
    hand the template the rendered HTML so the user never sees the
    "Generate" button flicker on re-login. When no summary exists we
    pass None and the template renders the Generate button as before.
    """
    video = db.get(Video, video_id)
    if not video:
        return templates.TemplateResponse(
            request,
            "error.html",
            _ctx(request, user, db=db, error="Video not found"),
            status_code=404,
        )

    section = db.get(Section, video.section_id)
    course = db.get(Course, section.course_id) if section else None

    # Look up the summary asset. Limit to one row (we always upsert in
    # `_run_generate_job`) so this is a single-row read.
    summary_asset = db.execute(
        select(Asset).where(
            Asset.video_id == video_id, Asset.asset_type == "summary"
        )
    ).scalar_one_or_none()
    summary_content = summary_asset.content if summary_asset else None

    return templates.TemplateResponse(
        request,
        "video.html",
        _ctx(
            request,
            user,
            db=db,
            video=video,
            course=course,
            section=section,
            summary_content=summary_content,
        ),
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    db: Session = Depends(get_db),
    user: dict[str, Any] | None = Depends(get_current_user_optional),
) -> HTMLResponse:
    """Login page with AuthKit."""
    if user:
        # Already logged in — redirect to dashboard
        return templates.TemplateResponse(
            request,
            "redirect.html",
            _ctx(request, user, db=db, target="/"),
        )

    return templates.TemplateResponse(
        request,
        "login.html",
        _ctx(request, user, db=db),
    )


@router.get("/chat-history", response_class=HTMLResponse)
async def chat_history_page(
    request: Request,
    db: Session = Depends(get_db),
    user: dict[str, Any] | None = Depends(get_current_user_optional),
) -> HTMLResponse:
    """Chat history page — list all past flashcard chat sessions.

    The page is a two-pane layout:
      - Left: list of all the user's chat sessions, with concept, video,
        date, and message count.
      - Right: messages of the selected session, with a composer to
        continue the conversation.

    Both panes are populated client-side via the existing
    `/api/chat/sessions` endpoints.
    """
    return templates.TemplateResponse(
        request,
        "chat_history.html",
        _ctx(request, user, db=db),
    )