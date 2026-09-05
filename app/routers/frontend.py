"""Frontend router — serves Jinja2 templates for the web UI."""

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.admin import get_user_role_from_db, require_capability
from app.auth.dependencies import get_current_user_optional
from app.auth.roles import Capability, capabilities_for_role
from app.config import settings
from app.database import get_db
from app.models import Asset, Course, Section, Video
from app.models.video import natural_sort_key, natural_sort_key_str
from app.services.catalog import visible_videos_for_user
from app.services.markdown import simple_markdown
from markupsafe import Markup

router = APIRouter(tags=["frontend"])

# auto_reload=True so template changes are picked up immediately during
# dev/test. In production, templates are still cached per-request by
# Starlette's TemplateResponse (which checks mtime).
templates = Jinja2Templates(
    directory=str(Path(__file__).parent.parent / "templates"),
)


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


def _natural_sort_key_filter(title: str) -> tuple[int, str]:
    """Jinja filter wrapper for the model's natural_sort_key.

    The same function is called by app/templates/course.html to compute
    data-sort-key for each video row, so the JS sort in the browser
    uses the same ordering as the server. Both implementations are
    unit-tested — see tests/test_model_video.py and tests/test_frontend.py.
    """
    return natural_sort_key(title)


def _natural_sort_key_str_filter(title: str) -> str:
    """Jinja filter wrapper for natural_sort_key_str — a lexicographically
    sortable string encoding of the same key, ready for data-sort-key."""
    return natural_sort_key_str(title)


templates.env.filters["natural_sort_key"] = _natural_sort_key_filter
templates.env.filters["natural_sort_key_str"] = _natural_sort_key_str_filter


def _format_duration_filter(seconds: float | int | None) -> str:
    """Render a duration in seconds as a human-readable string.

    MVP3.0 #8: used by the course page to show "ready · 9:08" or
    "ready · 1:23:45" beside each video's status badge.

    Format:
      - < 1 minute:  "0:SS"  (e.g. 42 sec → "0:42")
      - < 1 hour:    "M:SS"  (e.g. 9 min 8 sec → "9:08")
      - >= 1 hour:   "H:MM:SS" (e.g. 2 h 5 min 33 sec → "2:05:33")

    Returns "" for None / negative — caller is expected to check.
    """
    if seconds is None or seconds < 0:
        return ""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


