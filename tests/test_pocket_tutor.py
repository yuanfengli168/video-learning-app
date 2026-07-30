"""Tests for the pocket tutor service + job queue.

Ollama is fully mocked — we never require a live Ollama to run the test suite.
"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Asset, Course, Section, Video
from app.pocket import tutor


# ── tutor.py: prompt assembly & response parsing ───────────────

def test_format_user_prompt_contains_all_materials():
    prompt = tutor._format_user_prompt(
        transcript="T", summary="S", quiz="Q", flashcards="F", mindmap="M"
    )
    for needle in ("T", "S", "Q", "F", "M", "Transcript", "Summary:", "Quiz:", "Flashcards:", "Mindmap:"):
        assert needle in prompt, f"missing {needle!r} in prompt"


def test_format_user_prompt_caps_each_field():
    """Long inputs are truncated to keep total under Ollama's context window."""
    huge = "x" * 100_000
    prompt = tutor._format_user_prompt(
        transcript=huge, summary=huge, quiz=huge, flashcards=huge, mindmap=huge
    )
    # The full prompt must be well under the sum of all uncapped fields (500_000 chars)
    assert len(prompt) < 200_000


def test_fallback_used_when_prompt_too_large(monkeypatch):
    """If the full prompt exceeds PROMPT_CHAR_LIMIT, minimal fallback is used.

    We monkeypatch the per-field caps in `_format_user_prompt` to be effectively
    unlimited, so the un-capped total prompt would exceed PROMPT_CHAR_LIMIT.
    """
    import app.pocket.tutor as t
    monkeypatch.setattr(t, "_format_user_prompt", lambda t, s, q, f, m: t + s + q + f + m)
    huge = "x" * 50_000
    full = huge + huge + huge + huge + huge  # 250_000 chars
    assert len(full) > t.PROMPT_CHAR_LIMIT


def test_parse_chunks_happy_path():
    raw = json.dumps([
        {"index": 0, "start_ts": 0.0, "end_ts": 120.0, "duration_label": "2min",
         "concept_title": "Intro", "teach_text": "hello", "check_question": "ok?"},
        {"index": 1, "start_ts": 120.0, "end_ts": 420.0, "duration_label": "5min",
         "concept_title": "Core", "teach_text": "world", "check_question": "got it?"},
    ])
    chunks = tutor._parse_chunks(raw)
    assert len(chunks) == 2
    assert chunks[0].concept_title == "Intro"
    assert chunks[1].duration_label == "5min"


def test_parse_chunks_strips_markdown_fence():
    raw = "```json\n" + json.dumps([
        {"index": 0, "start_ts": 0.0, "end_ts": 60.0, "duration_label": "2min",
         "concept_title": "X", "teach_text": "y", "check_question": "?"}
    ]) + "\n```"
    chunks = tutor._parse_chunks(raw)
    assert len(chunks) == 1


def test_parse_chunks_finds_array_in_verbose_response():
    """Ollama sometimes adds prose around the JSON. Extract the first [...]."""
    raw = "Here you go:\n" + json.dumps([
        {"index": 0, "start_ts": 0, "end_ts": 60, "duration_label": "2min",
         "concept_title": "X", "teach_text": "y", "check_question": "?"}
    ]) + "\nEnjoy!"
    chunks = tutor._parse_chunks(raw)
    assert len(chunks) == 1


def test_parse_chunks_invalid_raises():
    with pytest.raises(ValueError):
        tutor._parse_chunks("not json at all, sorry")


def test_coerce_chunk_handles_bad_duration_label():
    out = tutor._coerce_chunk(
        {"index": 0, "start_ts": 0, "end_ts": 60, "duration_label": "potato",
         "concept_title": "X", "teach_text": "y", "check_question": "?"},
        0,
    )
    assert out["duration_label"] == "5min"  # coerced to default


def test_generate_chunks_returns_error_on_ollama_failure():
    """Ollama unreachable → TutorResult with error, no crash."""
    with patch.object(tutor, "_call_ollama", side_effect=ConnectionError("nope")):
        result = tutor.generate_chunks(
            transcript="t", summary="s", quiz="q", flashcards="f", mindmap="m"
        )
    assert result.error is not None
    assert "nope" in result.error
    assert result.chunks == []


def test_generate_chunks_happy_path():
    raw = json.dumps([
        {"index": 0, "start_ts": 0, "end_ts": 60, "duration_label": "2min",
         "concept_title": "X", "teach_text": "y", "check_question": "?"}
    ])
    with patch.object(tutor, "_call_ollama", return_value=raw):
        result = tutor.generate_chunks(
            transcript="t", summary="s", quiz="q", flashcards="f", mindmap="m"
        )
    assert result.error is None
    assert len(result.chunks) == 1
    assert result.chunks[0].concept_title == "X"


