"""Telemetry router — client-side UI event ingestion (2026-09-05).

Feeds the admin analytics dashboard + per-user usage pages. The web
UI batches events client-side (see static/js/telemetry.js) and POSTs
them here every ~10s / on page unload via `navigator.sendBeacon`.

Why a dedicated endpoint (not reusing log_event call sites):
  The events table is already the app's audit log — `log_event()` in
  `app/utils/events.py` is the single writer used by server-side hot
  paths (LLM calls, caption jobs). UI events (video plays, pauses,
  seeks, tab clicks, logins) have no server-side call site to hook,
  so the browser is the only place they can originate. This router is
  the trusted bridge: session-auth'd, strictly validated, size-capped.

Security / abuse design (friend-tester scale, but stranger-proof):
  1. Auth REQUIRED — unauthenticated beacons are dropped (401).
  2. Strict source allowlist (UI_EVENT_SOURCES) — a compromised page
     can't forge `services.*` audit events or spam arbitrary sources.
  3. Per-batch cap (MAX_EVENTS_PER_BATCH) + per-event context size
     cap — bounded write amplification per request.
  4. Level is ALWAYS "INFO" — UI events are observational, never
     WARNING/ERROR (those are server-side concepts).
  5. video_id is validated to exist + be visible to the user, so the
     beacon can't probe hidden catalog IDs (PAID_ONLY/ADMIN_ONLY).

Event contract (matches doc/roles-tiers-cheatsheet.md §B):
  source         video_id      context
  ─────────────  ───────────  ─────────────────────
  ui.login       —             —
  ui.player      <video_id>    {action: play|pause|seek|ended,
                                position_ms?, from_ms?, to_ms?}
  ui.materials   <video_id>    {tab: summary|mindmap|flashcards|...}
  ui.chat        <video_id>    —
  ui.actions     <video_id>    {action: transcribe|generate, model?}

Usage counting: the PAID usage page counts `services.llm_providers`
"LLM call succeeded" events per user (server-side truth), NOT these
ui.* events. UI events answer "what did they click"; LLM events
answer "what did they consume".
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.config import settings
from app.database import get_db
from app.models import Video
from app.models.course import Course
from app.models.section import Section
from app.utils.events import log_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])


# ── Validation constants ────────────────────────────────────────────────────

# Allowlisted event sources. Anything else is a 400. The `services.*`
# namespace is deliberately absent — those are server-side audit
# events and must never be client-forgeable.
UI_EVENT_SOURCES = frozenset({
    "ui.login",
    "ui.player",
    "ui.materials",
    "ui.chat",
    "ui.actions",
})

# Player actions (context.action when source == "ui.player").
UI_PLAYER_ACTIONS = frozenset({"play", "pause", "seek", "ended"})

# Action clicks (context.action when source == "ui.actions").
UI_ACTION_KINDS = frozenset({"transcribe", "generate"})

# Caps — see module docstring §security. 25 events is generous for a
# 10s batch even with seek-debounce off; context strings stay small.
MAX_EVENTS_PER_BATCH = 25
MAX_CONTEXT_CHARS = 500


class UIEvent(BaseModel):
    """One client-side event. Validated before touching the DB."""

    source: str = Field(min_length=3, max_length=32)
    video_id: str | None = Field(default=None, max_length=36)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("context")
    @classmethod
    def _context_size(cls, v: dict[str, Any]) -> dict[str, Any]:
        if not v:
            return v
        # Cheap size guard: serialize and measure. Over-cap → truncate
        # keys (keep it simple; UI contexts are tiny in practice).
        import json as _json

        try:
            raw = _json.dumps(v, ensure_ascii=False)
        except (TypeError, ValueError):
            raise ValueError("context must be JSON-serializable")
        if len(raw) > MAX_CONTEXT_CHARS:
            raise ValueError(f"context too large ({len(raw)} > {MAX_CONTEXT_CHARS} chars)")
        return v


class TelemetryBatch(BaseModel):
    """Batched beacon payload. The JS beacon flushes every ~10s."""

    events: list[UIEvent] = Field(min_length=1, max_length=MAX_EVENTS_PER_BATCH)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _validate_player_context(event: UIEvent) -> None:
    """ui.player events must carry a known action + numeric positions."""
    action = event.context.get("action")
    if action not in UI_PLAYER_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown player action: {action!r}",
        )
    for key in ("position_ms", "from_ms", "to_ms"):
        val = event.context.get(key)
        if val is None:
            continue
        if not isinstance(val, (int, float)) or val < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{key} must be a non-negative number",
            )


def _validate_action_context(event: UIEvent) -> None:
    """ui.actions events must carry a known action kind."""
    kind = event.context.get("action")
    if kind not in UI_ACTION_KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown action kind: {kind!r}",
        )


def _validate_materials_context(event: UIEvent) -> None:
    """ui.materials events must carry a non-empty tab name."""
    tab = event.context.get("tab")
    if not isinstance(tab, str) or not tab or len(tab) > 32:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ui.materials requires context.tab (1-32 chars)",
        )


def _validate_video_access(
    db: Session, user: dict[str, Any], video_id: str | None
) -> None:
    """video_id must exist AND be visible to this user's tier.

    Prevents the beacon from being used to probe PAID_ONLY/ADMIN_ONLY
    catalog IDs (existence + access oracle). Uses the same
    visibility-tier comparison as the rest of the app.
    """
    if video_id is None:
        return

    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unknown video_id",
        )

    from app.auth.roles import user_can_access_video

    user_role = user.get("role")
    if not user_can_access_video(user_role, video.visibility):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="video not accessible to your tier",
        )


# ── Endpoint ─────────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def ingest_telemetry(
    batch: TelemetryBatch,
    request: Request,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Ingest a batch of UI events from the browser beacon.

    Returns 202 with the count accepted — telemetry must never be a
    UX blocker, so the client treats any 2xx as success and never
    retries or surfaces errors to the user.

    Validation failures are 400s for the whole batch (all-or-nothing):
    a hostile client shouldn't get partial feedback about which event
    shapes probe the schema. Auth failure is the standard 401.
    """
    uid = user.get("uid", "")
    accepted = 0

    for event in batch.events:
        # 1. Source allowlist — the core forgery guard.
        if event.source not in UI_EVENT_SOURCES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown event source: {event.source!r}",
            )

        # 2. Per-source context shape validation.
        if event.source == "ui.player":
            _validate_player_context(event)
        elif event.source == "ui.actions":
            _validate_action_context(event)
        elif event.source == "ui.materials":
            _validate_materials_context(event)

        # 3. video_id existence + tier-access check (also covers the
        #    catalog-probe oracle). ui.login has no video.
        _validate_video_access(db, user, event.video_id)

    # All validated → write in one transaction. log_event never
    # raises; a DB hiccup degrades to stderr logging (by design).
    for event in batch.events:
        message = event.source
        if event.source == "ui.player":
            message = f"ui player {event.context.get('action', '?')}"
        elif event.source == "ui.actions":
            message = f"ui action {event.context.get('action', '?')}"
        elif event.source == "ui.materials":
            message = f"ui materials tab {event.context.get('tab', '?')}"
        elif event.source == "ui.chat":
            message = "ui chat message sent"
        elif event.source == "ui.login":
            message = "ui user logged in"

        log_event(
            db,
            level="INFO",
            source=event.source,
            message=message,
            user_id=uid,
            video_id=event.video_id,
            context=event.context or None,
        )
        accepted += 1

    db.commit()
    return {"accepted": accepted}