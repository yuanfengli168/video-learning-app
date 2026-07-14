"""Tests for chat service — Ollama chat integration."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.chat import build_system_prompt, chat_with_ollama


def test_build_system_prompt():
    """Should build a system prompt with the concept name."""
    prompt = build_system_prompt("RAG")
    assert "RAG" in prompt
    assert "real-world" in prompt.lower()


def test_build_system_prompt_different_concepts():
    """Should include different concept names."""
    for concept in ["RAG", "Neural Networks", "Transformer"]:
        prompt = build_system_prompt(concept)
        assert concept in prompt


def test_chat_with_ollama_success():
    """Should return the assistant's response."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": {"content": "RAG is used in..."}}
    mock_resp.raise_for_status = MagicMock()

    with patch("app.services.chat.httpx.post", return_value=mock_resp):
        result = chat_with_ollama(
            messages=[{"role": "user", "content": "How does RAG work?"}],
            system_prompt="You are a tutor.",
        )

    assert result == "RAG is used in..."


def test_chat_with_ollama_no_system_prompt():
    """Should work without a system prompt."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": {"content": "Hello!"}}
    mock_resp.raise_for_status = MagicMock()

    with patch("app.services.chat.httpx.post", return_value=mock_resp):
        result = chat_with_ollama(
            messages=[{"role": "user", "content": "Hi"}],
        )

    assert result == "Hello!"


def test_chat_with_ollama_http_error():
    """Should raise on HTTP error."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("Connection refused")

    with patch("app.services.chat.httpx.post", return_value=mock_resp):
        with pytest.raises(Exception, match="Connection refused"):
            chat_with_ollama(
                messages=[{"role": "user", "content": "Hi"}],
            )


def test_chat_with_ollama_empty_response():
    """Should return empty string if response has no content."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": {}}
    mock_resp.raise_for_status = MagicMock()

    with patch("app.services.chat.httpx.post", return_value=mock_resp):
        result = chat_with_ollama(
            messages=[{"role": "user", "content": "Hi"}],
        )

    assert result == ""


def test_chat_with_ollama_multiple_messages():
    """Should send full conversation history to Ollama."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": {"content": "Response"}}
    mock_resp.raise_for_status = MagicMock()

    messages = [
        {"role": "user", "content": "What is RAG?"},
        {"role": "assistant", "content": "RAG is..."},
        {"role": "user", "content": "How is it used?"},
    ]

    with patch("app.services.chat.httpx.post", return_value=mock_resp) as mock_post:
        chat_with_ollama(messages=messages, system_prompt="You are a tutor.")

        # Verify the request body
        call_args = mock_post.call_args
        request_body = call_args[1]["json"]
        # System prompt + 3 messages = 4 total
        assert len(request_body["messages"]) == 4
        assert request_body["messages"][0]["role"] == "system"
        assert request_body["messages"][1]["role"] == "user"

# ─────────────────────────────────────────────────────────────────────────────
# Video-scope chat (MVP2.0 — "💬 Discuss" tab on the video page)
# ─────────────────────────────────────────────────────────────────────────────


def test_build_video_system_prompt_includes_title_and_materials():
    """The video-scope prompt must include the title + all 4 materials
    so the LLM can answer questions about any of them."""
    from app.services.chat import build_video_system_prompt
    prompt = build_video_system_prompt(
        video_title="Intro to RAG",
        summary="RAG combines retrieval with generation.",
        mindmap="- RAG\n  - retrieval\n  - generation",
        quiz="Q1: What does RAG stand for?\n  ✓ Retrieval-Augmented Generation",
        transcript="[00:00] Welcome to the RAG tutorial.\n[00:05] Today we'll cover...",
    )
    assert "Intro to RAG" in prompt
    assert "retrieval with generation" in prompt
    assert "RAG" in prompt  # mindmap
    assert "Retrieval-Augmented Generation" in prompt  # quiz
    assert "[00:00] Welcome" in prompt  # transcript


