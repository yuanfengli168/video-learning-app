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

from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user  # noqa: F401  (kept for future use)
from app.database import get_db
from app.models import Video
from app.pocket import jobs, sync, tutor
from app.pocket.dev_auth import get_current_user_dev_or_real as get_current_user
from app.pocket.models import PocketChunk, PocketProgress
from app.pocket.schemas import (
    BatchFeedbackRequest,
    BatchFeedbackResponse,
    ChunkDoneOut,
    ChunkOut,
    FavoriteToggleResponse,
    FavoritesOut,
    FavoriteItemOut,
    FeedbackRequest,
    FeedbackResponse,
    MarkDoneWithAnswerRequest,
    ProgressDetailOut,
    ProgressDetailItemOut,
    ProgressOut,
    SnapshotOut,
    TeachJobCreated,
    TeachStatusOut,
)

router = APIRouter(tags=["pocket"])

import logging
logger = logging.getLogger(__name__)


# Friendly explanations when Ollama is down. Keyed by OllamaUnavailableError.kind
# so the iOS UI can show a specific message ("Check Ollama is running" vs
# "Ollama is slow right now").
_OLLAMA_DOWN_EXPLANATION: dict[str, str] = {
    "unreachable": "AI tutor is offline. Make sure Ollama is running on your Mac, then try again. Your answer is saved.",
    "timeout":     "AI tutor is taking too long. Try again in a moment. Your answer is saved.",
    "http_5xx":    "AI tutor had an error. Try again in a moment. Your answer is saved.",
}

import hashlib


def _etag_for(snapshot: dict) -> str:
    """Compute a short, stable ETag from the snapshot's sync_token.

    Phone sends this as `If-None-Match` on the next call. If unchanged,
    server returns 304 with no body — saves ~99% of bandwidth on
    'nothing changed' opens.
    """
    token = snapshot.get("sync_token", "") or ""
    # Use a short prefix of SHA-256; wrap in quotes per HTTP spec.
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    return f'"{digest}"'


