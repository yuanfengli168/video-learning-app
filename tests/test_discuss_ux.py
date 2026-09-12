"""Discuss tab UX improvements (2026-09-12, user requests #1–#4).

Four user-reported issues on the video page's 💬 Discuss tab:

  1. Input box grew to the right instead of expanding downward
     (single-line <input>) → swapped to an auto-resizing <textarea>
     (1 row → ~6 rows, Enter sends / Shift+Enter newline).
  2. Assistant messages are markdown but rendered raw — users saw
     ## and ** litter → added a dependency-free, DOM-node-based
     markdown renderer (never innerHTML on LLM output; links are
     validated to http(s):// or root-relative only).
  3. Every page load created a NEW session — history vanished on
     logout/login, video switches, refreshes → the tab now resumes
     the user's most recent video-scope session (GET /sessions?
     video_id=…&scope=video, load messages, continue there).
  4. Input was fully disabled while waiting for the AI → only the
     Send button greys out now; the textarea stays active so users
     can type their next question mid-reply.

These tests pin the markup + JS contract server-side (rendered HTML
assertions). The behavioral flow (resume, markdown rendering) is
additionally covered by the API tests in tests/test_chat_router.py
(list filters) and manual verification.
"""

import io
from unittest.mock import patch

from fastapi.testclient import TestClient

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def _setup_video(paid_client: TestClient) -> str:
    """Course → section → one video. Returns video_id."""
    with _mock_auth():
        course_resp = paid_client.post(
            "/api/courses", json={"title": "Course"},
            headers=_auth_headers(),
        )
        course_id = course_resp.json()["course_id"]
        section_resp = paid_client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
        upload_resp = paid_client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", io.BytesIO(b"fake video"), "video/mp4")},
            headers=_auth_headers(),
        )
    return upload_resp.json()["video_id"]


def _get_video_html(paid_client: TestClient, video_id: str) -> str:
    with _mock_auth():
        resp = paid_client.get(f"/video/{video_id}")
    assert resp.status_code == 200, resp.text
    return resp.text


# ── 1. Textarea input (auto-resize) ───────────────────────────────────────


def test_discuss_input_is_textarea(paid_client: TestClient, db_session):
    """The Discuss input is a <textarea> with the auto-resize hook —
    not the old single-line <input> that scrolled text sideways."""
    html = _get_video_html(paid_client, _setup_video(paid_client))
    assert 'id="discuss-input"' in html
    assert "<textarea" in html and "rows=\"1\"" in html
    assert 'oninput="autoResizeDiscussInput(this)"' in html
    assert "function autoResizeDiscussInput" in html
    # The old single-line input is gone
    assert 'type="text"\n                                placeholder="Ask about the video' not in html
    # Shift+Enter = newline hint is present for discoverability
    assert "Shift+Enter" in html


# ── 2. Markdown rendering ─────────────────────────────────────────────────


def test_discuss_markdown_renderer_present(paid_client: TestClient, db_session):
    """Assistant bubbles render via the markdown renderer; user
    bubbles stay plain text (whitespace-pre-wrap)."""
    html = _get_video_html(paid_client, _setup_video(paid_client))
    assert "function renderDiscussMarkdown(" in html
    assert "function _renderMarkdownBlock(" in html
    assert "function _mdInline(" in html
    # Assistant bubbles carry the markdown class hook
    assert "discuss-markdown" in html
    # The legacy plain renderer is retained for other call sites
    assert "function renderDiscussTextWithCitations(" in html


def test_discuss_markdown_link_safety(paid_client: TestClient, db_session):
    """The renderer's only data-driven element (<a href>) validates
    URLs to http(s) or root-relative — the javascript: guard must be
    in the shipped JS."""
    html = _get_video_html(paid_client, _setup_video(paid_client))
    # The regex literal appears in the rendered script block
    assert "https?:" in html
    assert ".test(url))" in html


# ── 3. Session resume ──────────────────────────────────────────────────────


def test_discuss_resume_logic_present(paid_client: TestClient, db_session):
    """startDiscussSession first looks for an existing session via
    GET /sessions?video_id=…&scope=video and renders its history;
    only creates a new session when none exists."""
    html = _get_video_html(paid_client, _setup_video(paid_client))
    assert "renderDiscussHistory" in html
    assert "scope=video" in html
    assert "video-sessions" in html, "fallback create call still present"


# ── 4. Send-only disable while waiting ─────────────────────────────────────


def test_discuss_send_button_disabled_classes(paid_client: TestClient, db_session):
    """The Send button greys out via disabled: classes while waiting;
    the textarea is NOT disabled in sendDiscussMessage anymore."""
    html = _get_video_html(paid_client, _setup_video(paid_client))
    assert "disabled:bg-gray-300" in html
    assert "disabled:cursor-not-allowed" in html
    # The old `input.disabled = true` in the send path is gone — the
    # only remaining disables are in startDiscussSession's init try
    # (session bootstrap), not the per-message send.
    send_fn_start = html.find("async function sendDiscussMessage")
    send_fn_end = html.find("async function", send_fn_start + 10)
    send_body = html[send_fn_start:send_fn_end if send_fn_end > 0 else len(html)]
    assert "input.disabled = true" not in send_body, (
        "sendDiscussMessage must not disable the textarea (request #4)"
    )