def test_build_video_system_prompt_friendly_defaults():
    """When materials are empty, the prompt should still be usable
    (not literally 'None' or empty sections)."""
    from app.services.chat import build_video_system_prompt
    prompt = build_video_system_prompt(video_title="New Upload")
    assert "New Upload" in prompt
    # Each empty section should get a placeholder, not the literal word "None"
    for placeholder in ["No summary", "No mindmap", "No quiz", "No transcript"]:
        assert placeholder in prompt, f"missing placeholder for {placeholder!r}"


def test_transcript_to_chat_text_short_video():
    """Short videos (≤ 600 segments) get formatted as `[mm:ss] text` lines."""
    from app.services.chat import transcript_to_chat_text
    segments = [
        {"start": 0.0, "end": 5.0, "text": "Hello world"},
        {"start": 65.5, "end": 70.0, "text": "After one minute"},
    ]
    text = transcript_to_chat_text(segments)
    assert "[00:00] Hello world" in text
    assert "[01:05] After one minute" in text
    # No "omitted" marker for short videos
    assert "omitted" not in text


def test_transcript_to_chat_text_long_video_truncated():
    """Long videos (1000+ segments) get head + tail with omitted marker."""
    from app.services.chat import transcript_to_chat_text
    segments = [{"start": float(i), "end": float(i + 1), "text": f"seg {i}"} for i in range(1000)]
    text = transcript_to_chat_text(segments)
    assert "omitted for length" in text
    # First few should still be there
    assert "seg 0" in text
    # Last few should still be there
    assert "seg 999" in text


def test_transcript_to_chat_text_empty():
    """Empty input returns empty string (not 'None')."""
    from app.services.chat import transcript_to_chat_text
    assert transcript_to_chat_text([]) == ""


def test_render_quiz_for_chat_basic():
    """Quiz JSON renders as Q: / ✓ A: lines."""
    from app.services.chat import render_quiz_for_chat
    quiz_json = '[{"question": "What is RAG?", "options": ["Retrieval", "Reactive"], "correct_index": 0}]'
    text = render_quiz_for_chat(quiz_json)
    assert "What is RAG?" in text
    assert "Retrieval" in text
    assert "Reactive" not in text  # only the correct answer is shown


def test_render_quiz_for_chat_empty():
    """Empty / invalid JSON returns empty string."""
    from app.services.chat import render_quiz_for_chat
    assert render_quiz_for_chat("") == ""
    assert render_quiz_for_chat("not json") == ""
    assert render_quiz_for_chat("[]") == ""


def test_video_scope_placeholder_constant_exists():
    """The placeholder string for video-scope sessions is exported
    from both the models package (so the router can use it) and is
    consistent with the chat service."""
    from app.models.chat import VIDEO_SCOPE_CONCEPT_PLACEHOLDER
    assert VIDEO_SCOPE_CONCEPT_PLACEHOLDER == "[whole video]"


# ── MVP3.0 Part B (manualTodo [jul14] #6) — parse_citations tests ────────────
#
# parse_citations() extracts [M:SS] / [H:MM:SS] markers from the AI's
# response text and returns them as a list of {start_seconds, display,
# offset, raw} dicts. The Discuss tab uses this on the backend to
# build a structured 'citations' field on the /api/chat/sessions/
# {id}/messages response, which the frontend then renders as
# clickable seek links.
#
# These tests lock the regex contract so the prompt + parser can
# evolve together without breaking the UI.


def test_parse_citations_empty_and_none():
    """parse_citations must tolerate empty / None input without raising."""
    from app.services.chat import parse_citations
    assert parse_citations("") == []
    assert parse_citations(None) == []


def test_parse_citations_no_markers():
    """Plain text with no markers returns an empty list."""
    from app.services.chat import parse_citations
    result = parse_citations("这是普通的中文文本，没有时间戳。")
    assert result == []


def test_parse_citations_single_mmss():
    """[M:SS] is converted to total seconds."""
    from app.services.chat import parse_citations
    result = parse_citations("视频在 [3:45] 提到 Claude Code 需要付费。")
    assert len(result) == 1
    assert result[0]["start_seconds"] == 225.0
    assert result[0]["display"] == "[3:45]"
    assert result[0]["offset"] == 4  # '视频在 ' is 4 chars (each Chinese char = 1)
    assert result[0]["raw"] == "[3:45]"


