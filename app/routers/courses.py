"""Course router — CRUD for courses and sections."""

import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.admin import require_capability
from app.auth.dependencies import get_current_user
from app.auth.roles import Capability
from app.config import settings
from app.database import get_db
from app.models import Asset, Course, Section, Video
from app.services.retry import (
    find_failed_generate_videos,
    find_failed_transcribe_videos,
)

router = APIRouter(prefix="/api/courses", tags=["courses"])


# ── Schemas ──


class CourseCreate(BaseModel):
    title: str
    description: str = ""


class CourseUpdate(BaseModel):
    title: str | None = None
    description: str | None = None


class SectionCreate(BaseModel):
    title: str
    order_index: int = 0


class SectionUpdate(BaseModel):
    """2026-09-12 (user report): sections had NO rename path — created
    once and stuck forever. Mirrors CourseUpdate's shape: all-None =
    no-op, so callers can PATCH a single field safely."""
    title: str | None = None
    order_index: int | None = None

# ── Course endpoints ──


@router.get("")
async def list_courses(
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List all courses for the current user."""
    courses = db.execute(
        select(Course).where(Course.user_id == user.get("uid", ""))
    ).scalars().all()

    return [
        {
            "id": c.id,
            "title": c.title,
            "description": c.description,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in courses
    ]


@router.post("")
async def create_course(
    body: CourseCreate,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(require_capability(Capability.MANAGE_OWN_COURSE)),
) -> dict[str, str]:
    """Create a new course."""
    course = Course(
        id=str(uuid.uuid4()),
        title=body.title,
        description=body.description,
        user_id=user.get("uid", ""),
    )
    db.add(course)
    db.commit()

    return {"course_id": course.id}


@router.get("/{course_id}")
async def get_course(
    course_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Get a course with its sections."""
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    return {
        "id": course.id,
        "title": course.title,
        "description": course.description,
        "sections": [
            {
                "id": s.id,
                "title": s.title,
                "order_index": s.order_index,
                "video_count": len(s.videos),
            }
            for s in course.sections
        ],
    }


@router.put("/{course_id}")
async def update_course(
    course_id: str,
    body: CourseUpdate,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    """Update a course."""
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    if body.title is not None:
        course.title = body.title
    if body.description is not None:
        course.description = body.description
    db.commit()

    return {"status": "updated"}


@router.delete("/{course_id}")
async def delete_course(
    course_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete a course and everything in it (cascades to sections,
    videos, assets, chat sessions).

    MVP2.0 — mirrors the video delete pattern. The DB-level cascade
    is already wired up via `ondelete="CASCADE"` on the FKs and
    `cascade="all, delete-orphan"` on the relationships, so deleting
    a course row at the DB level cleans up everything below it.
    The on-disk files are NOT cleaned up by the DB cascade — we
    walk the tree manually first and unlink each one (idempotent:
    missing files are fine).

    Returns 200 with a cascade summary so the UI can confirm
    what was deleted.

    Soft-delete with a trash folder / 30-day restore is a separate
    feature (manualTodo #8) — deferred to MVP3.
    """
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    # Walk the tree, unlink on-disk files, and count cascade targets.
    # We do the file cleanup BEFORE the DB delete so we can still
    # see the file_path strings on each Video row.
    files_deleted = 0
    files_missing = 0
    total_videos = 0
    total_assets = 0

    for section in course.sections:
        for video in section.videos:
            total_videos += 1
            total_assets += len(db.execute(
                select(Asset).where(Asset.video_id == video.id)
            ).scalars().all())
            # Unlink the on-disk file. Idempotent — a missing file
            # is not an error (e.g. admin cleanup, 0-byte rejection).
            file_path = Path(video.file_path)
            try:
                if file_path.exists():
                    file_path.unlink()
                    files_deleted += 1
                else:
                    files_missing += 1
            except OSError:
                files_missing += 1

    # Count chat sessions across all videos in the course
    video_ids = [v.id for s in course.sections for v in s.videos]
    total_sessions = 0
    if video_ids:
        from app.models import ChatSession
        total_sessions = len(db.execute(
            select(ChatSession).where(ChatSession.video_id.in_(video_ids))
        ).scalars().all())

    # Now actually delete — SQLAlchemy cascade handles the DB side
    db.delete(course)
    db.commit()

    return {
        "status": "deleted",
        "course_id": course_id,
        "deleted": {
            "files": files_deleted,
            "files_missing": files_missing,
            "videos": total_videos,
            "assets": total_assets,
            "chat_sessions": total_sessions,
        },
    }


@router.put("/{course_id}/sections/{section_id}")
async def update_section(
    course_id: str,
    section_id: str,
    body: SectionUpdate,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    """Rename / re-order a section (2026-09-12 — user report: sections
    were create-once, no edit path).

    Same ownership rules as every other section route: the section
    must exist under THIS course, and the requesting user must own
    the course. All-None body = no-op (200), mirroring CourseUpdate.
    """
    section = db.get(Section, section_id)
    if not section or section.course_id != course_id:
        raise HTTPException(
            status_code=404,
            detail="Section not found",
        )
    course = db.get(Course, course_id)
    if not course or course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(
                status_code=400, detail="Section title cannot be empty"
            )
        section.title = title
    if body.order_index is not None:
        section.order_index = body.order_index
    db.commit()

    return {"status": "updated"}


@router.delete("/{course_id}/sections/{section_id}")
async def delete_section(
    course_id: str,
    section_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete a section and all its videos (cascades to assets + chat).

    MVP2.0 — same pattern as delete_course and delete_video.
    Returns 200 with a cascade summary. 404 if the section doesn't
    exist or doesn't belong to the course; 403 if the user doesn't
    own the course.

    Soft-delete deferred to MVP3 (manualTodo #8).
    """
    section = db.get(Section, section_id)
    if not section or section.course_id != course_id:
        raise HTTPException(
            status_code=404,
            detail="Section not found",
        )
    course = db.get(Course, course_id)
    if not course or course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    # Walk the section's videos, unlink files, count cascade targets
    files_deleted = 0
    files_missing = 0
    total_videos = len(section.videos)
    total_assets = 0
    for video in section.videos:
        total_assets += len(db.execute(
            select(Asset).where(Asset.video_id == video.id)
        ).scalars().all())
        file_path = Path(video.file_path)
        try:
            if file_path.exists():
                file_path.unlink()
                files_deleted += 1
            else:
                files_missing += 1
        except OSError:
            files_missing += 1

    # Count chat sessions for these videos
    video_ids = [v.id for v in section.videos]
    total_sessions = 0
    if video_ids:
        from app.models import ChatSession
        total_sessions = len(db.execute(
            select(ChatSession).where(ChatSession.video_id.in_(video_ids))
        ).scalars().all())

    db.delete(section)
    db.commit()

    return {
        "status": "deleted",
        "section_id": section_id,
        "deleted": {
            "files": files_deleted,
            "files_missing": files_missing,
            "videos": total_videos,
            "assets": total_assets,
            "chat_sessions": total_sessions,
        },
    }


# ── Section endpoints ──


@router.post("/{course_id}/sections")
async def create_section(
    course_id: str,
    body: SectionCreate,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(require_capability(Capability.MANAGE_OWN_COURSE)),
) -> dict[str, str]:
    """Create a section in a course."""
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    section = Section(
        id=str(uuid.uuid4()),
        title=body.title,
        order_index=body.order_index,
        course_id=course_id,
    )
    db.add(section)
    db.commit()

    return {"section_id": section.id}


@router.get("/{course_id}/sections/{section_id}/videos")
async def list_section_videos(
    course_id: str,
    section_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List videos in a section."""
    section = db.get(Section, section_id)
    if not section or section.course_id != course_id:
        raise HTTPException(status_code=404, detail="Section not found")

    course = db.get(Course, course_id)
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    return [
        {
            "id": v.id,
            "title": v.title,
            "status": v.status,
            "duration": v.duration,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in section.videos
    ]

@router.post("/{course_id}/sections/{section_id}/retry-failed")
async def retry_failed_section_videos(
    course_id: str,
    section_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Re-queue the failed step (transcribe OR generate) for every
    video in this section whose status is 'error'.

    A video can fail in two places:
    - last_transcribe_job.status='failed' (e.g. 0-byte file,
      unsupported codec) — needs re-transcribing.
    - last_generate_job.status='failed' (e.g. LLM empty body,
      JSON parse error) — needs re-generating.

    We re-queue whichever step failed. For transcribe failures, the
    full auto-pipeline (transcribe → generate) runs again — the
    chained worker `_staggered_transcribe_job` handles both steps
    (2026-09-12 bugfix: this previously scheduled the bare
    transcribe worker, which silently skipped generation).
    For generate-only failures, we just call `_run_generate_job`.

    Used by the "Retry all failed" button on the course page. Each
    retry runs as a FastAPI BackgroundTask so the response is
    immediate; the UI polls /status per video to show progress.

    Returns:
        {retried: int, video_ids: [...], transcribe_retried: int,
         generate_retried: int} so the UI can show a useful
        summary like "Retrying 4 videos (3 transcribe, 1 generate)".
    """
    section = db.get(Section, section_id)
    if not section or section.course_id != course_id:
        raise HTTPException(status_code=404, detail="Section not found")

    course = db.get(Course, course_id)
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    # Find failed videos in this section, partitioned by which step
    # failed. A video that has BOTH transcribe and generate failed
    # only goes into the transcribe bucket — re-running transcribe
    # auto-pipelines to generate, so the generate job will be
    # re-run as a side effect.
    all_transcribe_failed = find_failed_transcribe_videos(db)
    all_generate_failed = find_failed_generate_videos(db)

    section_video_ids = {v.id for v in section.videos}
    transcribe_failed = {
        row["video_id"] for row in all_transcribe_failed
        if row["video_id"] in section_video_ids
    }
    generate_failed = {
        row["video_id"] for row in all_generate_failed
        if row["video_id"] in section_video_ids
        # Skip videos that also have a failed transcribe job — the
        # transcribe retry will handle them.
        and row["video_id"] not in transcribe_failed
    }

    if not transcribe_failed and not generate_failed:
        return {
            "retried": 0,
            "transcribe_retried": 0,
            "generate_retried": 0,
            "video_ids": [],
        }

    # Lazy import: the worker uses start_job + the in-memory tracker
    # to know the job is real (otherwise it bails early).
    from app.jobs import start_job, serialize_job
    from app.routers.generation import _run_generate_job

    retried_ids: list[str] = []

    # 1. Re-run transcribe for videos whose transcribe step failed.
    #    2026-09-12 bugfix: this used to schedule bare
    #    `_run_transcribe_job` — which does NOT chain into generate
    #    (that chaining lives in `_run_auto_pipeline`). Transcribe
    #    retries therefore produced a transcript but no materials
    #    (generated_at stayed NULL; the docstring below was wrong
    #    about this). Now we reuse the shared chained worker, same
    #    as retry-stuck: transcribe, then generate, in one task.
    for video_id in transcribe_failed:
        job = start_job(
            video_id, "transcribe",
            message="Retrying transcribe (then generate) via 'Retry all failed'...",
        )
        video = db.get(Video, video_id)
        if video:
            video.last_transcribe_job = serialize_job(job)
            video.status = "transcribing"
            db.commit()
        background_tasks.add_task(_staggered_transcribe_job, video_id, 0)
        retried_ids.append(video_id)

    # 2. Re-run generate for videos whose generate step failed
    #    (but transcribe already succeeded — the transcript is in
    #    the DB and just needs the LLM step again).
    for video_id in generate_failed:
        job = start_job(
            video_id, "generate",
            message="Retrying generate via 'Retry all failed'...",
        )
        video = db.get(Video, video_id)
        if video:
            video.last_generate_job = serialize_job(job)
            video.status = "generating"
            db.commit()
        # MVP2.1 patch: pass user_id + user_role. Earlier this was
        # called with only (video_id,) which silently failed inside
        # BackgroundTasks (TypeError swallowed). Symptom: status
        # stuck at 'generating', no LLM call, no error event.
        background_tasks.add_task(
            _run_generate_job,
            video_id,
            user.get("uid", ""),
            user.get("role", 2),
        )
        retried_ids.append(video_id)

    return {
        "retried": len(retried_ids),
        "transcribe_retried": len(transcribe_failed),
        "generate_retried": len(generate_failed),
        "video_ids": retried_ids,
    }


@router.post("/{course_id}/sections/{section_id}/retry-stuck")
async def retry_stuck_section_videos(
    course_id: str,
    section_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Re-queue STUCK videos in this section for the course OWNER
    (2026-09-12 — self-service counterpart to the admin requeue;
    2026-09-13 — extended to the full 4-form stuck taxonomy).

    'Stuck' (see _is_stuck_video for the ONE shared definition):
      A. queued >10 min — pipeline died at birth (restart ate the
         BackgroundTask)
      B. transcribing, job >30 min — worker died mid-transcribe
      C. generating, job >30 min — LLM call interrupted (be417367
         form: progress 90%, SIGKILL'd)
      D. ready but generated_at NULL — PERMANENT incomplete state,
         no timeout needed (the 2026-09-12 chain-bug victims)

    Repair is need-based: video already has a transcript Asset →
    only the generate step re-runs (no Whisper re-burn); no
    transcript → the full chained pipeline (transcribe→generate).
    Staggered 5s apart either way.

    Scopes to the section + ownership (same guard as retry-failed):
    only the course owner's own stuck videos, never other users'.
    'error' videos are deliberately NOT handled here — that's the
    retry-failed endpoint's domain.
    """
    section = db.get(Section, section_id)
    if not section or section.course_id != course_id:
        raise HTTPException(status_code=404, detail="Section not found")

    course = db.get(Course, course_id)
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your course")

    # ── Stuck detection (2026-09-13 taxonomy — see
    #    doc/launch-risk-audit-2026-09-12.md "审计后决策" #6) ──
    #    Based on FACTS, not the status field (the status field lies:
    #    'ready' with generated_at=NULL is unfinished; 'transcribing'
    #    from a dead worker never recovers on its own).
    stuck = [v for v in section.videos if _is_stuck_video(v)]

    if not stuck:
        return {"retried": 0, "video_ids": [], "message": "No stuck videos in this section."}

    from app.jobs import start_job, serialize_job

    retried_ids: list[str] = []
    for i, video in enumerate(stuck):
        # Repair path depends on what the video already has: a
        # transcript Asset means only the generate step is missing
        # (forms C/D — 'generating' timed out, or 'ready' without
        # materials) — skip the expensive Whisper re-run. No
        # transcript (forms A/B) → the full chained pipeline.
        has_transcript = (
            db.execute(
                select(Asset.id).where(
                    Asset.video_id == video.id,
                    Asset.asset_type == "transcript",
                )
            ).first()
            is not None
        )
        if has_transcript:
            job = start_job(
                video.id, "generate",
                message="Re-queueing stuck generation (owner self-service)...",
            )
            video.last_generate_job = serialize_job(job)
            video.status = "generating"
            db.commit()
            background_tasks.add_task(
                _staggered_generate_retry,
                video.id, i * 5,
                user.get("uid", ""), int(user.get("role", 2)),
            )
        else:
            job = start_job(
                video.id, "transcribe",
                message="Re-queueing stuck processing (owner self-service)...",
            )
            video.last_transcribe_job = serialize_job(job)
            video.status = "transcribing"
            db.commit()
            background_tasks.add_task(
                _staggered_transcribe_job, video.id, i * 5
            )
        retried_ids.append(video.id)

    return {
        "retried": len(retried_ids),
        "video_ids": retried_ids,
        "message": (
            f"Re-queued {len(retried_ids)} stuck video(s) — watch the "
            f"status badges turn to transcribing/ready over the next "
            f"few minutes."
        ),
    }


def _is_stuck_video(video: Video) -> bool:
    """The ONE definition of 'stuck' (2026-09-13). Shared by this
    endpoint and the course-page stuck_count so UI and API can never
    disagree (the lesson from the first-stuck-count drift).

    Stuck = unfinished (no generated_at) AND matching one of:
      A. status='queued' older than 10 min  — pipeline died at birth
         (server restart ate the BackgroundTask; today's 8 videos)
      B. status='transcribing', job older than 30 min — worker died
         mid-transcribe (kill/deadlock); the job JSON's started_at
         is the truth, transcribe_started_at may be stale
      C. status='generating', job older than 30 min — LLM call was
         interrupted (be417367: progress 90%, SIGKILL'd)
      D. status='ready' with generated_at NULL — PERMANENT fact, no
         timeout needed: the transcribe worker set 'ready' but the
         chained generate never ran/finished (the 2026-09-12 chain
         bug's 8 victims). Nothing will ever self-heal this.
    'error' is deliberately NOT stuck — that's retry-failed's domain
    (explicit failure, different semantics). 'ready' WITH generated_at
    is complete — excluded, no false positives.
    """
    from datetime import datetime, timedelta, timezone as _tz
    import json as _json

    if video.generated_at is not None:
        return False  # finished — never stuck

    now = datetime.now(_tz.utc).replace(tzinfo=None)

    def _job_started_older_than(job_json: str | None, minutes: int) -> bool:
        if not job_json:
            return False  # no job record → can't prove staleness; skip
        try:
            started = _json.loads(job_json).get("started_at")
        except (ValueError, TypeError):
            return False
        if not started:
            return False
        started_dt = datetime.fromtimestamp(float(started), _tz.utc).replace(
            tzinfo=None
        )
        return started_dt < now - timedelta(minutes=minutes)

    if video.status == "queued":
        created = video.created_at
        if created is None:
            return False
        if created.tzinfo is not None:
            created = created.replace(tzinfo=None)
        return created < now - timedelta(minutes=10)

    if video.status == "transcribing":
        return _job_started_older_than(video.last_transcribe_job, 30)

    if video.status == "generating":
        return _job_started_older_than(video.last_generate_job, 30)

    if video.status == "ready":
        # generated_at IS NULL here (checked above) → permanent
        # incomplete state. No timeout applies.
        return True

    return False


def _staggered_generate_retry(
    video_id: str,
    delay_seconds: int,
    user_id: str,
    user_role: int,
) -> None:
    """Sleep `delay_seconds`, then run ONLY the generate step for a
    video that already has a transcript (stuck forms C/D). Mirrors
    _staggered_transcribe_job's stagger rationale."""
    if delay_seconds > 0:
        import time as _time

        _time.sleep(min(delay_seconds, 300))
    from app.jobs import start_job, serialize_job
    from app.database import SessionLocal
    from app.models import Video

    db = SessionLocal()
    try:
        video = db.get(Video, video_id)
        if not video:
            return
        # The request handler already flipped status + started the
        # job; re-serialize here in case the worker runs after a
        # long stagger and the in-memory tracker was lost (fresh
        # worker process). Same pattern as _staggered_transcribe_job.
        job = start_job(
            video_id, "generate",
            message="Re-queueing stuck generation (owner self-service)...",
        )
        video.last_generate_job = serialize_job(job)
        video.status = "generating"
        db.commit()
    finally:
        db.close()

    from app.routers.generation import _run_generate_job

    _run_generate_job(video_id, user_id, user_role)


def _staggered_transcribe_job(video_id: str, delay_seconds: int) -> None:
    """Sleep `delay_seconds`, transcribe, then CHAIN INTO GENERATE.

    2026-09-12 bugfix (user report: retry button worked but the
    course page showed bare 'ready' with no T:/G: timing): this
    originally called only `_run_transcribe_job`, which does NOT
    chain generation — that chaining lives in `_run_auto_pipeline`.
    Result: stuck-video retries produced a transcript but no
    materials (generated_at stayed NULL, so the timing badge never
    rendered, and Summary/Flashcards/etc were missing).

    The generate step mirrors `_run_auto_pipeline`'s tail: mark the
    video 'generating' + start the job tracker, then look up the
    owner uid/role from the section → course chain (the request
    context is gone in a BackgroundTask) and call
    `_run_generate_job(video_id, uid, role)`.
    """
    if delay_seconds > 0:
        import time as _time

        _time.sleep(min(delay_seconds, 300))
    from app.jobs import start_job, serialize_job
    from app.routers.videos import _run_transcribe_job

    _run_transcribe_job(video_id, "base")

    # Transcribe failed? _run_transcribe_job set status='error' and
    # wrote the failure to the job tracker — nothing to chain into.
    from app.jobs import get_job

    job = get_job(video_id, "transcribe")
    if not job or job.get("status") != "completed":
        return

    from app.database import SessionLocal
    from app.models import User

    db = SessionLocal()
    try:
        video = db.get(Video, video_id)
        if not video:
            return
        video.status = "generating"
        gen_job = start_job(
            video_id, "generate", total=100,
            message="Auto-pipeline: starting LLM generation...",
        )
        video.last_generate_job = serialize_job(gen_job)
        db.commit()
    finally:
        db.close()

    # Owner uid/role from the DB (same chain as _run_auto_pipeline).
    owner_uid = ""
    owner_role = 2
    db2 = SessionLocal()
    try:
        v = db2.get(Video, video_id)
        if v and v.section and v.section.course and v.section.course.user_id:
            owner_uid = v.section.course.user_id
            owner_user = db2.get(User, owner_uid)
            if owner_user and owner_user.role is not None:
                owner_role = owner_user.role
    finally:
        db2.close()

    from app.routers.generation import _run_generate_job

    _run_generate_job(video_id, owner_uid, owner_role)
