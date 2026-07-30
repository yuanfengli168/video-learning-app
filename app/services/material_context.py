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
    materials: list[dict]  # [{filename, char_count, included: bool}]

    # Truncation warning for the UI
    truncated: bool


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

    for mat in ordered:
        if mat.status != "ready" or not mat.extracted_text:
            # Skip failed / not-ready materials
            metadata.append({
                "filename": mat.filename,
                "char_count": 0,
                "included": False,
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
        metadata.append({
            "filename": mat.filename,
            "char_count": included_chars,
            "included": True,
        })

    if not parts:
        return MaterialContext(prompt_section="", materials=metadata, truncated=truncated)

    header = (
        "\n\nUSER-UPLOADED MATERIALS (selected for this video):\n"
        "These are the user's reference materials. Use them as authoritative "
        "context alongside the video transcript. If the materials mention a "
        "concept that's NOT in the transcript, surface it in your teaching.\n"
    )
    return MaterialContext(
        prompt_section=header + "\n".join(parts),
        materials=metadata,
        truncated=truncated,
    )
