"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.middleware import SecurityHeadersMiddleware
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