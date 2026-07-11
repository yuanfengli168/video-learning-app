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
