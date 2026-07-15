"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.database import init_db
from app.middleware import SecurityHeadersMiddleware
from app.middleware_session import SessionExpiryMiddleware
from app.routers import auth as auth_router
from app.routers import chat as chat_router
from app.routers import courses as courses_router
from app.routers import frontend as frontend_router
from app.routers import generation as generation_router
from app.routers import videos as videos_router
from app.auth.session import router as session_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables on startup (MVP1)."""
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
)


# ── Global exception handlers (MVP2.0.5) ───────────────────────────────────
# Defense-in-depth: ensure every error response is JSON, never plain
# text. Without these, an uncaught exception in a route handler (e.g.
# a body-parser error that bubbles past the route) returns a 500 with
# a plain-text body, which the frontend's fetch().json() can't parse
# — the user sees a cryptic "Unexpected token..." error instead of
# the actual problem.
#
# The h11-level 400s ("Invalid HTTP request received.") are caught
# by uvicorn BEFORE FastAPI sees the request, so this handler doesn't
# help with those — for those, the fix is the bumped
# `h11_max_incomplete_event_size` in start.sh (64 MB). But for any
# other unexpected exception during request processing, this handler
# turns it into a clean JSON 500.
#
# See doc/MVP2.0-Status.md §19 for the full postmortem (the user
# reported "bulk upload fails error when parsing the body" which was
# actually a JSON-parse error on a plain-text 400 response).
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Wrap Starlette's HTTPException in a JSON response.

    FastAPI's default handler already returns JSON for HTTPException
    raised inside a route, but this custom handler also covers:
    - HTTPException raised by middleware (e.g. SessionExpiryMiddleware
      in the future if it ever raises instead of returning a Response)
    - HTTPException raised by dependencies or background tasks
    - Any place where FastAPI's default would emit a plain-text body

    The body shape matches FastAPI's default: `{"detail": "..."}` so
    frontend code that does `data.detail` keeps working.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=exc.headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all for any exception that escapes a route handler.

    Without this, FastAPI returns a 500 with a plain-text body (or,
    in debug mode, an HTML traceback) which the frontend's
    fetch().json() can't parse. With this, the user always gets a
    clean JSON error they can see in the UI.

    Logs the exception server-side so we can still debug.
    """
    import logging
    import traceback

    logger = logging.getLogger("uvicorn.error")
    logger.error(
        "Unhandled exception in %s %s: %s\n%s",
        request.method,
        request.url.path,
        exc,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": (
                f"Internal server error: {type(exc).__name__}. "
                "Check the server log for the full traceback."
            ),
        },
    )

# Serve static assets (e.g. app/static/js/transcript-follow.js for the
# transcript auto-scroll experiment — see MVP1.0-PostRelease § Optimization #1).
# Files under app/static/ are mounted at /static/*. No auth: these are
# read-only static assets that the templates reference by path.
_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Security headers — applied to every response (API + Jinja2 + static).
# See app/middleware.py for the full list. DEBUG is passed in so HSTS
# is never set on the http://localhost dev server.
#
# Note on middleware order: Starlette applies middleware in REVERSE
# order of registration — the LAST one added is the OUTERMOST. We
# register SecurityHeadersMiddleware AFTER SessionExpiryMiddleware
# so the security headers wrap the redirect response too. If a
# request hits an expired cookie, SessionExpiryMiddleware returns
# a 302; the response then bubbles up through SecurityHeadersMiddleware
# which adds CSP/X-Frame-Options/etc. before sending it to the
# browser. Without this ordering, the 302 would be sent without
# any security headers.
app.add_middleware(SessionExpiryMiddleware)
app.add_middleware(
    SecurityHeadersMiddleware,
    debug=settings.debug,
)

app.include_router(auth_router.router)
app.include_router(session_router)
app.include_router(courses_router.router)
app.include_router(videos_router.router)
app.include_router(generation_router.router)
app.include_router(chat_router.router)
app.include_router(frontend_router.router)


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "app": settings.app_name}