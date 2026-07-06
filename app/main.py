"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.database import init_db
from app.routers import auth as auth_router
from app.routers import chat as chat_router
from app.routers import courses as courses_router
from app.routers import frontend as frontend_router
from app.routers import generation as generation_router
from app.routers import videos as videos_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables on startup (MVP1)."""
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
)

app.include_router(auth_router.router)
app.include_router(courses_router.router)
app.include_router(videos_router.router)
app.include_router(generation_router.router)
app.include_router(chat_router.router)
app.include_router(frontend_router.router)


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "app": settings.app_name}