@router.get("/snapshot", response_model=SnapshotOut)
def get_snapshot(
    request: Request,
    response: Response,
    since: str | None = None,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Return a sync snapshot. Optional `since` token for incremental sync.

    Honors `If-None-Match` for cheap "nothing changed" round-trips:
    the phone sends the previous response's ETag, and we return 304 with
    no body if the sync_token hasn't moved.
    """
    snapshot = sync.build_snapshot(db=db, user_id=user["uid"], since=since)
    etag = _etag_for(snapshot)
    # Make the ETag available to the client on the 200 response too,
    # so subsequent calls can use it as If-None-Match.
    response.headers["ETag"] = etag

    if_none_match = request.headers.get("If-None-Match")
    if if_none_match and if_none_match == etag:
        # 304 has no body. The phone treats this as "no changes" and
        # just bumps its in-memory lastSyncDate.
        return Response(status_code=304, headers={"ETag": etag})

    return snapshot


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


# ── v0.1.3: extended progress with answers + favorites + verdicts ──

@router.get("/progress/{video_id}/detail", response_model=ProgressDetailOut)
def get_progress_detail(
    video_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Extended progress for the Teach-me UI: typed answers, favorites, AI verdicts.

    Joins PocketProgress with PocketChunk so the iOS Review-my-answers
    screen can render one rich card per chunk with concept title,
    student answer, and last AI verdict + explanation.
    """
    rows = db.execute(
        select(PocketProgress, PocketChunk)
        .join(
            PocketChunk,
            (PocketChunk.video_id == PocketProgress.video_id)
            & (PocketChunk.index == PocketProgress.chunk_index)
        )
        .where(
            PocketProgress.user_id == user["uid"],
            PocketProgress.video_id == video_id,
        )
        .order_by(PocketProgress.chunk_index)
    ).all()

    if not rows:
        return ProgressDetailOut(video_id=video_id, items=[]).model_dump()

    items = [
        ProgressDetailItemOut(
            chunk_id=chunk.id,
            chunk_index=progress.chunk_index,
            concept_title=chunk.concept_title,
            is_done=progress.completed_at is not None,
            user_answer=progress.user_answer or "",
            is_favorite=bool(progress.is_favorite),
            last_ai_verdict=progress.last_ai_verdict or "",
            last_ai_explanation=progress.last_ai_explanation or "",
        )
        for progress, chunk in rows
    ]
    return ProgressDetailOut(video_id=video_id, items=items).model_dump()


# ── v0.1.3: mark-done with typed answer + favorite toggle ─────

@router.post("/chunk/{chunk_id}/done", response_model=ChunkDoneOut)
def mark_chunk_done(
    chunk_id: str,
    body: MarkDoneWithAnswerRequest | None = Body(default=None),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Mark a chunk complete. Idempotent. Optionally persists user_answer + is_favorite."""
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

    now = datetime.utcnow()
    if existing is None:
        existing = PocketProgress(
            user_id=user["uid"],
            video_id=chunk.video_id,
            chunk_index=chunk.index,
        )
        db.add(existing)
    if body and body.user_answer:
        existing.user_answer = body.user_answer
    if body and body.is_favorite is not None:
        existing.is_favorite = body.is_favorite
    existing.last_seen_at = now
    db.commit()
    db.refresh(existing)

    return {
        "chunk_id": chunk.id,
        "video_id": chunk.video_id,
        "completed": True,
    }


# ── v0.1.3: AI grading (single + batch) ─────────────────────

@router.post("/chunk/{chunk_id}/feedback", response_model=FeedbackResponse)
def chunk_feedback(
    chunk_id: str,
    body: FeedbackRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Grade the student's typed answer against the canonical chunk answer.

    Persists the verdict + explanation to PocketProgress.

    The "canonical answer" is derived from the chunk: we combine the tutor's
    `teach_text` (the explanation the student was supposed to learn) with
    the `check_question` (so the grader knows what was being asked). If
    the caller sent their own canonical_answer in the body (future use),
    prefer it.
    """
    chunk = db.execute(
        select(PocketChunk).where(PocketChunk.id == chunk_id)
    ).scalar_one_or_none()
    if chunk is None:
        raise HTTPException(status_code=404, detail=f"Chunk {chunk_id} not found")

    if body.canonical_answer:
        canonical = body.canonical_answer
    else:
        # Build a canonical from what's actually in the chunk. teach_text is
        # the lesson (which contains the answer); check_question frames what
        # we asked the student. Together they give the grader full context.
        canonical = (
            f"Question asked: {chunk.check_question}\n"
            f"What the tutor said: {chunk.teach_text}"
        )

    try:
        result = tutor.grade_single(
            user_answer=body.user_answer,
            canonical_answer=canonical,
        )
    except tutor.OllamaUnavailableError as e:
        # Ollama is down / unreachable / 5xx — return a clean response
        # so the iOS UI can show a helpful message instead of a 500.
        # Persist nothing; the student can retry later.
        logger.warning(
            "chunk_feedback: Ollama %s for chunk %s: %s",
            e.kind, chunk_id, e.detail,
        )
        return FeedbackResponse(
            chunk_id=chunk_id,
            verdict=tutor.VERDICT_MISSED,
            explanation=_OLLAMA_DOWN_EXPLANATION.get(
                e.kind,
                "AI tutor is currently unavailable. Your answer is saved — try again in a moment.",
            ),
            ollama_unavailable=True,
        ).model_dump()

    existing = db.execute(
        select(PocketProgress).where(
            PocketProgress.user_id == user["uid"],
            PocketProgress.video_id == chunk.video_id,
            PocketProgress.chunk_index == chunk.index,
        )
    ).scalar_one_or_none()
    now = datetime.utcnow()
    if existing is None:
        existing = PocketProgress(
            user_id=user["uid"],
            video_id=chunk.video_id,
            chunk_index=chunk.index,
            user_answer=body.user_answer,
            last_ai_verdict=result["verdict"],
            last_ai_explanation=result["explanation"],
            last_ai_graded_at=now,
        )
        db.add(existing)
    else:
        if body.user_answer:
            existing.user_answer = body.user_answer
        existing.last_ai_verdict = result["verdict"]
        existing.last_ai_explanation = result["explanation"]
        existing.last_ai_graded_at = now
        existing.last_seen_at = now
    db.commit()

    return FeedbackResponse(
        chunk_id=chunk.id,
        verdict=result["verdict"],  # type: ignore[arg-type]
        explanation=result["explanation"],
    )


@router.post("/chunks/grade-batch", response_model=BatchFeedbackResponse)
def chunks_grade_batch(
    body: BatchFeedbackRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Grade multiple student answers in one Ollama call (faster for many chunks)."""
    if not body.items:
        return BatchFeedbackResponse(verdicts=[]).model_dump()

    chunk_ids = [it.chunk_id for it in body.items]
    chunks = {
        c.id: c
        for c in db.execute(
            select(PocketChunk).where(PocketChunk.id.in_(chunk_ids))
        ).scalars().all()
    }

    items_for_ollama = []
    for it in body.items:
        ch = chunks.get(it.chunk_id)
        items_for_ollama.append({
            "user_answer": it.user_answer,
            "canonical_answer": it.canonical_answer or (ch.check_question if ch else ""),
        })

    results = tutor.grade_batch(items_for_ollama)

    now = datetime.utcnow()
    for it, result in zip(body.items, results):
        ch = chunks.get(it.chunk_id)
        if ch is None:
            continue
        existing = db.execute(
            select(PocketProgress).where(
                PocketProgress.user_id == user["uid"],
                PocketProgress.video_id == ch.video_id,
                PocketProgress.chunk_index == ch.index,
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = PocketProgress(
                user_id=user["uid"],
                video_id=ch.video_id,
                chunk_index=ch.index,
                user_answer=it.user_answer,
                last_ai_verdict=result["verdict"],
                last_ai_explanation=result["explanation"],
                last_ai_graded_at=now,
            )
            db.add(existing)
        else:
            if it.user_answer:
                existing.user_answer = it.user_answer
            existing.last_ai_verdict = result["verdict"]
            existing.last_ai_explanation = result["explanation"]
            existing.last_ai_graded_at = now
            existing.last_seen_at = now
    db.commit()

    verdicts = []
    for it, result in zip(body.items, results):
        verdicts.append(FeedbackResponse(
            chunk_id=it.chunk_id,
            verdict=result["verdict"],  # type: ignore[arg-type]
            explanation=result["explanation"],
        ))
    return BatchFeedbackResponse(verdicts=verdicts).model_dump()


# ── v0.1.3: favorites ──────────────────────────────────────

@router.post("/chunk/{chunk_id}/favorite", response_model=FavoriteToggleResponse)
def toggle_favorite(
    chunk_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Toggle favorite on a chunk. Idempotent — calling twice returns to the original state."""
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
    now = datetime.utcnow()
    if existing is None:
        existing = PocketProgress(
            user_id=user["uid"],
            video_id=chunk.video_id,
            chunk_index=chunk.index,
            is_favorite=True,
            last_seen_at=now,
        )
        db.add(existing)
    else:
        existing.is_favorite = not existing.is_favorite
        existing.last_seen_at = now
    db.commit()
    db.refresh(existing)
    return FavoriteToggleResponse(chunk_id=chunk.id, is_favorite=existing.is_favorite)


@router.get("/favorites/{video_id}", response_model=FavoritesOut)
def list_favorites(
    video_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """List the user's favorited chunks for this video (rich).

    Joins PocketProgress (favorite + answer + AI verdict) with PocketChunk
    (concept + transcript quote) so the iOS Favorites screen can render
    complete cards with one round trip.
    """
    rows = db.execute(
        select(PocketProgress, PocketChunk)
        .join(
            PocketChunk,
            (PocketChunk.video_id == PocketProgress.video_id)
            & (PocketChunk.index == PocketProgress.chunk_index)
        )
        .where(
            PocketProgress.user_id == user["uid"],
            PocketProgress.video_id == video_id,
            PocketProgress.is_favorite == True,  # noqa: E712
        )
        .order_by(PocketProgress.chunk_index)
    ).all()
    items = [
        FavoriteItemOut(
            chunk_id=chunk.id,
            chunk_index=progress.chunk_index,
            concept_title=chunk.concept_title,
            transcript_quote=chunk.transcript_quote or "",
            user_answer=progress.user_answer or "",
            last_ai_verdict=progress.last_ai_verdict or "",
            last_ai_explanation=progress.last_ai_explanation or "",
        )
        for progress, chunk in rows
    ]
    return FavoritesOut(video_id=video_id, favorites=items).model_dump()


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
        "transcript_quote": row.transcript_quote or "",
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