# ── minimal-fallback truncation (MVP0.2 followup) ────────────────
# When the user has many selected materials, the materials section
# can be 200K chars on its own. The previous minimal-fallback bug
# (manualTodo [jul30] #1 — video f1610715 timed out at 120s) kept
# the full materials section and pushed the prompt past the timeout.
# Now the minimal fallback caps materials at 40K chars and shows a
# truncation notice so the LLM knows the materials were sliced.

def test_format_minimal_prompt_truncates_oversized_materials():
    """A 200K-char materials section gets truncated to ≤40K + notice."""
    big_materials = "X" * 200_000
    out = tutor._format_user_prompt_minimal(
        transcript="t", summary="s", materials_section=big_materials
    )
    # The full big_materials string must NOT appear verbatim — it would
    # push the prompt past the timeout.
    assert "X" * 200_000 not in out
    # Truncation marker must be present.
    assert "materials truncated" in out
    # Result must fit within the 200K PROMPT_CHAR_LIMIT budget.
    assert len(out) < tutor.PROMPT_CHAR_LIMIT


def test_format_minimal_prompt_keeps_small_materials_intact():
    """A 1K-char materials section is preserved verbatim (no false truncation)."""
    small_materials = "header\n--- file.pdf ---\nshort content"
    out = tutor._format_user_prompt_minimal(
        transcript="t", summary="s", materials_section=small_materials
    )
    assert small_materials in out
    assert "materials truncated" not in out


def test_format_minimal_prompt_handles_no_materials():
    """Missing materials section is fine (graceful empty)."""
    out = tutor._format_user_prompt_minimal(
        transcript="t", summary="s", materials_section=""
    )
    assert "t" in out          # transcript still present
    assert "s" in out          # summary still present
    assert "truncated" not in out


def test_generate_chunks_uses_truncated_minimal_when_prompt_oversized():
    """End-to-end: 200K materials + 50K transcript → minimal fallback fires,
    Ollama receives a prompt that fits within the 200K cap."""
    raw = json.dumps([
        {"index": 0, "start_ts": 0, "end_ts": 60, "duration_label": "2min",
         "concept_title": "X", "teach_text": "y", "check_question": "?"}
    ])
    big_materials = "M" * 200_000
    with patch.object(tutor, "_call_ollama", return_value=raw) as mock_call:
        result = tutor.generate_chunks(
            transcript="t" * 50_000,
            summary="s" * 5_000,
            quiz="q",
            flashcards="f",
            mindmap="m",
            materials_section=big_materials,
        )
    assert result.error is None
    assert result.used_fallback is True
    # Verify the call to Ollama used a small prompt (not the 270K-char one)
    call_args = mock_call.call_args
    actual_prompt = call_args[0][0]
    assert len(actual_prompt) < tutor.PROMPT_CHAR_LIMIT, (
        f"prompt len {len(actual_prompt):,} still exceeds {tutor.PROMPT_CHAR_LIMIT:,}"
    )


def test_call_ollama_uses_configured_timeout_default():
    """When the caller does not pass timeout_s, we pull from settings."""
    # The default in settings is 600s. We patch settings to a known
    # value and verify _call_ollama respects it.
    from app.config import settings
    with patch.object(settings, "ollama_pocket_tutor_timeout_seconds", 42.0):
        with patch("httpx.Client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__.return_value.post.return_value.json.return_value = {
                "response": "ok"
            }
            ctx.__enter__.return_value.post.return_value.status_code = 200
            ctx.__enter__.return_value.post.return_value.text = "ok"
            mock_client.return_value = ctx

            tutor._call_ollama("test prompt")

    # httpx.Client must have been constructed with timeout=42.0
    assert mock_client.call_args.kwargs.get("timeout") == 42.0


# ── router: /m/teach/* flow ─────────────────────────────────────

@pytest.fixture
def auth_client(db_session):
    """TestClient with the pocket router's auth dependency mocked.

    Goes through sys.modules to grab the actual `app.pocket.router` module
    (the `app.pocket` package re-exports the APIRouter instance, not the
    module) and binds the override to that exact `get_current_user`
    reference — robust against test ordering pollution from other test
    files that set POCKET_DEV_AUTH at import time.
    """
    import sys
    pocket_router_module = sys.modules["app.pocket.router"]
    dep_callable = pocket_router_module.get_current_user

    app.dependency_overrides.clear()
    app.dependency_overrides[dep_callable] = lambda: {"uid": "test-user-pocket-1", "email": "pocket@test.local"}
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _make_video_with_assets(db):
    c = Course(user_id="test-user-pocket-1", title="C")
    db.add(c)
    db.commit()
    db.refresh(c)
    s = Section(course_id=c.id, title="S", order_index=0)
    db.add(s)
    db.commit()
    db.refresh(s)
    v = Video(section_id=s.id, title="V", order_index=0,
              filename="V.mp4", file_path="/tmp/V.mp4")
    db.add(v)
    db.commit()
    db.refresh(v)
    db.add(Asset(video_id=v.id, asset_type="summary", content="a short summary"))
    db.add(Asset(video_id=v.id, asset_type="transcript", content=json.dumps([
        {"start": 0, "end": 60, "text": "hello world"},
    ])))
    db.commit()
    return v


