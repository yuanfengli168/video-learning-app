"""Tests for v0.1.3 grading + favorites endpoints.

Ollama is fully mocked — we never require a live Ollama to run the test suite.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Asset, Course, Section, Video
from app.pocket import tutor
from app.pocket.models import PocketChunk, PocketProgress


@pytest.fixture
def auth_client(db_session):
    """Mock the pocket auth dep so it does not 401.

    The router module (`app.pocket.router`) imports
    `get_current_user_dev_or_real as get_current_user` at load time. That
    name is NOT visible on `app.pocket.router` (which is the APIRouter
    instance re-exported from __init__), so we go through sys.modules to
    grab the actual module and bind our override to the exact function
    reference FastAPI captured at route-registration time.

    Order-independent: works whether or not POCKET_DEV_AUTH=1 was set by
    another test module at import time, because we never rely on the
    function being actually invoked.
    """
    import sys
    pocket_router_module = sys.modules["app.pocket.router"]
    dep_callable = pocket_router_module.get_current_user

    app.dependency_overrides.clear()
    app.dependency_overrides[dep_callable] = lambda: {
        "uid": "test-user-pocket-3", "email": "pocket3@test.local"
    }
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _make_video(db):
    c = Course(user_id="test-user-pocket-3", title="C")
    db.add(c); db.commit(); db.refresh(c)
    s = Section(course_id=c.id, title="S", order_index=0)
    db.add(s); db.commit(); db.refresh(s)
    v = Video(section_id=s.id, title="V", order_index=0,
              filename="v.mp4", file_path="/tmp/v.mp4")
    db.add(v); db.commit(); db.refresh(v)
    db.add(Asset(video_id=v.id, asset_type="summary", content="A summary."))
    db.add(Asset(video_id=v.id, asset_type="transcript", content=json.dumps([
        {"start": 0, "end": 5, "text": "hello world"},
    ])))
    db.commit()
    return v


def _make_chunk(db, video, index=0, transcript_quote="", check_question="Q?",
                teach_text="A mini-lesson."):
    ch = PocketChunk(
        video_id=video.id, index=index, start_ts=0, end_ts=60,
        duration_label="5min", concept_title="C",
        transcript_quote=transcript_quote,
        teach_text=teach_text, check_question=check_question,
    )
    db.add(ch); db.commit(); db.refresh(ch)
    return ch


# ── grade_single (no network) ─────────────────────────────────

def test_grade_single_returns_verdict_and_explanation(monkeypatch):
    """grade_single should return a verdict from {got_it, partial, missed} + an explanation."""
    monkeypatch.setattr(tutor, "_call_ollama_grading", lambda prompt: {
        "verdict": "got_it",
        "explanation": "You nailed it.",
    })
    out = tutor.grade_single("The student said this.", "The canonical said that.")
    assert out["verdict"] == "got_it"
    assert "nailed" in out["explanation"]


def test_grade_single_normalizes_unknown_verdict(monkeypatch):
    """If Ollama returns an unknown verdict, we coerce to 'missed'."""
    monkeypatch.setattr(tutor, "_call_ollama_grading", lambda prompt: {
        "verdict": "kinda_close",
        "explanation": "Meh.",
    })
    out = tutor.grade_single("x", "y")
    assert out["verdict"] == "missed"


def test_grade_single_handles_ollama_failure(monkeypatch):
    """If Ollama errors, we return a clean miss + generic explanation."""
    def boom(_):
        raise ConnectionError("nope")
    monkeypatch.setattr(tutor, "_call_ollama_grading", boom)
    out = tutor.grade_single("x", "y")
    assert out["verdict"] == "missed"
    assert "error" in out or "fail" in out.lower()


def test_grade_batch_returns_one_per_input():
    """grade_batch aligns verdicts with input order, even if Ollama returns a partial list."""
    fake_response = json.dumps([
        {"verdict": "got_it", "explanation": "good"},
        {"verdict": "missed", "explanation": "wrong"},
    ])
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": fake_response}
    mock_resp.raise_for_status = lambda: None
    mock_resp.status_code = 200  # so the new "if r.status_code >= 400" check passes

    with patch("httpx.Client") as MockClient:
        ctx = MagicMock()
        ctx.__enter__.return_value.post.return_value = mock_resp
        ctx.__exit__.return_value = False
        MockClient.return_value = ctx

        results = tutor.grade_batch([
            {"user_answer": "a", "canonical_answer": "A"},
            {"user_answer": "b", "canonical_answer": "B"},
        ])
    assert len(results) == 2
    assert results[0]["verdict"] == "got_it"
    assert results[1]["verdict"] == "missed"


# ── HTTP: /m/chunk/{id}/done with body ─────────────────────────

def test_mark_done_with_answer_persists(auth_client, db_session):
    """POST /m/chunk/{id}/done with user_answer persists it to PocketProgress."""
    v = _make_video(db_session)
    ch = _make_chunk(db_session, v)
    r = auth_client.post(f"/m/chunk/{ch.id}/done", json={"user_answer": "my answer", "is_favorite": True})
    assert r.status_code == 200, r.text
    # Use a fresh query via the test's db_session (which the conftest patched
    # to the same in-memory DB the endpoint wrote to).
    db_session.expire_all()
    progress = db_session.query(PocketProgress).filter_by(video_id=v.id).first()
    assert progress is not None
    assert progress.user_answer == "my answer"
    assert progress.is_favorite is True


def test_mark_done_backward_compatible_no_body(auth_client, db_session):
    """POST /m/chunk/{id}/done with no body still works (backward compatible)."""
    v = _make_video(db_session)
    ch = _make_chunk(db_session, v)
    r = auth_client.post(f"/m/chunk/{ch.id}/done")
    assert r.status_code == 200
    db_session.expire_all()
    progress = db_session.query(PocketProgress).filter_by(video_id=v.id).first()
    assert progress is not None
    assert progress.user_answer == ""
    assert progress.is_favorite is False  # default


# ── HTTP: /m/chunk/{id}/feedback (single) ──────────────────────

def test_feedback_endpoint_grades_and_persists(auth_client, db_session, monkeypatch):
    """POST /m/chunk/{id}/feedback calls Ollama (mocked), persists verdict, returns it."""
    monkeypatch.setattr(tutor, "grade_single", lambda user_answer, canonical_answer: {
        "verdict": "partial",
        "explanation": "You got the gist but missed X.",
    })
    v = _make_video(db_session)
    ch = _make_chunk(db_session, v, check_question="What is the answer?")
    r = auth_client.post(f"/m/chunk/{ch.id}/feedback", json={
        "user_answer": "It's about X.",
        "canonical_answer": "It's about X and Y.",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "partial"
    assert "missed X" in body["explanation"]
    assert body["chunk_id"] == ch.id

    db_session.expire_all()
    progress = db_session.query(PocketProgress).filter_by(video_id=v.id).first()
    assert progress.last_ai_verdict == "partial"
    assert "missed X" in progress.last_ai_explanation
    assert progress.user_answer == "It's about X."


def test_feedback_endpoint_falls_back_to_teach_text(auth_client, db_session, monkeypatch):
    """When caller omits canonical_answer, endpoint derives it from teach_text +
    check_question so the grader has full context.

    Regression test for v0.1.3 bug where the endpoint was passing the
    check_question (a question, not an answer) as the canonical_answer,
    which confused Ollama into returning verdict=missed with empty
    explanation.
    """
    # Capture the prompt that grade_single builds; verify it contains
    # both the check_question and the teach_text.
    captured = {"prompt": None}

    def fake_ollama(prompt):
        captured["prompt"] = prompt
        return {"verdict": "got_it", "explanation": "Good job."}

    # Patch _call_ollama_grading (not grade_single) so the early-return
    # for empty answers and the fallback-explanation logic inside
    # grade_single still run.
    monkeypatch.setattr(tutor, "_call_ollama_grading", fake_ollama)

    v = _make_video(db_session)
    ch = _make_chunk(
        db_session, v,
        check_question="What was verified?",
        teach_text="The video verified that AI opponent could play chess.",
    )
    # No canonical_answer in body — endpoint must derive from chunk
    r = auth_client.post(f"/m/chunk/{ch.id}/feedback", json={
        "user_answer": "AI can play chess.",
    })
    assert r.status_code == 200, r.text
    # The canonical section of the prompt should contain BOTH the
    # question framing AND the teach text, not just the question.
    assert captured["prompt"] is not None
    assert "What was verified?" in captured["prompt"]
    assert "AI opponent could play chess" in captured["prompt"]


def test_feedback_empty_answer_does_not_call_ollama(auth_client, db_session, monkeypatch):
    """Empty / whitespace-only user_answer short-circuits with a clear
    'no answer' explanation instead of wasting an Ollama call."""
    called = {"count": 0}

    def fake_ollama(prompt):
        called["count"] += 1
        return {"verdict": "got_it", "explanation": "should not be called"}

    monkeypatch.setattr(tutor, "_call_ollama_grading", fake_ollama)

    v = _make_video(db_session)
    ch = _make_chunk(db_session, v, check_question="Q?")
    r = auth_client.post(f"/m/chunk/{ch.id}/feedback", json={"user_answer": "   "})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "missed"
    assert "No answer" in body["explanation"]
    assert called["count"] == 0


def test_feedback_empty_ollama_explanation_falls_back(auth_client, db_session, monkeypatch):
    """If Ollama returns a verdict but no explanation (broken model output),
    the endpoint substitutes a verdict-specific fallback so the UI never
    shows a blank feedback box."""
    def fake_ollama(prompt):
        # Ollama bug: returns verdict but empty explanation
        return {"verdict": "partial", "explanation": ""}

    monkeypatch.setattr(tutor, "_call_ollama_grading", fake_ollama)

    v = _make_video(db_session)
    ch = _make_chunk(db_session, v, check_question="Q?")
    r = auth_client.post(f"/m/chunk/{ch.id}/feedback", json={"user_answer": "x"})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "partial"
    assert body["explanation"]  # NOT empty — must have a fallback
    assert "Partially" in body["explanation"] or "gist" in body["explanation"]


def test_feedback_endpoint_404_on_unknown_chunk(auth_client, db_session):
    r = auth_client.post("/m/chunk/no-such-chunk/feedback", json={
        "user_answer": "x", "canonical_answer": "y",
    })
    assert r.status_code == 404


# ── HTTP: /m/chunks/grade-batch ────────────────────────────────

def test_grade_batch_endpoint(auth_client, db_session, monkeypatch):
    """POST /m/chunks/grade-batch returns aligned verdicts and persists them."""
    def fake_batch(items):
        return [
            {"verdict": "got_it", "explanation": f"good for {i}"}
            for i, _ in enumerate(items)
        ]
    monkeypatch.setattr(tutor, "grade_batch", fake_batch)
    v = _make_video(db_session)
    ch1 = _make_chunk(db_session, v, index=0)
    ch2 = _make_chunk(db_session, v, index=1)

    r = auth_client.post("/m/chunks/grade-batch", json={
        "items": [
            {"chunk_id": ch1.id, "user_answer": "a", "canonical_answer": "A"},
            {"chunk_id": ch2.id, "user_answer": "b", "canonical_answer": "B"},
        ]
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["verdicts"]) == 2
    assert all(v["verdict"] == "got_it" for v in body["verdicts"])


# ── HTTP: /m/chunk/{id}/favorite ──────────────────────────────

def test_favorite_toggle_persists(auth_client, db_session):
    """POST /m/chunk/{id}/favorite toggles is_favorite, returns the new state."""
    v = _make_video(db_session)
    ch = _make_chunk(db_session, v)
    # First toggle: not-favorited → favorited
    r1 = auth_client.post(f"/m/chunk/{ch.id}/favorite")
    assert r1.status_code == 200
    assert r1.json()["is_favorite"] is True
    # Second toggle: favorited → not-favorited
    r2 = auth_client.post(f"/m/chunk/{ch.id}/favorite")
    assert r2.json()["is_favorite"] is False


def test_favorite_404_on_unknown_chunk(auth_client, db_session):
    r = auth_client.post("/m/chunk/no-such-chunk/favorite")
    assert r.status_code == 404


# ── HTTP: /m/favorites/{video_id} ─────────────────────────────

def test_list_favorites(auth_client, db_session):
    v = _make_video(db_session)
    ch0 = _make_chunk(db_session, v, index=0)
    ch1 = _make_chunk(db_session, v, index=1)
    ch2 = _make_chunk(db_session, v, index=2)
    # Favorite chunks 0 and 2, not 1
    auth_client.post(f"/m/chunk/{ch0.id}/favorite")
    auth_client.post(f"/m/chunk/{ch2.id}/favorite")

    r = auth_client.get(f"/m/favorites/{v.id}")
    assert r.status_code == 200
    body = r.json()
    assert sorted(item["chunk_index"] for item in body["favorites"]) == [0, 2]


# ── HTTP: /m/progress/{video_id}/detail ───────────────────────

def test_progress_detail_returns_answers_and_favorites(auth_client, db_session):
    v = _make_video(db_session)
    ch0 = _make_chunk(db_session, v, index=0)
    ch1 = _make_chunk(db_session, v, index=1)
    # Mark ch0 done with answer + favorite
    auth_client.post(f"/m/chunk/{ch0.id}/done", json={"user_answer": "my a", "is_favorite": True})
    # Mark ch1 done with no extras
    auth_client.post(f"/m/chunk/{ch1.id}/done")

    r = auth_client.get(f"/m/progress/{v.id}/detail")
    assert r.status_code == 200
    body = r.json()
    items = {it["chunk_index"]: it for it in body["items"]}
    assert 0 in items and 1 in items
    assert items[0]["is_done"] is True
    assert items[0]["user_answer"] == "my a"
    assert items[0]["is_favorite"] is True
    assert items[1]["is_done"] is True
    assert items[1]["is_favorite"] is False


# ── Ollama-down error handling (v0.1.3 hardening) ─────────────────

import httpx as _httpx


def test_is_ollama_available_returns_true_when_reachable(monkeypatch):
    """When Ollama /api/tags returns 2xx, is_ollama_available returns (True, detail)."""
    fake_resp = MagicMock()
    fake_resp.status_code = 200

    def fake_get(self, url, **kwargs):
        return fake_resp

    monkeypatch.setattr(_httpx.Client, "get", fake_get)
    ok, detail = tutor.is_ollama_available()
    assert ok is True
    assert "200" in detail


def test_is_ollama_available_returns_false_on_connection_refused(monkeypatch):
    """When Ollama is unreachable, is_ollama_available returns (False, 'unreachable: ...')."""
    def fake_get(self, url, **kwargs):
        raise _httpx.ConnectError("Connection refused")

    monkeypatch.setattr(_httpx.Client, "get", fake_get)
    ok, detail = tutor.is_ollama_available()
    assert ok is False
    assert "unreachable" in detail


def test_is_ollama_available_returns_false_on_timeout(monkeypatch):
    def fake_get(self, url, **kwargs):
        raise _httpx.TimeoutException("timed out")

    monkeypatch.setattr(_httpx.Client, "get", fake_get)
    ok, detail = tutor.is_ollama_available()
    assert ok is False
    assert "timeout" in detail


def test_is_ollama_available_returns_false_on_5xx(monkeypatch):
    fake_resp = MagicMock()
    fake_resp.status_code = 503
    monkeypatch.setattr(_httpx.Client, "get", lambda self, url, **kwargs: fake_resp)
    ok, detail = tutor.is_ollama_available()
    assert ok is False
    assert "503" in detail


def test_grade_single_raises_OllamaUnavailableError_on_connect_error(monkeypatch):
    """_call_ollama_grading must convert httpx.ConnectError to OllamaUnavailableError."""
    def fake_httpx_call(self, url, **kwargs):
        raise _httpx.ConnectError("Connection refused")

    monkeypatch.setattr(_httpx.Client, "post", fake_httpx_call)
    with pytest.raises(tutor.OllamaUnavailableError) as excinfo:
        tutor.grade_single("test answer", "canonical")
    assert excinfo.value.kind == "unreachable"


def test_grade_single_raises_OllamaUnavailableError_on_timeout(monkeypatch):
    def fake_httpx_call(self, url, **kwargs):
        raise _httpx.TimeoutException("slow")
    monkeypatch.setattr(_httpx.Client, "post", fake_httpx_call)
    with pytest.raises(tutor.OllamaUnavailableError) as excinfo:
        tutor.grade_single("test answer", "canonical")
    assert excinfo.value.kind == "timeout"


def test_feedback_endpoint_returns_clean_response_when_ollama_down(
    auth_client, db_session, monkeypatch
):
    """POST /m/chunk/{id}/feedback must return 200 (not 500) when Ollama is unreachable.

    The response should have verdict=missed, a helpful explanation, and
    ollama_unavailable=True so the iOS UI can show a "Tutor offline" banner.
    """
    def fake_ollama(prompt):
        raise tutor.OllamaUnavailableError("unreachable", "Connection refused")

    monkeypatch.setattr(tutor, "_call_ollama_grading", fake_ollama)

    v = _make_video(db_session)
    ch = _make_chunk(db_session, v, check_question="Q?")
    r = auth_client.post(f"/m/chunk/{ch.id}/feedback", json={"user_answer": "x"})
    assert r.status_code == 200, r.text  # NOT 500
    body = r.json()
    assert body["verdict"] == "missed"
    assert body["ollama_unavailable"] is True
    assert "offline" in body["explanation"].lower() or "ollama" in body["explanation"].lower()


def test_feedback_endpoint_uses_specific_message_for_timeout(
    auth_client, db_session, monkeypatch
):
    """When Ollama times out (not just unreachable), the message says 'taking too long'."""
    def fake_ollama(prompt):
        raise tutor.OllamaUnavailableError("timeout", "60s exceeded")

    monkeypatch.setattr(tutor, "_call_ollama_grading", fake_ollama)
    v = _make_video(db_session)
    ch = _make_chunk(db_session, v, check_question="Q?")
    r = auth_client.post(f"/m/chunk/{ch.id}/feedback", json={"user_answer": "x"})
    assert r.status_code == 200
    body = r.json()
    assert body["ollama_unavailable"] is True
    assert "too long" in body["explanation"].lower() or "moment" in body["explanation"].lower()


def test_feedback_endpoint_does_not_persist_when_ollama_down(
    auth_client, db_session, monkeypatch
):
    """When Ollama is down, the user's typed answer is NOT persisted to the DB —
    the student can retry once Ollama is back without seeing a stale verdict."""
    def fake_ollama(prompt):
        raise tutor.OllamaUnavailableError("unreachable", "Connection refused")

    monkeypatch.setattr(tutor, "_call_ollama_grading", fake_ollama)
    v = _make_video(db_session)
    ch = _make_chunk(db_session, v, check_question="Q?")
    r = auth_client.post(f"/m/chunk/{ch.id}/feedback", json={"user_answer": "x"})
    assert r.status_code == 200

    db_session.expire_all()
    progress = db_session.query(PocketProgress).filter_by(video_id=v.id).first()
    # No progress row should be created when grading fails.
    assert progress is None


def test_grade_batch_returns_per_item_fallback_on_ollama_down(monkeypatch):
    """When Ollama is down, grade_batch returns one fallback dict per input item."""
    def fake_ollama(prompt):
        raise tutor.OllamaUnavailableError("unreachable", "nope")

    monkeypatch.setattr(tutor, "_call_ollama_grading", fake_ollama)

    results = tutor.grade_batch([
        {"user_answer": "a", "canonical_answer": "A"},
        {"user_answer": "b", "canonical_answer": "B"},
    ])
    assert len(results) == 2
    for r in results:
        assert r["verdict"] == "missed"
        # Should contain the unreachable-specific message
        assert "offline" in r["explanation"].lower() or "ollama" in r["explanation"].lower()
