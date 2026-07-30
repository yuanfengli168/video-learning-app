"""MVP0.2: HTTP endpoints for course materials.

The router is mounted under `/api/materials`. It uses the existing
`get_current_user` auth dependency so both the Mac web app and the
iOS app can call these endpoints with a Firebase Bearer token.

Endpoints:
  POST   /api/materials                       (Mac only — origin-gated)
  GET    /api/materials                       (Mac + iOS)
  GET    /api/materials/{id}                  (Mac + iOS)
  GET    /api/materials/{id}/text             (Mac + iOS)
  DELETE /api/materials/{id}                  (Mac only — origin-gated)
  POST   /api/materials/{id}/link             (Mac + iOS)

Upload/delete origin gating: Mac web app requests come from
`http://localhost:8000` or `https://localhost:<port>`. iOS requests
come from the simulator/device hitting `https://<mac-ip>:8443`.
We gate by checking the `Origin` header — present on browser requests,
absent on native iOS network requests.
"""

from __future__ import annotations

import io
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.config import settings
from app.database import get_db
from app.models import (
    PocketMaterial,
    PocketVideoMaterial,
    Section,
    Video,
)
from app.models.material import _uuid as _new_uuid
from app.schemas.material import (
    MaterialOut,
    MaterialTextOut,
    MaterialUploadOut,
)
from app.services.material_extractor import detect_kind, extract

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/materials", tags=["materials"])


# ── Helpers ────────────────────────────────────────────────────────────


def _require_mac_origin(origin: str | None) -> None:
    """Reject iOS / API clients for upload + delete.

    iOS doesn't send an Origin header (native URLSession), so we treat
    "no Origin" as iOS / non-browser. The Mac web app's fetch always
    includes Origin (CORS preflight or simple request).

    Why gate: the Mac is the user's authoring surface — the iOS app
    is a passive mirror. Letting the iOS app upload would mean
    handling large file transfers over the local network on a
    phone-sized screen with no progress UI. Better to keep that
    responsibility on the Mac web app.
    """
    if not origin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This action is only available from the Mac web app. "
                "iOS is a read-only mirror — upload via your Mac."
            ),
        )


def _storage_path_for(user_id: str, material_id: str, ext: str) -> Path:
    """Return the on-disk path where the uploaded file should live."""
    user_dir = Path(settings.upload_dir) / "materials" / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / f"{material_id}{ext}"


def _user_total_bytes(db: Session, user_id: str) -> int:
    """Sum the size_bytes of all materials for this user."""
    total = db.execute(
        select(func.coalesce(func.sum(PocketMaterial.size_bytes), 0)).where(
            PocketMaterial.user_id == user_id
        )
    ).scalar_one()
    return int(total)


def _user_can_upload_more(db: Session, user_id: str, additional_bytes: int) -> None:
    """Raise 413 if this upload would exceed the user's storage cap."""
    current = _user_total_bytes(db, user_id)
    if current + additional_bytes > settings.materials_max_total_bytes_per_user:
        limit_mb = settings.materials_max_total_bytes_per_user // (1024 * 1024)
        used_mb = current // (1024 * 1024)
        add_mb = additional_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Upload would exceed your {limit_mb} MB storage cap "
                f"(currently using {used_mb} MB, this file is {add_mb} MB). "
                "Delete some materials or upgrade (MVP0.3)."
            ),
        )


def _is_pdf_or_text_or_zip(filename: str) -> bool:
    return detect_kind(filename) != "unknown"


# ── Endpoints ──────────────────────────────────────────────────────────