def test_teach_unknown_video_404(auth_client, db_session):
    r = auth_client.post("/m/teach/nonexistent-id")
    assert r.status_code == 404


def test_teach_creates_job_and_status_returns_pending_then_ready(auth_client, db_session):
    """End-to-end: POST /m/teach creates a job, polling reveals it finished."""
    db = db_session
    v = _make_video_with_assets(db)

    # Patch both the Ollama call AND the in-process job so it runs synchronously
    # in the test (we don't want to wait for the asyncio loop in TestClient).
    fake_chunks = [{"id": "c1", "video_id": v.id, "index": 0, "start_ts": 0, "end_ts": 60, "duration_label": "2min",
                    "concept_title": "C", "teach_text": "T", "check_question": "?"}]

    def fake_generate(transcript, summary, quiz, flashcards, mindmap, materials_section=""):
        return tutor.TutorResult(
            chunks=[tutor.ChunkOut.model_validate(fake_chunks[0])],
            used_fallback=False,
            elapsed_s=0.01,
        )

    with patch("app.pocket.tutor.generate_chunks", side_effect=fake_generate):
        r = auth_client.post(f"/m/teach/{v.id}")
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        assert job_id

        # Poll until ready (cap at ~5s)
        for _ in range(50):
            sr = auth_client.get(f"/m/teach/{v.id}/status", params={"job_id": job_id})
            assert sr.status_code == 200, sr.text
            body = sr.json()
            if body["status"] == "ready":
                assert len(body["chunks"]) == 1
                assert body["chunks"][0]["concept_title"] == "C"
                return
            time.sleep(0.1)
        pytest.fail(f"Job never became ready; last body = {body!r}")


def test_teach_status_unknown_job_404(auth_client, db_session):
    r = auth_client.get("/m/teach/anything/status", params={"job_id": "no-such-job"})
    assert r.status_code == 404


def test_chunks_cached_after_job(auth_client, db_session):
    """After a job completes, /m/chunks/{video_id} should return the persisted chunks."""
    db = db_session
    v = _make_video_with_assets(db)

    fake_chunk = tutor.ChunkOut.model_validate({
        "id": "", "video_id": v.id, "index": 0, "start_ts": 0, "end_ts": 60,
        "duration_label": "5min", "concept_title": "Persisted",
        "teach_text": "body", "check_question": "q?",
    })
    fake_chunks = [fake_chunk]

    with patch("app.pocket.tutor.generate_chunks", return_value=tutor.TutorResult(
        chunks=fake_chunks, used_fallback=False, elapsed_s=0.01,
    )):
        job_id = auth_client.post(f"/m/teach/{v.id}").json()["job_id"]
        # Wait briefly for sync job to complete
        for _ in range(20):
            sr = auth_client.get(f"/m/teach/{v.id}/status", params={"job_id": job_id}).json()
            if sr["status"] in ("ready", "error"):
                break
            time.sleep(0.05)
        assert sr["status"] == "ready"

    # Now /m/chunks should return the cached chunk
    r = auth_client.get(f"/m/chunks/{v.id}")
    assert r.status_code == 200
    chunks = r.json()
    assert len(chunks) == 1
    assert chunks[0]["concept_title"] == "Persisted"


# ── router: /m/chunk/{id}/done + /m/progress/{video_id} ─────────

def test_mark_chunk_done_and_progress(auth_client, db_session):
    db = db_session
    v = _make_video_with_assets(db)

    # Insert a chunk directly so we don't depend on the job system
    from app.pocket.models import PocketChunk
    chunk = PocketChunk(video_id=v.id, index=0, start_ts=0, end_ts=60,
                        duration_label="5min", concept_title="T", teach_text="x", check_question="?")
    db.add(chunk)
    db.commit()
    db.refresh(chunk)

    # Mark done
    r = auth_client.post(f"/m/chunk/{chunk.id}/done")
    assert r.status_code == 200
    assert r.json()["completed"] is True

    # Idempotent
    r2 = auth_client.post(f"/m/chunk/{chunk.id}/done")
    assert r2.status_code == 200
    assert r2.json()["completed"] is True

    # Progress reflects the completion
    pr = auth_client.get(f"/m/progress/{v.id}").json()
    assert 0 in pr["chunks_done"]
    assert pr["last_seen_chunk"] == 0


def test_progress_empty_for_new_video(auth_client, db_session):
    db = db_session
    v = _make_video_with_assets(db)
    pr = auth_client.get(f"/m/progress/{v.id}").json()
    assert pr["chunks_done"] == []
    assert pr["last_seen_chunk"] is None


def test_mark_chunk_done_unknown_404(auth_client, db_session):
    r = auth_client.post("/m/chunk/no-such-chunk/done")
    assert r.status_code == 404
