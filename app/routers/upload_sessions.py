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
from starlette.requests import ClientDisconnect

from app.auth.admin import require_capability
from app.auth.dependencies import get_current_user
from app.auth.roles import Capability
from app.database import get_db
from app.models import UploadSession
from app.services import upload_sessions
from app.services.upload_limits import UploadLimitError
from app.utils.events import log_event

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
        # The one-active-session rule raises ValueError with this
        # exact prefix; if matched, surface the existing session
        # metadata so the client can render a Clear-and-retry button
        # instead of a generic "you have something in progress" wall
        # of silence (known-issues §6, the 9/26 incident).
        msg = str(exc)
        if msg.startswith("You already have an upload in progress"):
            existing = upload_sessions.get_active_session(db, user_row)
            return JSONResponse(
                status_code=400,
                content={
                    "code": "active_session_in_progress",
                    "message": msg,
                    "session": _serialize_session(existing)
                    if existing is not None else None,
                },
            )
        return JSONResponse(status_code=400, content={"detail": msg})

    return {
        "session_id": session.id,
        "chunk_size": session.chunk_size,
        "total_chunks": session.total_chunks,
        "received_chunks": [],  # fresh session: nothing staged yet
    }


@router.get("/me")
async def my_active_session(
    user: dict[str, Any] = Depends(_upload_cap),
    db: Session = Depends(get_db),
):
    """The C+ at-attempt banner source (known-issues §6, 2026-09-26).

    Returns the calling user's ACTIVE session (if any), with the TTL-
    derived expires_at so the client can show an honest countdown.
    Used by the upload-card JS on course.html / dashboard.html to
    render a banner ABOVE the file picker before the user clicks
    Upload — surfaces the stuck session at the moment of decision
    instead of forcing them to attempt an upload first.

    Path is /me (not /active) so it can never collide with the
    {session_id} routes below — FastAPI's literal-segment matching
    prefers /me over /{session_id}."""
    from app.models import User

    uid = user.get("uid", "")
    user_row = db.get(User, uid)
    if user_row is None:
        return {"active_session": None}
    session = upload_sessions.get_active_session(db, user_row)
    if session is None:
        return {"active_session": None}
    return {"active_session": _serialize_session_with_ttl(session)}


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


def _serialize_session(session: UploadSession) -> dict:
    """The structured shape the client renders for the Resume card
    AND the active-session modal. Single source of truth so both
    surfaces can never disagree (known-issues §6 followup).

    received_chunks / received_bytes are read from the filesystem —
    the staging dir is the truth (registry §3a), the DB row only
    holds declared metadata.

    chunk_size is REQUIRED by the client's resume path (it slices
    the re-picked file into chunks — without it the first chunk
    PUT computes a wrong slice). The 9/28 incident ("Upload session
    not found" after clicking Resume) was exactly this gap: the
    payload had `id` but the client destructured `session_id`, and
    chunk_size was missing entirely → PUT .../undefined/... → 404.
    """
    got = upload_sessions.received_chunks(session.id)
    return {
        "id": session.id,
        "filename": session.original_filename,
        "declared_size": session.declared_size,
        "chunk_size": session.chunk_size,
        "total_chunks": session.total_chunks,
        "received_chunks": got,
        "received_bytes": sum(
            upload_sessions._chunk_path(session.id, i).stat().st_size
            for i in got
        ),
        "section_id": session.section_id,
        "started_at": session.created_at.isoformat()
        if session.created_at else None,
        "last_chunk_at": session.last_activity_at.isoformat()
        if session.last_activity_at else None,
        "expires_at": None,  # populated by GET /me; init returns None
                             # so the client doesn't show a
                             # misleading countdown.
    }


def _serialize_session_with_ttl(session: UploadSession) -> dict:
    """The GET /me payload variant: includes the TTL-derived
    expires_at so the client can show an honest countdown ("will
    auto-cancel in N minutes if nothing arrives")."""
    payload = _serialize_session(session)
    if session.last_activity_at is not None:
        from app.config import settings
        from datetime import timedelta

        expires_at = session.last_activity_at + timedelta(
            hours=settings.upload_session_ttl_hours
        )
        payload["expires_at"] = expires_at.isoformat()
    return payload


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
    try:
        data = await request.body()
    except ClientDisconnect:
        # Chunks are sacred (owner direction, 2026-09-27): the
        # connection was severed (navigate away / close tab / laptop
        # lid), but everything already received is on disk and valid.
        # PRESERVE the session — do NOT cancel. The 1h inactivity TTL
        # is the net for genuine abandonment; the Resume card on the
        # course page picks this back up if the user returns within
        # the window. This turns the 9/26 incident into: chunks kept,
        # user resumes, upload finishes.
        duration_s = (
            (owned.last_activity_at - owned.created_at).total_seconds()
            if owned.last_activity_at and owned.created_at
            else 0.0
        )
        log_event(
            db,
            level="WARNING",
            source="ui.upload",
            message="chunk PUT client_disconnect; session preserved",
            user_id=user.get("uid", "") or None,
            context={
                "session_id": owned.id,
                "chunk_index": index,
                "declared_size": owned.declared_size,
                "chunks_uploaded": len(
                    upload_sessions.received_chunks(owned.id)
                ),
                "duration_since_first_chunk_s": round(duration_s, 1),
            },
        )
        db.commit()
        # 499 (Client Closed Request) — the response never reaches the
        # dead client, but a clean status beats an unhandled 500 in
        # the logs (this exact 500 is what made the 9/26 diagnosis
        # harder than it needed to be).
        return JSONResponse(
            status_code=499,
            content={"detail": "Client disconnected; chunks preserved."},
        )
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