"""Pydantic schemas for the /m/* endpoints.

All text fields only in v0.1 — no images, no video URLs, no chat history.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ── Sync snapshot ──────────────────────────────────────────────

class CourseOut(BaseModel):
    id: str
    title: str
    description: str = ""
    updated_at: datetime


class SectionOut(BaseModel):
    id: str
    course_id: str
    title: str
    order_index: int
    updated_at: datetime


class VideoOut(BaseModel):
    id: str
    section_id: str
    title: str
    order_index: int
    # Generated materials, embedded so the iOS app needs 1 call per sync.
    summary: str = ""
    transcript: str = ""           # full transcript as one string (v0.1 simplification)
    flashcards: str = ""           # JSON string; iOS decodes
    quiz: str = ""                 # JSON string; iOS decodes
    mindmap: str = ""
    updated_at: datetime


class SnapshotOut(BaseModel):
    courses: list[CourseOut]
    sections: list[SectionOut]
    videos: list[VideoOut]
    deleted_ids: list[str] = Field(
        default_factory=list,
        description="IDs (course|section|video) that were deleted since the last sync token.",
    )
    sync_token: str = Field(
        description="Pass as ?since=<token> on the next call. ISO-8601 max(updated_at).",
    )


# ── Tutor chunks ───────────────────────────────────────────────

DurationLabel = Literal["2min", "5min", "25min"]


class ChunkOut(BaseModel):
    id: str
    video_id: str
    index: int
    start_ts: float
    end_ts: float
    duration_label: DurationLabel
    concept_title: str
    teach_text: str
    check_question: str


class TeachJobCreated(BaseModel):
    job_id: str
    status: Literal["pending"] = "pending"


class TeachStatusOut(BaseModel):
    job_id: str
    status: Literal["pending", "ready", "error"]
    chunks: list[ChunkOut] | None = None
    error: str | None = None


# ── Progress ───────────────────────────────────────────────────

class ChunkDoneOut(BaseModel):
    chunk_id: str
    video_id: str
    completed: bool


class ProgressOut(BaseModel):
    video_id: str
    chunks_done: list[int] = Field(default_factory=list)
    last_seen_chunk: int | None = None
    last_seen_at: datetime | None = None
