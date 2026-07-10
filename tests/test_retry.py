"""Tests for app/services/retry.py — find failed jobs for retry."""

import json

from app.models import Course, Section, Video
from app.services.retry import (
    find_failed_generate_videos,
    find_failed_transcribe_videos,
    _safe_parse,
)


# ── _safe_parse (the JSON-tolerance helper) ─────────────────────────────────

def test_safe_parse_none_returns_none():
    """None input returns None (no crash, no default value)."""
    assert _safe_parse(None) is None


def test_safe_parse_empty_string_returns_none():
    """Empty string returns None (the column is non-null but could be '')."""
    assert _safe_parse("") is None


def test_safe_parse_malformed_json_returns_none():
    """Malformed JSON returns None instead of raising.

    Why: this function is called on every row in a retry-sweep query.
    One bad row should not abort the whole sweep."""
    assert _safe_parse("{not json") is None
    assert _safe_parse('{"unclosed":') is None


def test_safe_parse_non_dict_returns_none():
    """A JSON value that is not an object (e.g. an array, a string) is rejected.

    We always expect last_*_job to be a dict (the shape defined in
    app/jobs.py). Anything else is corruption we should skip, not crash on."""
    assert _safe_parse("[1, 2, 3]") is None
    assert _safe_parse('"just a string"') is None
    assert _safe_parse("42") is None


def test_safe_parse_valid_dict_returns_dict():
    """Valid JSON object is returned as-is."""
    parsed = _safe_parse('{"status": "failed", "error": "boom"}')
    assert parsed == {"status": "failed", "error": "boom"}


# ── find_failed_generate_videos ─────────────────────────────────────────────

def test_find_failed_generate_videos_empty_db(db_session):
    """Empty database returns an empty list, not None or an error."""
    result = find_failed_generate_videos(db_session)
    assert result == []


def test_find_failed_generate_videos_no_jobs(db_session):
    """Videos with NULL last_generate_job are not in the result.

    This is the common case for a freshly-uploaded video that hasn't
    started generating yet."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()
    section = Section(title="W1", course_id=course.id)
    db_session.add(section)
    db_session.flush()
    v = Video(title="fresh", filename="x.mp4", file_path="/tmp/x.mp4",
              section_id=section.id)
    db_session.add(v)
    db_session.commit()

    assert find_failed_generate_videos(db_session) == []


def test_find_failed_generate_videos_returns_only_failed(db_session):
    """Videos with status='completed' or 'running' are NOT in the result.

    We only want to retry the ones that actually failed."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()
    section = Section(title="W1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    cases = [
        ("completed", "done", None),
        ("running", "in-progress", None),
        ("failed", "broken", "x"),
        ("failed", "also-broken", "x"),
    ]
    for status, title, error in cases:
        job = {"status": status, "error": error}
        v = Video(
            title=title,
            filename="x.mp4",
            file_path="/tmp/x.mp4",
            section_id=section.id,
            last_generate_job=json.dumps(job),
        )
        db_session.add(v)
    db_session.commit()

    result = find_failed_generate_videos(db_session)
    assert len(result) == 2
    titles = sorted(r["title"] for r in result)
    assert titles == ["also-broken", "broken"]
    for r in result:
        assert r["error"] == "x"
        assert r["video_id"] is not None


def test_find_failed_generate_videos_handles_corrupt_json(db_session):
    """A video with a corrupt last_generate_job is skipped, not crashed on.

    Real scenario: a half-written column from a crash mid-write would
    leave invalid JSON. The retry sweep must tolerate it."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()
    section = Section(title="W1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    v_good = Video(
        title="good-failed",
        filename="x.mp4",
        file_path="/tmp/x.mp4",
        section_id=section.id,
        last_generate_job='{"status": "failed", "error": "boom"}',
    )
    v_bad = Video(
        title="bad-corrupt",
        filename="y.mp4",
        file_path="/tmp/y.mp4",
        section_id=section.id,
        last_generate_job='{"unclosed":',  # malformed
    )
    db_session.add(v_good)
    db_session.add(v_bad)
    db_session.commit()

    result = find_failed_generate_videos(db_session)
    assert len(result) == 1
    assert result[0]["title"] == "good-failed"


def test_find_failed_generate_videos_sorted_by_title(db_session):
    """Result is sorted by title for stable, predictable retry order."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()
    section = Section(title="W1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    for title in ["Charlie", "Alpha", "Bravo"]:
        v = Video(
            title=title,
            filename="x.mp4",
            file_path="/tmp/x.mp4",
            section_id=section.id,
            last_generate_job='{"status": "failed", "error": "x"}',
        )
        db_session.add(v)
    db_session.commit()

    result = find_failed_generate_videos(db_session)
    titles = [r["title"] for r in result]
    assert titles == ["Alpha", "Bravo", "Charlie"]


def test_find_failed_generate_videos_includes_error_and_failed_at(db_session):
    """The error message and completion timestamp are surfaced for the script to log."""
    import json
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()
    section = Section(title="W1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    job = {
        "status": "failed",
        "error": "Could not extract valid JSON from LLM response (len=0)",
        "completed_at": 1783608224.18,
    }
    v = Video(
        title="errored",
        filename="x.mp4",
        file_path="/tmp/x.mp4",
        section_id=section.id,
        last_generate_job=json.dumps(job),
    )
    db_session.add(v)
    db_session.commit()

    result = find_failed_generate_videos(db_session)
    assert len(result) == 1
    assert result[0]["error"] == "Could not extract valid JSON from LLM response (len=0)"
    assert result[0]["failed_at"] == 1783608224.18


# ── find_failed_transcribe_videos (parallel of the above) ──────────────────

def test_find_failed_transcribe_videos_returns_only_failed(db_session):
    """Same behavior as the generate variant, but for transcribe jobs."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()
    section = Section(title="W1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    cases = [
        ("completed", "transcribed", None),
        ("failed", "whisper-broken", "x"),
    ]
    for status, title, error in cases:
        job = {"status": status, "error": error}
        v = Video(
            title=title,
            filename="x.mp4",
            file_path="/tmp/x.mp4",
            section_id=section.id,
            last_transcribe_job=json.dumps(job),
        )
        db_session.add(v)
    db_session.commit()

    result = find_failed_transcribe_videos(db_session)
    assert len(result) == 1
    assert result[0]["title"] == "whisper-broken"


def test_find_failed_transcribe_videos_does_not_mix_with_generate(db_session):
    """A failed transcribe job is NOT returned by find_failed_generate_videos.

    The two are separate columns, so this should be obvious — but the test
    is the contract: callers can use the generate helper without worrying
    that it'll pick up transcribe failures too."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()
    section = Section(title="W1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    v = Video(
        title="transcribe-failed-generate-ok",
        filename="x.mp4",
        file_path="/tmp/x.mp4",
        section_id=section.id,
        last_transcribe_job='{"status": "failed", "error": "whisper boom"}',
        last_generate_job='{"status": "completed", "error": null}',
    )
    db_session.add(v)
    db_session.commit()

    assert find_failed_generate_videos(db_session) == []
    assert len(find_failed_transcribe_videos(db_session)) == 1
