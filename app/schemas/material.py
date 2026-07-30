"""MVP0.2: Pydantic schemas for course materials.

The schemas serve three audiences:
  1. The Mac web app — upload form, section/videos picker, viewer page
  2. The iOS app — read-only mirror + selection sync (PUT selection)
  3. The tutor prompt builder — looks up selected materials to inline
     into the LLM context
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MaterialStatus = Literal["processing", "ready", "failed"]


class MaterialOut(BaseModel):
    """Metadata for one uploaded material. Returned by list/get endpoints.

    `extracted_text` is intentionally NOT included here — it's large
    and lazy-loaded via /api/materials/{id}/text.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    section_id: str | None
    video_id: str | None

    filename: str
    size_bytes: int
    mime_type: str
    status: MaterialStatus
    char_count: int | None
    error_message: str | None = None
    # MVP0.2 followup: provenance. One of "pypdf", "vision" (macOS Vision
    # framework), "ollama_vision" (llava etc), "tesseract", or None (legacy
    # row from before this column existed).
    extraction_method: str | None = None

    created_at: datetime
    updated_at: datetime


class MaterialUploadOut(BaseModel):
    """Returned immediately by POST /api/materials (sync upload + sync extract).

    The material row is created with status='processing' and the file
    is saved to disk, then `extracted_text` is computed. By the time
    this response is sent back, status is either 'ready' (extraction
    succeeded) or 'failed' (extraction errored). The client uses
    `status` + `char_count` to decide whether to show "Ready" or
    "Failed to extract" in the UI.
    """

    id: str
    filename: str
    section_id: str | None
    video_id: str | None
    status: MaterialStatus
    char_count: int | None
    error_message: str | None = None
    # MVP0.2 followup: provenance of how the text was extracted. One
    # of "pypdf", "vision" (macOS Vision OCR), "ollama_vision",
    # "tesseract", or None (legacy / unsupported format).
    extraction_method: str | None = None


class MaterialTextOut(BaseModel):
    """Full extracted text. Returned by GET /api/materials/{id}/text.

    Returned as plain text (Content-Type: text/plain) to avoid JSON
    escaping overhead for large strings.
    """

    text: str


class VideoMaterialLink(BaseModel):
    """A material linked to a video. Returned by GET /api/videos/{id}/materials."""

    model_config = ConfigDict(from_attributes=True)

    material_id: str
    filename: str
    size_bytes: int
    char_count: int | None
    added_at: datetime
    # MVP0.2 followup: which extraction method was used to read this
    # material. Lets the Mac UI show a small "OCR" badge so the user
    # knows the text came from a scan rather than the native text
    # layer. Values: "pypdf" (native), "vision", "ollama_vision",
    # "tesseract" (OCR fallbacks), None for non-PDFs and for
    # PDF/text extraction that pypdf handled natively.
    extraction_method: str | None = None


class VideoMaterialsSetIn(BaseModel):
    """Body for PUT /api/videos/{id}/materials — replace the entire selection.

    The Mac web app's picker and the iOS picker both PUT this shape
    when the user taps "Save".
    """

    material_ids: list[str] = Field(default_factory=list, max_length=10)


class VideoMaterialsOut(BaseModel):
    """Returned by GET /api/videos/{id}/materials.

    Includes both:
      - the user's CURRENT selection (via PocketVideoMaterial rows)
      - the full list of AVAILABLE materials for this video (the
        union of section-scope materials + video-scope siblings)
    so the picker sheet can render the full UI in one round-trip.
    """

    video_id: str
    selected_ids: list[str]
    available: list[VideoMaterialLink]
