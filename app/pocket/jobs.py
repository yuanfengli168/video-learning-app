"""In-process async job queue for the tutor.

Each /m/teach/{video_id} POST starts a job. The job runs Ollama in a
worker thread (it's blocking I/O), and writes its result to a dict
keyed by job_id. The phone polls /m/teach/{video_id}/status to read
the result.

No Celery, no Redis — one user, one process. Replaced by Celery in v0.2
if we ever go multi-user.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select

import app.database as _app_database
from app.models import Asset, Video
from app.pocket import tutor
from app.pocket.models import PocketChunk
from app.services.material_context import build_materials_section

log = logging.getLogger(__name__)


@dataclass
class Job:
    job_id: str
    user_id: str
    video_id: str
    status: str = "pending"   # "pending" | "ready" | "error"
    chunks: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        d["finished_at"] = self.finished_at.isoformat() if self.finished_at else None
        return d


# Module-level in-memory job store. Per-process; cleared on restart (acceptable
# for v0.1: the phone re-polls and starts a new job).
_jobs: dict[str, Job] = {}


def start_teach_job(user_id: str, video_id: str) -> str:
    """Kick off an async tutor job. Returns the new job_id immediately."""
    job_id = str(uuid.uuid4())
    _jobs[job_id] = Job(job_id=job_id, user_id=user_id, video_id=video_id)
    # asyncio.create_task needs a running loop; the router is async, so this is safe.
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run_job(job_id, user_id, video_id))
    except RuntimeError:
        # No running loop (e.g. called from sync code in a test). Run inline.
        _run_job_sync(job_id, user_id, video_id)
    return job_id


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


async def _run_job(job_id: str, user_id: str, video_id: str) -> None:
    """Async wrapper that runs the blocking Ollama call in a thread."""
    try:
        chunks = await asyncio.to_thread(_do_generate, user_id, video_id)
        job = _jobs.get(job_id)
        if job is None:
            return
        if chunks is None:
            job.status = "error"
            job.error = "Ollama call failed (see server logs)"
        else:
            job.status = "ready"
            job.chunks = chunks
        job.finished_at = datetime.utcnow()
    except Exception as e:  # noqa: BLE001
        log.exception("pocket.jobs: job %s crashed", job_id)
        job = _jobs.get(job_id)
        if job is not None:
            job.status = "error"
            job.error = str(e)
            job.finished_at = datetime.utcnow()


def _run_job_sync(job_id: str, user_id: str, video_id: str) -> None:
    """Sync fallback for tests / contexts without an event loop."""
    chunks = _do_generate(user_id, video_id)
    job = _jobs.get(job_id)
    if job is None:
        return
    if chunks is None:
        job.status = "error"
        job.error = "Ollama call failed (see server logs)"
    else:
        job.status = "ready"
        job.chunks = chunks
    job.finished_at = datetime.utcnow()


def _do_generate(user_id: str, video_id: str) -> list[dict[str, Any]] | None:
    """Load materials, call Ollama, persist chunks, return chunk dicts.

    Returns None on hard error; returns [] on empty-but-valid response.

    Uses _app_database.SessionLocal at call time so the conftest's
    monkey-patch of SessionLocal (for in-memory test DB) is honored.
    """
    db = _app_database.SessionLocal()
    try:
        v = db.execute(select(Video).where(Video.id == video_id)).scalar_one_or_none()
        if v is None:
            log.warning("pocket.jobs: video %s not found", video_id)
            return None

        # Load materials as text
        assets = {
            r.asset_type: (r.content or "")
            for r in db.execute(
                select(Asset).where(Asset.video_id == video_id)
            ).scalars().all()
        }
        # Re-use the same transcript-flattening that sync.py does
        transcript = _flatten_transcript(assets.get("transcript", ""))

        # MVP0.2: load user-selected materials for this video
        materials_ctx = build_materials_section(db, video_id, user_id=user_id)

        result = tutor.generate_chunks(
            transcript=transcript,
            summary=assets.get("summary", ""),
            quiz=assets.get("quiz", ""),
            flashcards=assets.get("flashcards", ""),
            mindmap=assets.get("mindmap", ""),
            materials_section=materials_ctx.prompt_section,
            # MVP0.2 followup #2 (anti-drift language): pass the
            # video's detected language so the tutor generates Chinese
            # chunks for a Chinese video. Default to 'en' when the
            # language is unknown (legacy / not yet detected) — per
            # user instruction 2026-07-30 we don't auto-detect from
            # the transcript text yet.
            language=v.language or "en",
        )

        if result.error:
            return None

        # Persist chunks (replacing any prior run for this video)
        db.query(PocketChunk).filter(PocketChunk.video_id == video_id).delete()
        for c in result.chunks:
            db.add(PocketChunk(
                video_id=video_id,
                index=c.index,
                start_ts=c.start_ts,
                end_ts=c.end_ts,
                duration_label=c.duration_label,
                concept_title=c.concept_title,
                # MVP0.2 followup #3: persist the structured teach_text
                # sections so the iOS app can render them as two cards.
                # Both nullable — the parser sets them to None when the
                # LLM didn't emit the headings (legacy path).
                teach_text=c.teach_text,
                teach_text_transcript=c.teach_text_transcript,
                teach_text_materials=c.teach_text_materials,
                transcript_quote=c.transcript_quote,
                check_question=c.check_question,
            ))
        db.commit()

        return [c.model_dump() for c in result.chunks]
    finally:
        db.close()


def _flatten_transcript(content: str) -> str:
    """If content is a JSON list of segments, join as '[ts] text' lines; else return as-is."""
    if not content:
        return ""
    try:
        segs = json.loads(content)
        if isinstance(segs, list):
            return "\n".join(
                f"[{seg.get('start', 0):.1f}s] {seg.get('text', '')}"
                for seg in segs
            )
    except (json.JSONDecodeError, TypeError):
        pass
    return content