def test_parse_citations_multiple_mmss():
    """Multiple markers in one response all get parsed, in order."""
    from app.services.chat import parse_citations
    result = parse_citations("在 [3:45] 和 [8:12] 都有提到。")
    assert [c["start_seconds"] for c in result] == [225.0, 492.0]
    # Offsets must be ascending.
    assert result[0]["offset"] < result[1]["offset"]


def test_parse_citations_hhmmss():
    """[H:MM:SS] is converted to total seconds for > 1h videos."""
    from app.services.chat import parse_citations
    result = parse_citations("在 [1:23:45] 处讲解了 Opus 4.6。")
    assert len(result) == 1
    assert result[0]["start_seconds"] == 3600 + 23 * 60 + 45  # = 5025
    assert result[0]["display"] == "[1:23:45]"


def test_parse_citations_mixed_mmss_and_hhmmss():
    """M:SS and H:MM:SS can coexist; both are extracted in source order."""
    from app.services.chat import parse_citations
    result = parse_citations("See [1:23] and [1:30:45] for context.")
    assert [c["start_seconds"] for c in result] == [83.0, 3600 + 30 * 60 + 45]


def test_parse_citations_leading_zero():
    """[03:45] is accepted and parsed the same as [3:45]."""
    from app.services.chat import parse_citations
    result = parse_citations("At [03:45] we have the result.")
    assert len(result) == 1
    assert result[0]["start_seconds"] == 225.0


def test_parse_citations_fractional_seconds():
    """Fractional seconds (e.g. [1:23.5]) are preserved, not rounded to int."""
    from app.services.chat import parse_citations
    result = parse_citations("At [1:23.5] he paused.")
    assert len(result) == 1
    assert result[0]["start_seconds"] == 83.5


def test_parse_citations_rejects_range():
    """Ranges like [1:23-1:45] are NOT matched — the parser only
    handles single-point timestamps. Documented in the docstring."""
    from app.services.chat import parse_citations
    result = parse_citations("在 [1:23-1:45] 之间讨论了 Trae。")
    assert result == []


def test_parse_citations_rejects_approximate():
    """Tilde-prefixed approximations like [~1:23] are not matched."""
    from app.services.chat import parse_citations
    result = parse_citations("大概在 [~1:23] 左右提到了。")
    assert result == []


def test_parse_citations_rejects_trailing_s():
    """[1:23s] is not matched (the LLM sometimes adds a trailing s)."""
    from app.services.chat import parse_citations
    result = parse_citations("At [1:23s] the speaker switched topics.")
    assert result == []


def test_parse_citations_rejects_invalid_minutes_or_seconds():
    """Out-of-range values like [99:99] are silently ignored."""
    from app.services.chat import parse_citations
    assert parse_citations("Bad [99:99] time") == []
    # [99:00] is also invalid because minutes must be < 60.
    assert parse_citations("Bad [99:00] time") == []


def test_parse_citations_ignores_unrelated_brackets():
    """Bracket content that isn't a timestamp is not matched."""
    from app.services.chat import parse_citations
    result = parse_citations("Use [hello] or [cmd+s] for shortcuts.")
    assert result == []


def test_parse_citations_preserves_offsets_for_splicing():
    """Each citation carries the char offset of its match start, so
    the frontend can use it as a splice point. Verify with a known
    string."""
    from app.services.chat import parse_citations
    text = "012[3:45]6789[12:34]end"
    result = parse_citations(text)
    assert len(result) == 2
    # `[3:45]` starts at offset 3
    assert result[0]["offset"] == 3
    assert result[0]["display"] == "[3:45]"
    # `[12:34]` starts at offset 13 (after the first citation)
    assert result[1]["offset"] == 13
    assert result[1]["display"] == "[12:34]"
    assert result[1]["start_seconds"] == 12 * 60 + 34


def test_parse_citations_ordered_by_offset():
    """Even if a regex match would naturally produce them in source
    order, verify the result is sorted by offset (defense in depth
    against future regex changes)."""
    from app.services.chat import parse_citations
    result = parse_citations("[5:30] before [2:00] after [10:00]")
    offsets = [c["offset"] for c in result]
    assert offsets == sorted(offsets)
