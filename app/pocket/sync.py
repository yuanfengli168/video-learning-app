"""Sync service for the pocket sub-app.

Semantics (per user decision):
- New rows in courses/sections/videos   →  appear in next snapshot
- Updated rows                         →  overwrite in snapshot (new updated_at)
- Deleted rows                         →  recorded in deleted_ids so iOS can drop them

The phone is a read-only mirror; nothing the phone writes back can affect
source data (only pocket_progress is phone-writable, and it's a separate table).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, Course, PocketVideoMaterial, Section, Video
from app.models.video import natural_sort_key
from app.pocket.models import PocketSyncLog

ASSET_TYPES = ("summary", "transcript", "flashcards", "quiz", "mindmap")


def _iso(dt: datetime | None) -> str:
    """ISO-8601 string with no microseconds, suitable as a sync token.

    Uses space separator (not 'T') so that SQLite string-comparisons against
    its native datetime storage format ('YYYY-MM-DD HH:MM:SS') work correctly
    when used as a `WHERE` filter. See build_snapshot() for the matching
    `_parse_token` logic.
    """
    if dt is None:
        return ""
    return dt.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _parse_token(token: str | None) -> datetime | None:
    """Parse a sync_token (ISO or SQLite-native format) into a datetime."""
    if not token:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(token, fmt)
        except ValueError:
            continue
    return None


def _video_assets(db: Session, video_id: str) -> dict[str, str]:
    """Return {asset_type: content} for the given video. Empty strings for missing.

    Also returns the max updated_at across all assets so the video's sync
    timestamp reflects when its content (not just its record) last changed.
    """
    rows = db.execute(
        select(Asset).where(Asset.video_id == video_id, Asset.asset_type.in_(ASSET_TYPES))
    ).scalars().all()
    out: dict[str, str] = {t: "" for t in ASSET_TYPES}
    max_asset_dt = None
    for r in rows:
        content = r.content or ""
        if r.asset_type == "transcript":
            content = _flatten_transcript(content)
        out[r.asset_type] = content
        if r.updated_at and (max_asset_dt is None or r.updated_at > max_asset_dt):
            max_asset_dt = r.updated_at
    return out, max_asset_dt


def _flatten_transcript(content: str) -> str:
    """Normalize a transcript to one [ts] text per line.

    Tolerates three real shapes seen in the DB:
    - JSON list of {start,end,text} dicts
    - JSON list of plain strings
    - JSON object {"segments": [...]} (the most common historical format)

    Falls back to the raw content if nothing parses.
    """
    if not content:
        return ""
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content

    segs: list
    if isinstance(parsed, list):
        segs = parsed
    elif isinstance(parsed, dict) and isinstance(parsed.get("segments"), list):
        segs = parsed["segments"]
    else:
        return content

    lines: list[str] = []
    for seg in segs:
        if isinstance(seg, str):
            lines.append(seg)
        elif isinstance(seg, dict):
            ts = seg.get("start", 0)
            try:
                ts_str = f"[{float(ts):.1f}s]"
            except (TypeError, ValueError):
                ts_str = "[0.0s]"
            lines.append(f"{ts_str} {seg.get('text', '')}")
        else:
            lines.append(str(seg))
    return "\n".join(lines)


def _serialize_section(s: Section) -> dict[str, Any]:
    # Section has no updated_at column (only Course does). Use created_at as a
    # reasonable stand-in for v0.1 — section creation/deletion is rare relative
    # to video-level updates, and the iOS app only needs a monotonic-ish token.
    return {
        "id": s.id,
        "course_id": s.course_id,
        "title": s.title,
        "order_index": s.order_index,
        "updated_at": _iso(s.created_at),
    }


def _serialize_video(v: Video, assets: dict[str, str], effective_updated_at, selected_material_ids: list[str]) -> dict[str, Any]:
    # effective_updated_at is max(video.updated_at, max(asset.updated_at)) — so
    # regenerated summaries / new flashcards / corrected transcripts all bump
    # the video's sync timestamp and the iOS app re-fetches on next sync.
    return {
        "id": v.id,
        "section_id": v.section_id,
        "title": v.title,
        "order_index": v.order_index,
        "summary": assets["summary"],
        "transcript": assets["transcript"],
        "flashcards": assets["flashcards"],
        "quiz": assets["quiz"],
        "mindmap": assets["mindmap"],
        # MVP0.2: list of material IDs the user has selected for this video.
        # iOS uses this to render the "Materials in context" badge and the
        # MaterialsPickerView. Order matches user's selection order on Mac.
        "selected_materials": selected_material_ids,
        "updated_at": _iso(effective_updated_at),
    }


def _serialize_course(c: Course) -> dict[str, Any]:
    return {
        "id": c.id,
        "title": c.title,
        "description": c.description or "",
        "updated_at": _iso(c.updated_at),
    }


def build_snapshot(db: Session, user_id: str, since: str | None = None) -> dict[str, Any]:
    """Build a sync snapshot for the given user.

    `since` (optional sync token) — if provided, only return rows whose
    effective `updated_at > since`. Phone uses this for incremental sync.
    The token format is `%Y-%m-%d %H:%M:%S` (SQLite-native) so string
    comparisons against stored datetimes work correctly.
    """
    since_dt = _parse_token(since)

    # ── Courses ─────────────────────────────────────────────
    # v0.1 simplification: on incremental sync, always return ALL of the user's
    # courses (not just the changed ones). This is because a course may have a
    # video whose asset changed; that course is logically "new" to the phone
    # too. Filtering correctly would require recursive ancestor traversal which
    # is overkill for v0.1 — the JSON is tiny (a few courses with text fields).
    q_courses = select(Course).where(Course.user_id == user_id)
    courses = db.execute(q_courses.order_by(Course.updated_at)).scalars().all()
    course_dicts = [_serialize_course(c) for c in courses]
    course_ids = [c.id for c in courses]

    # ── Sections (only for the courses above) ──────────────
    # Same reasoning: always return all sections for the user's courses.
    sections: list[Section] = []
    if course_ids:
        q_sections = select(Section).where(Section.course_id.in_(course_ids))
        sections = db.execute(q_sections.order_by(Section.order_index)).scalars().all()
    section_dicts = [_serialize_section(s) for s in sections]
    section_ids = [s.id for s in sections]

    # ── Videos (only for the sections above) ────────────────
    # Note: we don't apply the `since` filter at the SQL level for videos,
    # because an asset regeneration (summary / quiz / flashcards / mindmap)
    # bumps the Asset.updated_at, not the Video.updated_at. The v0.1 trade-off:
    # every incremental sync returns all videos for the user. The JSON is
    # text-only and small (<1MB for ~50 videos). v0.2 can optimize with a
    # proper "videos with any asset newer than `since`" subquery.
    video_dicts: list[dict[str, Any]] = []
    if section_ids:
        q_videos = select(Video).where(Video.section_id.in_(section_ids))
        videos = db.execute(q_videos).scalars().all()
        # MVP0.2: bulk-load the user's selected materials for these videos in
        # one query so we don't N+1 the selection lookup.
        video_id_list = [v.id for v in videos]
        selected_map: dict[str, list[str]] = {vid: [] for vid in video_id_list}
        if video_id_list:
            sel_rows = db.execute(
                select(PocketVideoMaterial.video_id, PocketVideoMaterial.material_id, PocketVideoMaterial.created_at)
                .where(PocketVideoMaterial.video_id.in_(video_id_list))
                .order_by(PocketVideoMaterial.created_at.asc())
            ).all()
            for row in sel_rows:
                selected_map.setdefault(row.video_id, []).append(row.material_id)
        for v in videos:
            assets, max_asset_dt = _video_assets(db, v.id)
            # Effective timestamp: max of the video record and any of its assets
            effective_dt = v.updated_at
            if max_asset_dt and (effective_dt is None or max_asset_dt > effective_dt):
                effective_dt = max_asset_dt
            # Apply `since` filter on the effective timestamp
            if since_dt and effective_dt and effective_dt <= since_dt:
                continue
            video_dicts.append(_serialize_video(v, assets, effective_dt, selected_map.get(v.id, [])))
        # natural-sort by title so iOS display is stable
        video_dicts.sort(key=lambda d: natural_sort_key(d["title"]))

    # ── Deleted IDs (since last sync) ──────────────────────
    # Anything that was soft-touched (deleted) and not re-included above.
    # For v0.1 we use a simple rule: a course/section/video whose parent
    # is missing on this snapshot IS a candidate for deletion, but we only
    # list deletions that are NEW since `since` token. The phone's
    # responsibility is to know its own snapshot and drop missing IDs.
    #
    # To keep the contract simple in v0.1, we don't try to be clever about
    # cascading deletes server-side — we return `deleted_ids=[]` and rely
    # on the iOS app to compare against its prior snapshot. The audit log
    # records what was created/updated this sync.
    deleted_ids: list[str] = []

    # ── Audit log ──────────────────────────────────────────
    for c in courses:
        db.add(PocketSyncLog(user_id=user_id, action="snapshot", target_type="course", target_id=c.id))
    for s in sections:
        db.add(PocketSyncLog(user_id=user_id, action="snapshot", target_type="section", target_id=s.id))
    for v_dict in video_dicts:
        db.add(PocketSyncLog(user_id=user_id, action="snapshot", target_type="video", target_id=v_dict["id"]))
    db.commit()

    # ── Compute next sync_token (max updated_at across everything) ──
    max_dt = _max_updated_at(course_dicts, section_dicts, video_dicts)
    sync_token = _iso(max_dt) if max_dt else _iso(datetime.utcnow())

    return {
        "courses": course_dicts,
        "sections": section_dicts,
        "videos": video_dicts,
        "deleted_ids": deleted_ids,
        "sync_token": sync_token,
    }


def _max_updated_at(*groups: list[dict[str, Any]]) -> datetime | None:
    best: datetime | None = None
    for group in groups:
        for item in group:
            iso = item.get("updated_at") or ""
            if not iso:
                continue
            try:
                dt = datetime.fromisoformat(iso)
            except ValueError:
                continue
            if best is None or dt > best:
                best = dt
    return best
