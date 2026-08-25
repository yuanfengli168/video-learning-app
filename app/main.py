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
from app.routers import admin as admin_router
from app.routers import auth as auth_router
from app.routers import chat as chat_router
from app.routers import courses as courses_router
from app.routers import frontend as frontend_router
from app.routers import generation as generation_router
from app.routers import plugins as plugins_router
from app.routers import videos as videos_router
from app.auth.session import router as session_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables + start the plugin worker pool on startup (MVP1/MVP2.1.0.1)."""
    init_db()
    # MVP2.1.0.1 — start the plugin worker pool. The
    # pool is a module-level singleton (app/workers/
    # plugin_pool.py). Calling start() is idempotent.
    # Without this, plugin runs would never execute
    # (they'd just sit in the queue forever).
    from app.workers.plugin_pool import plugin_pool
    plugin_pool.start()
    yield
    # MVP2.1.0.1 — graceful shutdown. Waits up to 30s
    # for in-flight plugin jobs to finish before the
    # process exits. Best-effort: on hard kill (SIGKILL)
    # the loop is skipped and jobs are lost (acceptable
    # for v1; the DB rows stay as 'running' until the
    # next startup, when a future sweeper could mark
    # them 'failed').
    await plugin_pool.stop(timeout=30.0)


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
app.include_router(admin_router.router)
app.include_router(courses_router.router)
app.include_router(videos_router.router)
app.include_router(generation_router.router)
app.include_router(chat_router.router)
app.include_router(plugins_router.router)
app.include_router(frontend_router.router)


@app.get("/api/health")
async def health_check():
    """Liveness check — is the process up and responding?

    Used by:
      - Day 6: Cloudflare Tunnel health checks (does the upstream
        accept connections?)
      - External uptime monitors (Pingdom, UptimeRobot, etc.)
      - Manual smoke test from terminal:  curl localhost:8000/api/health

    Per k8s liveness-probe semantics: NO dependency checks here.
    If we add DB/Ollama checks, an unhealthy DB would cause gunicorn
    to kill this worker, which doesn't fix the DB. Use /api/ready
    for dependency checks instead.

    Returns 200 always (unless the process is so broken it can't
    respond at all).
    """
    return {
        "status": "ok",
        "app": settings.app_name,
        "server": "gunicorn" if "gunicorn" in __import__("sys").argv[0] else "uvicorn",
    }


@app.get("/api/ready")
async def readiness_check():
    """Readiness check — can the process actually serve traffic?

    Used by:
      - Day 6: load balancers / Cloudflare Tunnel to decide whether
        to route traffic to this worker
      - Onboarding probes: 'is the DB reachable? is Ollama up?'

    Difference from /api/health (liveness):
      - /api/health: 'is the process alive?' — used to kill+restart
      - /api/ready:  'should this process receive traffic?' — used
        to add/remove from the routing pool

    We check 3 things in order of severity:
      1. DB query (SELECT 1) — must succeed; SQLite is local so this
         rarely fails, but on a corrupted DB it will
      2. Ollama reachability — non-fatal (PAID/ADMIN users get an
         error if Ollama is down, but FREE users on Groq still work);
         we report ollama.ok=False but still return 200 if the
         DB is healthy
      3. events table exists (defense — if init_db() didn't run,
         queries will fail)

    Returns 503 only when the DB is unreachable (the only thing
    that makes the process truly unservable). Ollama is reported
    in the body but doesn't change the status code.
    """
    # Use the *module* app.database rather than a from-import, so the
    # tests/conftest.py patch on app_database.SessionLocal takes effect.
    # (A `from app.database import SessionLocal` at module load time would
    # bind to the original SessionLocal, defeating the test patch.)
    from app import database as app_database_module

    db_status = "ok"
    db_error: str | None = None
    ollama_ok = True
    events_table_ok = True

    # 1. DB connectivity
    try:
        db = app_database_module.SessionLocal()
        try:
            from sqlalchemy import text
            db.execute(text("SELECT 1"))
            # 2. events table exists
            db.execute(text("SELECT 1 FROM events LIMIT 1"))
        finally:
            db.close()
    except Exception as exc:
        db_status = "error"
        db_error = f"{type(exc).__name__}: {exc}"
        # If DB is down, we can't trust anything — return 503
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "app": settings.app_name,
                "db": {"status": db_status, "error": db_error},
                "ollama_ok": False,
                "events_table_ok": False,
                "reason": "database_unreachable",
            },
        )

    # 3. Ollama reachability (non-fatal — only matters for PAID/ADMIN)
    try:
        import httpx
        resp = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=2.0)
        ollama_ok = resp.status_code == 200
    except Exception:
        ollama_ok = False

    return {
        "status": "ready",
        "app": settings.app_name,
        "db": {"status": db_status},
        "ollama_ok": ollama_ok,
        "events_table_ok": events_table_ok,
    }