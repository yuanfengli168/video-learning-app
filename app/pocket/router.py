"""Pocket v0.1 router — mounted at /m/* on the main FastAPI app.

Endpoints (all require an authenticated user, same as the rest of the app):
- GET   /m/snapshot?since=<token>     — incremental sync of courses/sections/videos + materials
- POST  /m/teach/{video_id}            — start an async tutor job
- GET   /m/teach/{video_id}/status     — poll job status
- GET   /m/chunks/{video_id}           — read cached chunks (no Ollama call)
- POST  /m/chunk/{chunk_id}/done       — mark chunk complete
- GET   /m/progress/{video_id}         — read progress state
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user  # noqa: F401  (kept for future use)
from app.database import get_db
from app.models import Video
from app.pocket import jobs, sync
from app.pocket.dev_auth import get_current_user_dev_or_real as get_current_user
from app.pocket.models import PocketChunk, PocketProgress
from app.pocket.schemas import (
    ChunkDoneOut,
    ChunkOut,
    ProgressOut,
    SnapshotOut,
    TeachJobCreated,
    TeachStatusOut,
)

router = APIRouter(tags=["pocket"])


# ── /m/snapshot ────────────────────────────────────────────────

@router.get("/snapshot", response_model=SnapshotOut)
def get_snapshot(
    since: str | None = None,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Return a sync snapshot. Optional `since` token for incremental sync."""
    return sync.build_snapshot(db=db, user_id=user["uid"], since=since)


# ── /m/teach ───────────────────────────────────────────────────

@router.post("/teach/{video_id}", response_model=TeachJobCreated)
def post_teach(
    video_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Start an async tutor job. Returns {job_id} immediately."""
    v = db.execute(select(Video).where(Video.id == video_id)).scalar_one_or_none()
    if v is None:
        raise HTTPException(status_code=404, detail=f"Video {video_id} not found")
    job_id = jobs.start_teach_job(user_id=user["uid"], video_id=video_id)
    return {"job_id": job_id}


@router.get("/teach/{video_id}/status", response_model=TeachStatusOut)
def get_teach_status(
    video_id: str,
    job_id: str,
    user: dict = Depends(get_current_user),
) -> dict:
    """Poll status of a previously-started tutor job."""
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found (expired or invalid)")
    if job.user_id != user["uid"]:
        raise HTTPException(status_code=403, detail="Not your job")
    out: dict = {"job_id": job.job_id, "status": job.status}
    if job.status == "ready":
        out["chunks"] = [_chunk_dict_to_out(c) for c in job.chunks]
    if job.status == "error":
        out["error"] = job.error or "unknown error"
    return out


# ── /m/chunks (read cached) ────────────────────────────────────

@router.get("/chunks/{video_id}", response_model=list[ChunkOut])
def get_cached_chunks(
    video_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Read server-cached chunks for a video. No Ollama call."""
    rows = db.execute(
        select(PocketChunk)
        .where(PocketChunk.video_id == video_id)
        .order_by(PocketChunk.index)
    ).scalars().all()
    return [_chunk_row_to_out(r) for r in rows]


# ── /m/chunk/{id}/done ─────────────────────────────────────────

@router.post("/chunk/{chunk_id}/done", response_model=ChunkDoneOut)
def mark_chunk_done(
    chunk_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Mark a chunk complete. Idempotent."""
    chunk = db.execute(
        select(PocketChunk).where(PocketChunk.id == chunk_id)
    ).scalar_one_or_none()
    if chunk is None:
        raise HTTPException(status_code=404, detail=f"Chunk {chunk_id} not found")

    existing = db.execute(
        select(PocketProgress).where(
            PocketProgress.user_id == user["uid"],
            PocketProgress.video_id == chunk.video_id,
            PocketProgress.chunk_index == chunk.index,
        )
    ).scalar_one_or_none()

    if existing is None:
        db.add(PocketProgress(
            user_id=user["uid"],
            video_id=chunk.video_id,
            chunk_index=chunk.index,
        ))
    # else: already done — idempotent no-op
    db.commit()

    return {
        "chunk_id": chunk.id,
        "video_id": chunk.video_id,
        "completed": True,
    }


# ── /m/progress/{video_id} ─────────────────────────────────────

@router.get("/progress/{video_id}", response_model=ProgressOut)
def get_progress(
    video_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Read the user's progress for a video."""
    rows = db.execute(
        select(PocketProgress)
        .where(
            PocketProgress.user_id == user["uid"],
            PocketProgress.video_id == video_id,
        )
        .order_by(PocketProgress.last_seen_at.desc())
    ).scalars().all()

    if not rows:
        return {"video_id": video_id, "chunks_done": [], "last_seen_chunk": None, "last_seen_at": None}

    chunks_done = sorted({r.chunk_index for r in rows})
    top = rows[0]  # most recent (ordered desc)
    return {
        "video_id": video_id,
        "chunks_done": chunks_done,
        "last_seen_chunk": top.chunk_index,
        "last_seen_at": top.last_seen_at,
    }


# ── helpers ────────────────────────────────────────────────────

def _chunk_row_to_out(row: PocketChunk) -> dict:
    return {
        "id": row.id,
        "video_id": row.video_id,
        "index": row.index,
        "start_ts": row.start_ts,
        "end_ts": row.end_ts,
        "duration_label": row.duration_label,
        "concept_title": row.concept_title,
        "teach_text": row.teach_text,
        "check_question": row.check_question,
    }


def _chunk_dict_to_out(d: dict) -> dict:
    """Chunks from the in-memory job are plain dicts; coerce to ChunkOut shape."""
    return {
        "id": d.get("id", ""),
        "video_id": d.get("video_id", ""),
        "index": int(d.get("index", 0)),
        "start_ts": float(d.get("start_ts", 0.0)),
        "end_ts": float(d.get("end_ts", 0.0)),
        "duration_label": d.get("duration_label", "5min"),
        "concept_title": d.get("concept_title", ""),
        "teach_text": d.get("teach_text", ""),
        "check_question": d.get("check_question", ""),
    }
