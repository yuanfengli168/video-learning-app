"""MVP0.2: build the LLM prompt section that inlines user-selected materials.

Used by:
  - `app/pocket/tutor.py` for /m/teach/{video_id} (iOS TeachMe)
  - `app/services/chat.py` for /api/chat/* (Mac Discuss tab)
  - `app/services/teach_real_world.py` for flashcard "Teach me real-world usage"

For each video, the user has selected N materials. We inline their
`extracted_text` (truncated per-material to keep total context small)
so the LLM can reference the user's uploaded source material.

Limits:
  - Per-material cap: 50,000 chars (~12K tokens, fits in glm-5.2:cloud
    context easily)
  - Total materials cap: 200,000 chars across all selected materials
  - If the cap is exceeded, we keep the first selected (chronologically)
    and drop later ones until we're under the cap. The user's intent is
    preserved for the highest-priority materials.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    PocketMaterial,
    PocketVideoMaterial,
)

log = logging.getLogger(__name__)

# Per-material cap (chars). Keeps the prompt reasonable while still
# letting the LLM see enough material to cite.
MAX_CHARS_PER_MATERIAL = 50_000

# Total materials cap (chars across all selected materials for one video).
# Leaves room for transcript (60K) + summary/quiz/etc (60K) + response
# generation (8K) = 128K used out of 1M Ollama context for glm-5.2:cloud.
MAX_CHARS_TOTAL_MATERIALS = 200_000


@dataclass
class MaterialContext:
    """The materials section to append to the tutor prompt."""

    # Formatted prompt section (starts with header; empty string if no
    # materials selected or all failed to extract).
    prompt_section: str

    # Metadata for UI display ("📄 2 materials in context")
    materials: list[dict]  # [{filename, char_count, included: bool, method}]

    # Truncation warning for the UI
    truncated: bool

    # MVP0.2 followup: warning surfaced when a selected material
    # required OCR (extraction_method in {"vision","ollama_vision",
    # "tesseract"}) but the tutor model is text-only. Most users
    # never see this (the OCR chain extracts usable text), but if
    # the OCR chain produced only garbage (e.g. all paths failed
    # silently), the UI shows this so the user knows to switch
    # to a vision-capable tutor LLM (llava, qwen2-vl, kimi-k3,
    # etc.) for full visual reasoning over the PDF.
    vision_required_hint: str | None = None


def build_materials_section(
    db: Session, video_id: str, user_id: str
) -> MaterialContext:
    """Look up selected materials for this video and build the prompt section.

    Returns a `MaterialContext` with the formatted text + metadata for
    the UI badge ("📄 2 materials · 15K chars") so the caller can
    surface what the LLM is seeing.

    Behavior:
      - If no materials are selected: returns empty prompt_section
      - If a material is in 'failed' status: skip it (don't include
        partial garbage)
      - If materials exceed MAX_CHARS_TOTAL_MATERIALS: include as many
        as fit (oldest-first), mark `truncated=True`
    """
    # Look up the user's current selection for this video
    selected_rows = db.execute(
        select(PocketVideoMaterial)
        .where(PocketVideoMaterial.video_id == video_id)
        .order_by(PocketVideoMaterial.created_at.asc())
    ).scalars().all()

    if not selected_rows:
        return MaterialContext(prompt_section="", materials=[], truncated=False)

    # Fetch the materials
    material_ids = [r.material_id for r in selected_rows]
    materials = db.execute(
        select(PocketMaterial).where(
            PocketMaterial.id.in_(material_ids),
            PocketMaterial.user_id == user_id,
        )
    ).scalars().all()

    # Sort by selection order (already ordered above by selection.created_at)
    material_map = {m.id: m for m in materials}
    ordered = [material_map[mid] for mid in material_ids if mid in material_map]

    # Build the section, respecting total cap
    parts: list[str] = []
    metadata: list[dict] = []
    total_chars = 0
    truncated = False
    ocr_materials_used = 0  # for the vision-required hint

    for mat in ordered:
        if mat.status != "ready" or not mat.extracted_text:
            # Skip failed / not-ready materials
            metadata.append({
                "filename": mat.filename,
                "char_count": 0,
                "included": False,
                "method": mat.extraction_method,
                "reason": mat.error_message or f"status: {mat.status}",
            })
            continue

        text = mat.extracted_text
        included_chars = min(len(text), MAX_CHARS_PER_MATERIAL)

        if total_chars + included_chars > MAX_CHARS_TOTAL_MATERIALS:
            truncated = True
            metadata.append({
                "filename": mat.filename,
                "char_count": included_chars,
                "included": False,
                "method": mat.extraction_method,
                "reason": "total context cap reached",
            })
            continue

        truncated_text = text[:MAX_CHARS_PER_MATERIAL]
        if len(text) > MAX_CHARS_PER_MATERIAL:
            truncated_text += (
                f"\n\n[... truncated: file has {len(text):,} chars, "
                f"showing first {MAX_CHARS_PER_MATERIAL:,} ...]"
            )
        parts.append(f"\n--- {mat.filename} ---\n{truncated_text}")
        total_chars += included_chars
        if mat.extraction_method in ("vision", "ollama_vision", "tesseract"):
            ocr_materials_used += 1
        metadata.append({
            "filename": mat.filename,
            "char_count": included_chars,
            "included": True,
            "method": mat.extraction_method,
        })

    if not parts:
        return MaterialContext(prompt_section="", materials=metadata, truncated=truncated)

    header = (
        "\n\nUSER-UPLOADED MATERIALS (selected for this video):\n"
        "These are the user's reference materials. Use them as authoritative "
        "context alongside the video transcript. If the materials mention a "
        "concept that's NOT in the transcript, surface it in your teaching.\n"
    )

    # MVP0.2 followup: if all selected materials required OCR (no
    # native text), and the tutor is text-only, compute a hint the
    # UI can surface. We never inject this into the prompt — the
    # tutor is reading the OCR'd text, which is fine. The hint is
    # informational ("for visual reasoning over the original PDF,
    # use a vision-capable model") and aimed at the human user.
    vision_hint = None
    if ocr_materials_used and len(parts) == ocr_materials_used:
        try:
            from app.config import settings
            from app.services.model_capabilities import probe_model_capabilities
            caps = probe_model_capabilities(settings.ollama_model)
            if not caps.is_vision:
                methods = sorted({
                    m.get("method") for m in metadata
                    if m.get("method") in ("vision", "ollama_vision", "tesseract")
                })
                vision_hint = (
                    f"{ocr_materials_used} selected material(s) required OCR "
                    f"(extracted via {', '.join(methods)}). Your tutor model "
                    f"`{settings.ollama_model}` is text-only — it reads the "
                    f"OCR'd text. For visual reasoning over the original PDF "
                    f"layout (tables, figures), switch to a vision-capable "
                    f"model (llava, qwen2-vl, kimi-k3)."
                )
        except Exception:
            pass  # best-effort hint; never fail the prompt build

    return MaterialContext(
        prompt_section=header + "\n".join(parts),
        materials=metadata,
        truncated=truncated,
        vision_required_hint=vision_hint,
    )