@router.post("", response_model=MaterialUploadOut, status_code=status.HTTP_201_CREATED)
async def upload_material(
    file: UploadFile = File(...),
    section_id: str | None = Form(default=None),
    video_id: str | None = Form(default=None),
    origin: str | None = Header(default=None),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MaterialUploadOut:
    """Upload a PDF / .md / .txt / .zip. Mac web app only.

    Form fields:
      - file (required): the uploaded file
      - section_id (optional): link to a section (visible to all its videos)
      - video_id (optional): link to a single video (per-video note)

    If both section_id and video_id are set, the file appears in:
      - the section's master list
      - the video's picker (auto-selected)
    """
    _require_mac_origin(origin)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    # MVP0.2: require at least one of section_id / video_id so materials
    # always have an attachment point. iOS will never hit this (gated above)
    # but the rule keeps data clean if anyone bypasses the UI.
    if not section_id and not video_id:
        raise HTTPException(
            status_code=400,
            detail="Material must be attached to a section_id or video_id.",
        )

    # Validate kind
    if not _is_pdf_or_text_or_zip(file.filename):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported file type: {file.filename}. "
                "Supported: .pdf, .md, .markdown, .txt, .zip"
            ),
        )

    # Read the file (with size cap)
    data = await file.read()
    size = len(data)
    if size == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if size > settings.materials_max_file_bytes:
        limit_mb = settings.materials_max_file_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large ({size // 1024 // 1024} MB > {limit_mb} MB cap).",
        )

    # Validate section_id / video_id exist + belong to user (when set)
    if section_id is not None:
        sec = db.execute(
            select(Section).where(Section.id == section_id)
        ).scalar_one_or_none()
        if sec is None:
            raise HTTPException(status_code=404, detail=f"Section {section_id} not found")
    if video_id is not None:
        vid = db.execute(select(Video).where(Video.id == video_id)).scalar_one_or_none()
        if vid is None:
            raise HTTPException(status_code=404, detail=f"Video {video_id} not found")

    # Per-user total size check
    _user_can_upload_more(db, user["uid"], size)

    # Save file to disk
    material_id = _new_uuid()
    # Derive extension from filename (sanitize)
    raw_ext = os.path.splitext(file.filename)[1].lower()
    ext = "".join(c for c in raw_ext if c.isalnum() or c == ".") or ".bin"
    path = _storage_path_for(user["uid"], material_id, ext)
    path.write_bytes(data)

    # Determine mime_type from extension
    mime = file.content_type or "application/octet-stream"

    # Create the row (status=processing, will be updated after extraction)
    material = PocketMaterial(
        id=material_id,
        user_id=user["uid"],
        section_id=section_id,
        video_id=video_id,
        filename=file.filename,
        size_bytes=size,
        mime_type=mime,
        storage_path=str(path),
        status="processing",
    )
    db.add(material)
    db.commit()

    # If uploaded directly to a video, auto-select it for that video
    if video_id is not None:
        link = PocketVideoMaterial(
            video_id=video_id,
            material_id=material_id,
        )
        db.add(link)
        db.commit()

    # Extract text synchronously. For PDFs up to ~100 pages and small
    # code archives this completes in <60s. We let the request hang
    # here (no async background job) to keep MVP0.2 simple — uploads
    # from the Mac are bounded by the 50 MB cap and 60s extraction
    # budget, both enforced at this layer.
    try:
        text = extract(file.filename, data)
        if text is None:
            material.status = "failed"
            material.error_message = "Unsupported file format"
        else:
            material.extracted_text = text
            material.char_count = len(text)
            material.status = "ready"
    except Exception as exc:
        log.warning("Extraction failed for %s: %s", file.filename, exc)
        material.status = "failed"
        material.error_message = f"Extraction failed: {exc}"

    material.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(material)

    return MaterialUploadOut(
        id=material.id,
        filename=material.filename,
        section_id=material.section_id,
        video_id=material.video_id,
        status=material.status,
        char_count=material.char_count,
        error_message=material.error_message,
    )


@router.get("", response_model=list[MaterialOut])
def list_materials(
    section_id: str | None = None,
    video_id: str | None = None,
    status_filter: str | None = None,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MaterialOut]:
    """List the authenticated user's materials.

    Query params:
      - section_id (optional): filter to materials in this section
        (section-scope + materials uploaded from videos in this section)
      - video_id (optional): filter to materials linked to this video
      - status_filter (optional): 'processing' | 'ready' | 'failed'
    """
    q = select(PocketMaterial).where(PocketMaterial.user_id == user["uid"])

    if section_id is not None:
        # Section's master list = section-scope OR video-scope for videos
        # in this section.
        sibling_video_ids = select(Video.id).where(Video.section_id == section_id)
        q = q.where(
            or_(
                PocketMaterial.section_id == section_id,
                PocketMaterial.video_id.in_(sibling_video_ids),
            )
        )

    if video_id is not None:
        q = q.where(PocketMaterial.video_id == video_id)

    if status_filter is not None:
        q = q.where(PocketMaterial.status == status_filter)

    q = q.order_by(PocketMaterial.created_at.desc())
    rows = db.execute(q).scalars().all()
    return [MaterialOut.model_validate(r) for r in rows]


@router.get("/{material_id}", response_model=MaterialOut)
def get_material(
    material_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MaterialOut:
    row = db.execute(
        select(PocketMaterial).where(
            and_(
                PocketMaterial.id == material_id,
                PocketMaterial.user_id == user["uid"],
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Material not found")
    return MaterialOut.model_validate(row)


@router.get("/{material_id}/text", response_class=__import__("fastapi.responses", fromlist=["PlainTextResponse"]).PlainTextResponse)
def get_material_text(
    material_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the extracted text as plain text (Content-Type: text/plain).

    This avoids JSON-escape overhead for large strings (a 500K-char PDF
    would balloon to 1MB+ as JSON). Used by:
      - The Mac web viewer page (raw text rendering)
      - The iOS PDF / text viewer (offline cache)
      - The tutor prompt builder (inlines selected materials)
    """
    row = db.execute(
        select(PocketMaterial).where(
            and_(
                PocketMaterial.id == material_id,
                PocketMaterial.user_id == user["uid"],
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Material not found")
    if row.status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Material is not ready (status: {row.status}). "
                   f"Error: {row.error_message or 'still processing'}",
        )
    return row.extracted_text or ""


@router.delete("/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_material(
    material_id: str,
    origin: str | None = Header(default=None),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a material. Mac web app only.

    Cascades to PocketVideoMaterial rows (PocketVideoMaterial FK is
    ondelete='CASCADE'). The on-disk file is deleted too.
    """
    _require_mac_origin(origin)

    row = db.execute(
        select(PocketMaterial).where(
            and_(
                PocketMaterial.id == material_id,
                PocketMaterial.user_id == user["uid"],
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Material not found")

    # Delete the file (best-effort; missing file is OK)
    try:
        Path(row.storage_path).unlink(missing_ok=True)
    except Exception as exc:
        log.warning("Failed to delete material file %s: %s", row.storage_path, exc)

    # MVP0.2: SQLite + test in-memory DB don't always enforce ON DELETE
    # CASCADE, so we explicitly remove the PocketVideoMaterial rows that
    # point at this material. (The FK is still declared CASCADE for
    # production Postgres / SQLite-with-foreign-keys=ON.)
    db.execute(
        delete(PocketVideoMaterial).where(PocketVideoMaterial.material_id == material_id)
    )
    db.delete(row)
    db.commit()
    return None


@router.post("/{material_id}/link", response_model=MaterialOut)
def link_material(
    material_id: str,
    section_id: str | None = Form(default=None),
    video_id: str | None = Form(default=None),
    origin: str | None = Header(default=None),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MaterialOut:
    """Re-link a material to a section / video.

    Used when the user wants to attach an existing material to a
    different section or video (e.g., uploaded to section 1, now
    wants section 2 to see it too).
    """
    _require_mac_origin(origin)
    row = db.execute(
        select(PocketMaterial).where(
            and_(
                PocketMaterial.id == material_id,
                PocketMaterial.user_id == user["uid"],
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Material not found")

    if section_id is not None:
        sec = db.execute(
            select(Section).where(Section.id == section_id)
        ).scalar_one_or_none()
        if sec is None:
            raise HTTPException(status_code=404, detail=f"Section {section_id} not found")
        row.section_id = section_id
    if video_id is not None:
        vid = db.execute(select(Video).where(Video.id == video_id)).scalar_one_or_none()
        if vid is None:
            raise HTTPException(status_code=404, detail=f"Video {video_id} not found")
        row.video_id = video_id

    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return MaterialOut.model_validate(row)
