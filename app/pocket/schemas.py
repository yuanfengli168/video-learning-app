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
    transcript_quote: str = ""
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


# ── v0.1.3: grading + favorites + typed answers ─────────────

# Verdict for AI grading of a student's answer
class FeedbackRequest(BaseModel):
    user_answer: str = ""
    canonical_answer: str = ""


class FeedbackResponse(BaseModel):
    chunk_id: str | None = None
    verdict: Literal["got_it", "partial", "missed"]
    explanation: str


class BatchFeedbackItem(BaseModel):
    chunk_id: str
    user_answer: str = ""
    canonical_answer: str = ""


class BatchFeedbackRequest(BaseModel):
    items: list[BatchFeedbackItem]


class BatchFeedbackResponse(BaseModel):
    verdicts: list[FeedbackResponse]


class MarkDoneWithAnswerRequest(BaseModel):
    """Body for /m/chunk/{id}/done that also persists typed answer + favorite."""
    user_answer: str = ""
    is_favorite: bool | None = None  # None = don't change, True/False = set


class FavoriteToggleResponse(BaseModel):
    chunk_id: str
    is_favorite: bool


class FavoritesOut(BaseModel):
    """Per-video favorited chunks (rich). Returned by GET /m/favorites/{video_id}.

    Each item carries the chunk concept title, the verbatim transcript quote,
    the student's typed answer, and the last AI verdict so the iOS
    Favorites screen can render standalone cards without re-querying.
    """
    video_id: str
    favorites: list["FavoriteItemOut"] = Field(default_factory=list)


class FavoriteItemOut(BaseModel):
    chunk_id: str
    chunk_index: int
    concept_title: str
    transcript_quote: str = ""
    user_answer: str = ""
    last_ai_verdict: str = ""
    last_ai_explanation: str = ""


class ProgressDetailOut(BaseModel):
    """Extended progress (v0.1.3): per-chunk rich detail.

    Returned by GET /m/progress/{video_id}/detail. Each item is one chunk
    with the student's typed answer, favorite flag, and the last AI
    verdict + explanation so the iOS app can render the "Review my
    answers" screen with a single round trip.
    """
    video_id: str
    items: list["ProgressDetailItemOut"] = Field(default_factory=list)


class ProgressDetailItemOut(BaseModel):
    chunk_id: str
    chunk_index: int
    concept_title: str
    is_done: bool
    user_answer: str = ""
    is_favorite: bool = False
    last_ai_verdict: str = ""
    last_ai_explanation: str = ""
