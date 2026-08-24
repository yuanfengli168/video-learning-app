"""Tests for app/utils/events.py — the audit log helper.

Coverage:
  1. log_event inserts a row with all fields populated
  2. log_event normalizes bad level values to INFO
  3. log_event coerces datetime / non-JSON values in context
  4. log_event NEVER raises, even when DB write would fail
  5. recent_events filters by level/source/video_id and orders ts DESC
  6. recent_events respects limit + offset
  7. distinct_sources returns the sorted unique set
  8. log_event caps source at 64 chars (column constraint)
  9. log_event writes to stderr (stdlib logger mirror)
"""

import json
import logging
import uuid
from datetime import datetime

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.models import Event
from app.utils.events import (
    _coerce_for_json,
    distinct_sources,
    log_event,
    recent_events,
)


# ─────────────────────────────────────────────────────────────────────────
# _coerce_for_json
# ─────────────────────────────────────────────────────────────────────────


def test_coerce_handles_datetimes_and_nested_dicts():
    """datetime → ISO string; nested structures walked recursively."""
    dt = datetime(2026, 1, 2, 3, 4, 5)
    out = _coerce_for_json({"ts": dt, "list": [dt, "x"], "n": 1})
    assert out == {"ts": "2026-01-02T03:04:05", "list": ["2026-01-02T03:04:05", "x"], "n": 1}


def test_coerce_handles_arbitrary_objects_via_str():
    """Unknown types fall back to str() so they don't break JSON encoding."""
    class Foo:
        def __str__(self):
            return "foo!"
    assert _coerce_for_json(Foo()) == "foo!"


# ─────────────────────────────────────────────────────────────────────────
# log_event: insertion behavior
# ─────────────────────────────────────────────────────────────────────────


def test_log_event_inserts_row_with_all_fields(db_session):
    eid = log_event(
        db_session,
        level="INFO",
        source="test.module",
        message="hello",
        user_id="uid-x",
        video_id="vid-y",
        context={"provider": "ollama", "ms": 234},
    )
    db_session.commit()

    assert eid is not None
    row = db_session.get(Event, eid)
    assert row is not None
    assert row.level == "INFO"
    assert row.source == "test.module"
    assert row.message == "hello"
    assert row.user_id == "uid-x"
    assert row.video_id == "vid-y"
    assert json.loads(row.context_json) == {"provider": "ollama", "ms": 234}


def test_log_event_normalizes_lowercase_level(db_session):
    """Lowercase / mixed-case levels are uppercased."""
    eid = log_event(db_session, level="warning", source="t", message="x")
    db_session.commit()
    assert db_session.get(Event, eid).level == "WARNING"


def test_log_event_invalid_level_becomes_info(db_session):
    """Garbage level strings don't crash — they're coerced to INFO."""
    eid = log_event(db_session, level="banana", source="t", message="x")
    db_session.commit()
    assert db_session.get(Event, eid).level == "INFO"


def test_log_event_caps_source_at_64_chars(db_session):
    """source column is VARCHAR(64); longer strings are truncated."""
    long_source = "x" * 200
    eid = log_event(db_session, level="INFO", source=long_source, message="y")
    db_session.commit()
    assert db_session.get(Event, eid).source == "x" * 64


def test_log_event_never_raises_on_db_failure(db_session, monkeypatch):
    """Even if the Session.add explodes, log_event returns None and does
    not propagate — the audit log must never break a real request."""
    def boom(*a, **kw):
        raise SQLAlchemyError("simulated db failure")
    monkeypatch.setattr(db_session, "add", boom)

    result = log_event(db_session, level="INFO", source="t", message="x")
    assert result is None  # never raises


def test_log_event_handles_non_serializable_context(db_session):
    """Weird context values are coerced via str() so they don't break JSON."""
    class Weird:
        def __str__(self):
            return "weird!"

    eid = log_event(
        db_session, level="INFO", source="t", message="x",
        context={"obj": Weird(), "dt": datetime(2026, 1, 1)},
    )
    db_session.commit()
    row = db_session.get(Event, eid)
    parsed = json.loads(row.context_json)
    assert parsed["obj"] == "weird!"
    assert parsed["dt"] == "2026-01-01T00:00:00"


def test_log_event_mirrors_to_stdlib_logger(db_session, caplog):
    """The helper also emits to the Python logger so existing log files
    keep working for grep-based workflows."""
    with caplog.at_level(logging.INFO, logger="app.utils.events"):
        log_event(db_session, level="INFO", source="mirror.test", message="see me")
        db_session.commit()
    assert any("mirror.test" in rec.message and "see me" in rec.message for rec in caplog.records)


# ─────────────────────────────────────────────────────────────────────────
# recent_events
# ─────────────────────────────────────────────────────────────────────────


def test_recent_events_orders_ts_desc(db_session):
    """Newest events first (most recent at index 0)."""
    log_event(db_session, "INFO", "t", "first")
    db_session.commit()
    log_event(db_session, "INFO", "t", "second")
    db_session.commit()
    log_event(db_session, "INFO", "t", "third")
    db_session.commit()

    rows = recent_events(db_session, source="t")
    messages = [r.message for r in rows]
    assert messages == ["third", "second", "first"]


def test_recent_events_filters_by_level(db_session):
    log_event(db_session, "INFO", "t", "i1")
    log_event(db_session, "WARNING", "t", "w1")
    log_event(db_session, "ERROR", "t", "e1")
    db_session.commit()

    warnings = recent_events(db_session, level="WARNING", source="t")
    assert [r.level for r in warnings] == ["WARNING"]
    assert [r.message for r in warnings] == ["w1"]


def test_recent_events_filters_by_video_id(db_session):
    vid = str(uuid.uuid4())
    log_event(db_session, "INFO", "t", "for_vid", video_id=vid)
    log_event(db_session, "INFO", "t", "no_vid")
    db_session.commit()

    rows = recent_events(db_session, video_id=vid)
    assert len(rows) == 1
    assert rows[0].message == "for_vid"


def test_recent_events_respects_limit_and_offset(db_session):
    for i in range(5):
        log_event(db_session, "INFO", "pager", f"row-{i}")
    db_session.commit()

    page1 = recent_events(db_session, source="pager", limit=2, offset=0)
    page2 = recent_events(db_session, source="pager", limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    # pages don't overlap
    assert {r.message for r in page1}.isdisjoint({r.message for r in page2})


# ─────────────────────────────────────────────────────────────────────────
# distinct_sources
# ─────────────────────────────────────────────────────────────────────────


def test_distinct_sources_returns_sorted_unique(db_session):
    log_event(db_session, "INFO", "zzz.module", "x")
    log_event(db_session, "INFO", "aaa.module", "x")
    log_event(db_session, "INFO", "aaa.module", "y")  # duplicate
    db_session.commit()

    sources = distinct_sources(db_session)
    assert sources == ["aaa.module", "zzz.module"]
