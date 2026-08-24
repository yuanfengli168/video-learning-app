"""Audit-log helper for the `events` table (Day 5).

Why this exists:
  Pre-Day-5, the app uses Python's stdlib `logging` to emit INFO/WARNING/ERROR
  messages. Those messages go to stderr / log files but never to the database,
  so the operator can't answer "what happened at 14:32 yesterday?" without
  grep'ing through log files.

  This module introduces `log_event()` — the single entry point every hot path
  should call instead of bare `logger.info()` when the event is something an
  operator might want to query later.

Usage:
    from app.utils.events import log_event

    log_event(
        db,
        level="INFO",
        source="services.youtube_captions_job",
        message="caption download completed",
        video_id=video.id,
        context={"language": "en", "chars": 12345},
    )

Design rules:
  1. NEVER raise from log_event. If the DB write fails, we fall back to
     stderr logging so a broken audit table doesn't crash a real user request.
  2. Always also emit to the Python logger, so existing log files keep
     working for grep-based workflows.
  3. Caller must pass the db session — we don't open our own (avoids surprise
     transactions in background tasks that already have a session).
  4. `context` is JSON-serialized; non-serializable values are coerced via
     str() so we never fail on weird inputs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Event

logger = logging.getLogger(__name__)


# Valid levels — matches stdlib logging levels we care about.
VALID_LEVELS = frozenset({"INFO", "WARNING", "ERROR", "DEBUG"})


def _coerce_for_json(value: Any) -> Any:
    """Make value JSON-serializable. Datetimes become ISO strings."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, dict):
        return {str(k): _coerce_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_coerce_for_json(v) for v in value]
    return str(value)


def log_event(
    db: Session,
    level: str,
    source: str,
    message: str,
    *,
    user_id: str | None = None,
    video_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> str | None:
    """Insert one row into the `events` table and mirror to stdlib logging.

    Args:
        db: An active SQLAlchemy session (caller commits / rolls back).
        level: One of INFO / WARNING / ERROR / DEBUG (others are coerced
               to uppercase; invalid values become 'INFO').
        source: Dotted path identifying the emitting module, e.g.
                'services.llm_providers'. Used as a dashboard filter.
        message: Human-readable summary. Keep it short and grep-friendly.
        user_id: Optional Firebase UID of the user who triggered the event.
        video_id: Optional Video.id this event relates to.
        context: Optional dict of structured details (provider name,
                 latency_ms, error_type, etc.). Non-JSON values are coerced.

    Returns:
        The new Event.id on success, or None if the write failed (in which
        case a WARNING is also logged to stderr). Callers must not depend on
        the return value for control flow.
    """
    # Normalize level — be forgiving about case + casing mistakes.
    lvl = (level or "INFO").upper()
    if lvl not in VALID_LEVELS:
        lvl = "INFO"

    # Serialize context (may be None).
    context_json = ""
    if context:
        try:
            context_json = json.dumps(_coerce_for_json(context), ensure_ascii=False)
        except Exception:  # never let a helper bug crash the caller
            context_json = json.dumps({"_serialize_error": True})

    # Mirror to stdlib logging so existing log-file workflows still work.
    py_level = getattr(logging, lvl, logging.INFO)
    logger.log(
        py_level,
        "[%s] %s%s",
        source,
        message,
        f" ctx={context_json}" if context_json else "",
    )

    # Insert the row.
    try:
        event = Event(
            level=lvl,
            source=source[:64],
            message=message,
            user_id=user_id,
            video_id=video_id,
            context_json=context_json,
        )
        db.add(event)
        db.flush()  # get the id without committing
        return event.id
    except Exception as exc:
        # Never let the audit log break a real request. Log + swallow.
        logger.warning("log_event failed (%s); swallowing to protect caller", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return None


def recent_events(
    db: Session,
    *,
    level: str | None = None,
    source: str | None = None,
    video_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Event]:
    """Query recent events for the admin dashboard.

    Filters are AND-ed; results are ts DESC. Returns at most `limit` rows.
    """
    q = db.query(Event)
    if level:
        q = q.filter(Event.level == level.upper())
    if source:
        q = q.filter(Event.source == source)
    if video_id:
        q = q.filter(Event.video_id == video_id)
    return q.order_by(Event.ts.desc()).limit(limit).offset(offset).all()


def distinct_sources(db: Session) -> list[str]:
    """Return a sorted list of distinct source strings for the dashboard filter dropdown."""
    rows = db.query(Event.source).distinct().all()
    return sorted(r[0] for r in rows if r[0])