templates.env.filters["format_duration"] = _format_duration_filter


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

    `course` (optional, passed via **extra) lets us compute per-video
    flags like `can_regen_materials` for the video page. Catalog videos
    have `course is None` (they live outside any user's course tree);
    owned videos have `course.user_id == viewer_uid`.
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
        # Expose capability set so nav can conditionally show admin links
        # (cheap: one DB lookup per request, cached in _lookup_role_cached)
        role = get_user_role_from_db(user.get("uid", ""), db)
        # Templates use string-based membership checks
        # (`{% if "upload_video" in user_capabilities %}`), so we expose
        # the values rather than the enum objects. Capability values are
        # stable, snake_case strings (see app/auth/roles.py).
        ctx["user_capabilities"] = frozenset(
            c.value for c in capabilities_for_role(role)
        )
        ctx["is_admin"] = Capability.CURATE_CATALOG in capabilities_for_role(role)
        # MVP2.1 (Day 13): expose the role as a string so JS toasts can
        # distinguish "FREE → upgrade" from "PAID → admin-only catalog".
        # Role enum int → name mapping: 0=ADMIN, 1=PAID, 2=FREE.
        ctx["user_role"] = role.name if hasattr(role, "name") else str(role)
        # MVP2.1 (Day 13): explicit flag for transcribe/regenerate buttons
        # so video.html can show a disabled state + upgrade tooltip for
        # FREE users. We could check `user_capabilities` directly in the
        # template, but a named flag is more readable and gives us one
        # place to change if the capability matrix shifts later.
        #
        # Day 13 update (PAID-on-own-only): PAID can regen on videos they
        # OWN (course.user_id == viewer_uid), not on admin-curated catalog
        # videos. Catalog videos are admin-only. ADMIN can regen anywhere.
        # FREE can regen nowhere.
        course = extra.get("course")  # set by video_view; None on dashboard
        if Capability.REGEN_MATERIALS in capabilities_for_role(role):
            if Capability.CURATE_CATALOG in capabilities_for_role(role):
                ctx["can_regen_materials"] = True
            else:
                # PAID tier — only allowed on own videos.
                viewer_uid = user.get("uid", "")
                ctx["can_regen_materials"] = (
                    course is not None and course.user_id == viewer_uid
                )
        else:
            ctx["can_regen_materials"] = False
    else:
        ctx["user_capabilities"] = frozenset()
        ctx["is_admin"] = False
        ctx["can_regen_materials"] = False
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

    Also passes `catalog_videos`: the admin-curated YouTube videos that
    the current user is allowed to see (filtered by visibility based on
    role). For MVP2 the catalog is the primary browse surface.
    """
    ctx = _ctx(request, user, db=db)
    # Limit to 50 most recent — the dashboard is a landing page, not
    # an exhaustive list. Full pagination comes later if needed.
    catalog_videos = db.execute(
        visible_videos_for_user(db, user, limit=50)
    ).scalars().all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            **ctx,
            "courses": ctx.get("sidebar_courses", []),
            "catalog_videos": catalog_videos,
        },
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

    # Day 5 hotfix2: if the video is in 'error' but all required assets
    # exist, flip it to 'ready' so the materials section actually
    # renders. See app/services/video_status.py for the rationale.
    from app.services.video_status import reconcile_video_status
    reconcile_video_status(db, video)

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

    # Available plugins for the Tools tab (MVP2.1.0). The
    # list is a thin dict per plugin (label, description,
    # available, missing deps) so the template can render
    # the buttons without needing a back-reference into
    # the service layer. The full PluginSpec object stays
    # in the service layer.
    from app.services.plugins import list_available_plugins
    import shutil as _shutil

    available_plugins = []
    for spec in list_available_plugins():
        missing = sorted(
            req for req in spec.requires
            if _shutil.which(req) is None
        )
        available_plugins.append(
            {
                "key": spec.key,
                "label": spec.label,
                "description": spec.description,
                "available": len(missing) == 0,
                "missing": missing,
            }
        )

    # Most recent plugin run per plugin_key, for the
    # "Last run" line under each Run button (MVP2.1.0.1).
    # We query once and group in Python — much cheaper
    # than N queries (one per plugin). The result is a
    # dict like {"webm_to_mp4": {id, ok, message,
    # output_path, ...}, ...} so the template can look
    # up by plugin.key. The "last_run" can be None
    # (first-time user, no runs yet).
    from app.models.plugin_run import PluginRun
    last_runs_by_plugin: dict[str, dict] = {}
    for run in (
        db.query(PluginRun)
        .filter(PluginRun.video_id == video.id)
        .order_by(PluginRun.created_at.desc())
        .all()
    ):
        # .all() returns rows in desc order, so the
        # first one we see per plugin_key is the most
        # recent. Skip if we've already recorded one
        # for this plugin (dedup).
        if run.plugin_key in last_runs_by_plugin:
            continue
        last_runs_by_plugin[run.plugin_key] = {
            "id": run.id,
            "ok": run.ok,
            # MVP2.1.0.3 — include the new `status` field
            # (queued / running / done / failed) so the
            # template can distinguish in-progress runs
            # from terminal-failed runs. Without this,
            # a run in 'running' state would render as
            # "Last run failed: Running..." (because
            # ok=False + the worker-set message "Running..."
            # would be the displayed text) — confusing
            # for the user. The template now branches
            # on status first, then on ok.
            "status": run.status,
            "message": run.message,
            "output_path": run.output_path,
            "extra": run.extra_json,
            "created_at": run.created_at.isoformat(),
        }

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
            available_plugins=available_plugins,
            plugin_last_runs=last_runs_by_plugin,
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


@router.get("/usage", response_class=HTMLResponse)
async def usage_page(
    request: Request,
    db: Session = Depends(get_db),
    user: dict[str, Any] | None = Depends(get_current_user_optional),
) -> HTMLResponse:
    """Usage page — how much of the user's AI quota they've consumed.

    2026-09-05 (usage+analytics plan commit 3/6):
      - FREE: plain-words Groq daily claim (15/day, resets midnight UTC).
      - PAID: two bars — 50 requests per rolling 7h window + 100 per
        fixed Mon–Sun week (display-only for the 9/9 soft launch; the
        per-worker in-memory limiter still does the actual enforcement).
    Counts come from the events table (worker-independent truth — see
    app/services/usage.py for why the in-memory trackers can't be
    trusted across gunicorn workers).
    """
    if not user:
        # Not signed in → login (same pattern as chat-history).
        response = templates.TemplateResponse(
            request,
            "login.html",
            _ctx(request, None, db=db),
        )
        response.headers["Location"] = "/login?next=/usage"
        return response

    from app.services.usage import get_user_usage

    uid = user.get("uid", "")
    role = int(user.get("role", 2))  # enrichment guarantees the key;
    # the default matches the pre-enrichment behaviour (FREE).
    usage = get_user_usage(db, uid, role)
    ctx = _ctx(request, user, db=db, usage=usage)
    return templates.TemplateResponse(request, "usage.html", ctx)


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


# Admin-only dep — lazy import to avoid circular import
_admin_capability_dep = require_capability(Capability.CURATE_CATALOG)


@router.get("/admin/upload", response_class=HTMLResponse)
async def admin_upload_page(
    request: Request,
    db: Session = Depends(get_db),
    user: dict[str, Any] | None = Depends(_admin_capability_dep),
) -> HTMLResponse:
    """Admin upload page — paste a YouTube URL, add title + visibility.

    The form POSTs to /api/admin/videos/youtube (JSON), which validates
    the URL, extracts the video ID, and creates a Video row with
    status='pending'.

    Visibility options:
      - public    (0) — anyone, including signed-out
      - paid_only (1) — paid users (any role >= PAID) only
      - admin_only(2) — admins only (for review / debugging)

    Side effect: if the admin has zero Courses/Sections, auto-create a
    "Default Catalog" / "Uncategorized" pair so the Section dropdown on
    the form isn't empty. Idempotent for admins who already have content.
    """
    # Auto-create default Course+Section if admin has none yet. This is
    # the same logic the backend uses on the POST path; sharing it via
    # ensure_admin_has_a_section keeps both call sites in sync.
    from app.services.section_picker import ensure_admin_has_a_section

    ensure_admin_has_a_section(db, user.get("uid", ""))

    return templates.TemplateResponse(
        request,
        "admin_upload.html",
        _ctx(
            request,
            user,
            db=db,
            visibility_options=[
                {"value": 0, "label": "Public — anyone can view"},
                {"value": 1, "label": "Paid only — paid subscribers"},
                {"value": 2, "label": "Admin only — review/debugging"},
            ],
        ),
    )

# ─────────────────────────────────────────────────────────────────────────
# /admin/budget  (Day 4 — observability)
# ─────────────────────────────────────────────────────────────────────────


@router.get("/admin/budget", response_class=HTMLResponse)
async def admin_budget_page(
    request: Request,
    user: dict[str, Any] | None = Depends(_admin_capability_dep),
) -> HTMLResponse:
    """Admin observability: LLM quota, provider chains, models.

    Shows the same data as GET /api/admin/llm/budget but rendered as HTML
    so admins can verify Day 4 governance is working without using curl.
    """
    from app.services.llm_quota import ollama_quota
    from app.config import settings as app_settings

    ollama_usage = ollama_quota.current_usage()
    return templates.TemplateResponse(
        request,
        "admin_budget.html",
        _ctx(
            request,
            user,
            ollama=ollama_usage,
            alert_pct=app_settings.ollama_quota_alert_pct,
            providers={
                "groq": app_settings.llm_model_groq,
                "ollama": app_settings.llm_model_ollama,
                "openai": app_settings.llm_model_openai,
            },
            chains={
                "free": app_settings.get_provider_chain(2),  # UserRole.FREE
                "paid": app_settings.get_provider_chain(1),  # UserRole.PAID
                "admin": app_settings.get_provider_chain(0),  # UserRole.ADMIN
            },
        ),
    )


# ─────────────────────────────────────────────────────────────────────────
# /admin/events  (Day 5 — audit log dashboard)
# ─────────────────────────────────────────────────────────────────────────


@router.get("/admin/events", response_class=HTMLResponse)
async def admin_events_page(
    request: Request,
    db: Session = Depends(get_db),
    user: dict[str, Any] | None = Depends(_admin_capability_dep),
    level: str | None = None,
    source: str | None = None,
    video_id: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    """Admin observability: recent structured events from the events table.

    Read-only view that supports three filters (level, source, video_id)
    and pagination (50 events per page). The events table is written to
    by log_event() in app/utils/events.py — every hot path that
    previously called logger.info/warning now also writes a row here.

    Query params:
      level: INFO | WARNING | ERROR (case-insensitive)
      source: dotted path like 'services.llm_providers'
      video_id: filter to one video
      page: 1-indexed page number
    """
    from app.utils.events import distinct_sources, recent_events

    page = max(1, page)
    page_size = 50
    offset = (page - 1) * page_size

    events = recent_events(
        db,
        level=level,
        source=source,
        video_id=video_id,
        limit=page_size,
        offset=offset,
    )
    sources = distinct_sources(db)

    return templates.TemplateResponse(
        request,
        "admin_events.html",
        _ctx(
            request,
            user,
            events=events,
            sources=sources,
            filters={
                "level": (level or "").upper(),
                "source": source or "",
                "video_id": video_id or "",
            },
            pagination={
                "page": page,
                "page_size": page_size,
                "has_next": len(events) == page_size,
            },
        ),
    )


# ─────────────────────────────────────────────────────────────────────────
# /admin/backups  (Day 10 — backup health dashboard)
# ─────────────────────────────────────────────────────────────────────────
#
# Day 10 incident (2026-08-28): the production DB was wiped silently.
# Root cause #1: launchd backup jobs had been failing with exit 126
# since Aug 22, with nobody watching. This dashboard surfaces that
# state in the UI so a single human can spot it.
#
# Data source: /tmp/video-app-backup-status.json, written every 5 min
# by scripts/backup/backup-probe.sh (launchd: com.videoapp.backup-probe).
# The probe gathers launchd state, backup-file metadata, and RAID
# free-space in one place. See app/services/backup_monitor.py.
#


@router.get("/admin/backups", response_class=HTMLResponse)
async def admin_backups_page(
    request: Request,
    user: dict[str, Any] | None = Depends(_admin_capability_dep),
) -> HTMLResponse:
    """Admin dashboard: backup health snapshot."""
    from app.services.backup_monitor import read_status_file, STATUS_PATH

    status = read_status_file()

    # When the probe has never run (e.g. right after plist load),
    # `status` is None — we still render the page but show a banner.
    probe_never_ran = status is None

    # Format the timestamp for the UI without coupling the template
    # to time formatting details.
    if status is not None:
        latest_age_minutes: float | None = (time.time() - status.probe_ts) / 60.0
    else:
        latest_age_minutes = None

    return templates.TemplateResponse(
        request,
        "admin_backups.html",
        _ctx(
            request,
            user,
            status=status,
            probe_never_ran=probe_never_ran,
            latest_age_minutes=latest_age_minutes,
            status_path=str(STATUS_PATH),
        ),
    )


@router.post("/admin/backups/run", response_class=HTMLResponse)
async def admin_backups_run(
    request: Request,
    user: dict[str, Any] | None = Depends(_admin_capability_dep),
) -> HTMLResponse:
    """Run backup-db.sh synchronously and redirect back to dashboard.

    Why synchronous and not background: launchd does not give the
    web process permission to launch another copy of bash from a
    user-context TCC-restricted path. Running it inline from the
    request thread is fine — it usually takes <2s (just sqlite3
    .backup + integrity_check). We surface stderr in the response
    body if it fails.

    Day 10: this button is the manual escape hatch — if launchd
    isn't running, you can still create a backup from the UI.
    """
    import subprocess
    from pathlib import Path

    script = Path("/Users/jackyli/Desktop/Githubs/video-learning-app/scripts/backup/backup-db.sh")
    if not script.exists():
        return templates.TemplateResponse(
            request,
            "admin_backups_run_result.html",
            _ctx(
                request,
                user,
                ok=False,
                stderr=f"Script not found: {script}",
                stdout="",
            ),
            status_code=500,
        )

    try:
        result = subprocess.run(
            ["/bin/bash", str(script)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        ok = result.returncode == 0
        return templates.TemplateResponse(
            request,
            "admin_backups_run_result.html",
            _ctx(
                request,
                user,
                ok=ok,
                stderr=result.stderr,
                stdout=result.stdout,
            ),
            status_code=200 if ok else 500,
        )
    except subprocess.TimeoutExpired:
        return templates.TemplateResponse(
            request,
            "admin_backups_run_result.html",
            _ctx(
                request,
                user,
                ok=False,
                stderr="Backup timed out after 60s",
                stdout="",
            ),
            status_code=500,
        )
