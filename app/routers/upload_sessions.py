"""Chunked-upload session endpoints (13a commit B, 2026-09-21).

The HTTP surface over app/services/upload_sessions.py. Design (the
ratified registry §3):

  POST   /api/upload-sessions/init           declare + validate + reserve
  PUT    /api/upload-sessions/{id}/chunk/{n}  one raw-body chunk (≤32MB)
  GET    /api/upload-sessions/{id}/status     the resume bitmap
  POST   /api/upload-sessions/{id}/complete  assemble → queue handoff
  DELETE /api/upload-sessions/{id}           instant cancel + space release

Why a SEPARATE router from the legacy /api/videos/upload paths: route
ordering (the Blockers.md postmortem — /upload-bulk got shadowed once),
plus a clean capability boundary: every route here is UPLOAD_VIDEO-
gated, ownership-checked, and returns the friendly limit wording.

Chunks are sent as RAW request bodies (not multipart): a 32MB chunk
spends zero bytes on base64/boundary overhead, and FastAPI reads them
via request.body() without the multipart parser's memory copy.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.admin import require_capability
from app.auth.dependencies import get_current_user
from app.auth.roles import Capability
from app.database import get_db
from app.models import UploadSession
from app.services import upload_sessions
from app.services.upload_limits import UploadLimitError

router = APIRouter(prefix="/api/upload-sessions", tags=["upload-sessions"])

# The router-level capability gate — FREE never uploads (registry §1).
_upload_cap = require_capability(Capability.UPLOAD_VIDEO)


class InitRequest(BaseModel):
    """The init payload — everything the server needs to validate
    before a single byte transfers (fail-fast, registry §5)."""
    section_id: str = Field(min_length=1, max_length=36)
    filename: str = Field(min_length=1, max_length=512)
    declared_size: int = Field(gt=0, le=20 * 1024 * 1024 * 1024)
    whisper_model: str | None = Field(default=None, max_length=64)
    language: str | None = Field(default=None, max_length=8)


@router.post("/init")
async def init_session(
    body: InitRequest,
    user: dict[str, Any] = Depends(_upload_cap),
    db: Session = Depends(get_db),
):
    """Declare an upload: validates ownership, extension, per-file
    limit, one-session rule, and quota headroom — then returns the
    session id + chunk plan. Any rejection happens BEFORE bytes move."""
    from app.models import Section, Course, User

    # Section ownership (same chain as the legacy upload path).
    section = db.get(Section, body.section_id)
    if section is None:
        return JSONResponse(
            status_code=404, content={"detail": "Section not found."}
        )
    course = db.get(Course, section.course_id)
    uid = user.get("uid", "")
    if course is None or course.user_id != uid:
        return JSONResponse(
            status_code=403, content={"detail": "Not your course."}
        )

    user_row = db.get(User, uid)

    try:
        session = upload_sessions.create_session(
            db,
            user_row,
            body.section_id,
            filename=body.filename,
            declared_size=body.declared_size,
            section_owner_ok=True,
            whisper_model=body.whisper_model,
            language=body.language,
        )
    except UploadLimitError as exc:
        # The friendly registry wording — 413 for size/quota (the
        # numbers are specific and actionable, never a raw 500).
        return JSONResponse(status_code=413, content={"detail": exc.message})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return {
        "session_id": session.id,
        "chunk_size": session.chunk_size,
        "total_chunks": session.total_chunks,
        "received_chunks": [],  # fresh session: nothing staged yet
    }


async def _owned_or_404(
    db: Session, session_id: str, uid: str
) -> UploadSession | JSONResponse:
    """Fetch the session if the caller owns it; else a 404 response
    (never 403 — don't confirm another user's session id exists)."""
    try:
        return upload_sessions._get_owned_session(db, session_id, uid)
    except LookupError:
        return JSONResponse(
            status_code=404, content={"detail": "Upload session not found."}
        )


@router.put("/{session_id}/chunk/{index}")
async def put_chunk(
    session_id: str,
    index: int,
    request: Request,
    user: dict[str, Any] = Depends(_upload_cap),
    db: Session = Depends(get_db),
):
    """Receive one chunk as a raw body. Idempotent: re-sending the
    same chunk overwrites (the network-retry contract, §3a)."""
    owned = await _owned_or_404(db, session_id, user.get("uid", ""))
    if isinstance(owned, JSONResponse):
        return owned

    # Read the raw body. A hard cap at chunk_size + slack catches a
    # client that ignores the chunk plan before we buffer anything
    # silly (Starlette's request.body() is the memory path — bound it).
    max_body = owned.chunk_size + 1024 * 1024
    if int(request.headers.get("content-length") or 0) > max_body:
        return JSONResponse(
            status_code=413,
            content={"detail": "Chunk body exceeds the chunk size plan."},
        )
    data = await request.body()
    if len(data) > max_body:
        return JSONResponse(
            status_code=413,
            content={"detail": "Chunk body exceeds the chunk size plan."},
        )

    try:
        count = upload_sessions.append_chunk(db, owned, index, data)
    except PermissionError as exc:
        # §3a's explicit race answer: swept/completed session → 409,
        # the client restarts the upload.
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return {"received": count, "total_chunks": owned.total_chunks}


@router.get("/{session_id}/status")
async def session_status(
    session_id: str,
    user: dict[str, Any] = Depends(_upload_cap),
    db: Session = Depends(get_db),
):
    """The resume/status payload — which chunks the server has."""
    owned = await _owned_or_404(db, session_id, user.get("uid", ""))
    if isinstance(owned, JSONResponse):
        return owned
    return upload_sessions.get_status(db, owned)


@router.post("/{session_id}/complete")
async def complete(
    session_id: str,
    user: dict[str, Any] = Depends(_upload_cap),
    db: Session = Depends(get_db),
):
    """Assemble + verify → Video row (queued) → the existing pipeline
    owns everything from here (the queue claims it within ~3s)."""
    owned = await _owned_or_404(db, session_id, user.get("uid", ""))
    if isinstance(owned, JSONResponse):
        return owned

    try:
        video = upload_sessions.complete_session(db, owned)
    except PermissionError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return {
        "video_id": video.id,
        "status": video.status,
        "auto_process": True,
    }


@router.delete("/{session_id}")
async def cancel_session(
    session_id: str,
    user: dict[str, Any] = Depends(_upload_cap),
    db: Session = Depends(get_db),
):
    """Instant space release (registry §2's valve): staging deleted,
    quota freed now — no sweeper wait."""
    owned = await _owned_or_404(db, session_id, user.get("uid", ""))
    if isinstance(owned, JSONResponse):
        return owned
    upload_sessions.delete_session(db, owned)
    return {"cancelled": True}


@router.get("/last-cancelled")
async def last_cancelled(
    user: dict[str, Any] = Depends(_upload_cap),
    db: Session = Depends(get_db),
):
    """The interrupted-upload banner feed (registry §3a, Round 2):
    the user's most-recent cancelled session, if any. The upload page
    renders the banner until their next upload completes; the client
    passes ?dismissed= after the ✕ click (a UI concern; the endpoint
    just reports state honestly)."""
    from sqlalchemy import select

    uid = user.get("uid", "")
    row = (
        db.execute(
            select(UploadSession)
            .where(
                UploadSession.user_id == uid,
                UploadSession.status == "cancelled",
            )
            .order_by(UploadSession.cancelled_at.desc())
            .limit(1)
        )
        .scalar_one_or_none()
    )
    if row is None:
        return {"has_cancelled": False}
    return {
        "has_cancelled": True,
        "filename": row.original_filename,
        "cancelled_at": row.cancelled_at.isoformat()
        if row.cancelled_at
        else None,
        "declared_size": row.declared_size,
    }