"""Tests for the chat/flashcard/quiz XSS escaping (2026-09-06).

The live-chat path in video.html interpolated raw strings into
innerHTML: the user's own message, the AI reply, the fetch error
text, and (flashcards/quiz tabs) the LLM-generated materials. A
message like `<img src=x onerror=alert(1)>` would execute on every
viewer's page. chat_history.html was already safe (own escapeHtml);
video.html was not.

These tests are template-source guards: they assert the escape calls
are present at each render site, so a future refactor can't silently
re-remove them (the same regression class as the bug itself).
"""

from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "templates" / "video.html"


def _src() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


# ── Live chat (video.html) ───────────────────────────────────────────────────

def test_chat_user_message_escaped():
    """The user's own message must go through escapeHtml before
    innerHTML — unescaped, a friend-tester typing
    <img src=x onerror=...> gets script execution on other viewers."""
    src = _src()
    assert "${escapeHtml(content)}" in src, (
        "video.html chat: user message must render via escapeHtml(content)"
    )
    assert "${content}" not in src, (
        "video.html chat: raw ${content} interpolation found — "
        "untrusted input must never reach innerHTML unescaped"
    )


def test_chat_ai_reply_escaped():
    """The AI reply must be escaped too — the LLM echoes user text
    back (e.g. 'explain this: <script>...'), so its output carries
    the same injection risk as user input."""
    src = _src()
    assert "${escapeHtml(data.ai_message.content)}" in src, (
        "video.html chat: AI reply must render via escapeHtml"
    )


def test_chat_error_message_escaped():
    """Fetch error text can carry server strings — escape it."""
    src = _src()
    assert "${escapeHtml(e.message)}" in src


# ── Materials tabs (LLM output — lower risk, same class) ────────────────────

def test_flashcards_escaped():
    """Flashcard term/definition come from LLM output derived from
    transcripts (which contain user-controlled text) — escape all
    interpolations, including the openChat() onclick (escapeJs)."""
    src = _src()
    assert "${escapeHtml(card.term)}" in src
    assert "${escapeHtml(card.definition)}" in src
    assert "openChat('${escapeJs(card.term)}')" in src, (
        "The openChat onclick interpolates into a JS string context — "
        "must use escapeJs (a quote in a term would break out and "
        "allow arbitrary JS)"
    )
    assert "openChat('${card.term}')" not in src


def test_quiz_escaped():
    """Quiz question/options — same class, same fix."""
    src = _src()
    assert "${escapeHtml(q.question)}" in src
    assert "${escapeHtml(opt)}" in src


# ── The chat history page was already safe — keep it that way ───────────────

def test_chat_history_already_escaped():
    """chat_history.html escapes message content at every render —
    guard against regression there too."""
    src = (
        Path(__file__).resolve().parents[1]
        / "app" / "templates" / "chat_history.html"
    ).read_text(encoding="utf-8")
    assert "${escapeHtml(m.content)}" in src
    # The definition of its escapeHtml (textContent-based) must survive
    assert "div.textContent" in src