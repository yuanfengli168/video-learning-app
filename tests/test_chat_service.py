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