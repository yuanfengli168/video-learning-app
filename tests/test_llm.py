"""Tests for LLM service — Ollama integration and JSON extraction."""

import json
from unittest.mock import patch, MagicMock

import pytest

from app.services.llm import (
    _extract_json,
    generate_flashcards,
    generate_materials,
    generate_mindmap,
    generate_quiz,
    generate_summary,
)


# ── _extract_json tests ──


def test_extract_json_direct():
    """Should parse direct JSON string."""
    text = '{"key": "value"}'
    result = _extract_json(text)
    assert result == {"key": "value"}


def test_extract_json_with_code_fence():
    """Should extract JSON from markdown code fences."""
    text = '```json\n{"key": "value"}\n```'
    result = _extract_json(text)
    assert result == {"key": "value"}


def test_extract_json_with_plain_fence():
    """Should extract JSON from plain code fences."""
    text = '```\n{"key": "value"}\n```'
    result = _extract_json(text)
    assert result == {"key": "value"}


def test_extract_json_with_surrounding_text():
    """Should extract JSON from text with surrounding content."""
    text = 'Here is the result:\n{"key": "value"}\nDone.'
    result = _extract_json(text)
    assert result == {"key": "value"}


def test_extract_json_invalid():
    """Should raise ValueError for non-JSON text."""
    with pytest.raises(ValueError, match="Could not extract"):
        _extract_json("This is just plain text with no JSON")


def test_extract_json_nested():
    """Should handle nested JSON objects."""
    text = '{"outer": {"inner": "value"}, "list": [1, 2, 3]}'
    result = _extract_json(text)
    assert result["outer"]["inner"] == "value"
    assert result["list"] == [1, 2, 3]


# ── generate_materials tests ──


FAKE_MATERIALS = {
    "summary": "# Summary\nKey points here.",
    "mindmap": "# Topic\n## Branch 1\n## Branch 2",
    "flashcards": [{"term": "AI", "definition": "Artificial Intelligence"}],
    "quiz": [{"question": "What is AI?", "options": ["A", "B", "C", "D"], "answer": "A", "answer_index": 0}],
}


def _mock_ollama_response(content: str):
    """Create a mock httpx response with the given content."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": {"content": content}}
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def test_generate_materials_success():
    """Should generate materials from a transcript."""
    transcript = {
        "segments": [{"start": 0.0, "end": 5.0, "text": "Welcome to the lecture"}],
        "language": "en",
    }

    with patch("app.services.llm.httpx.post", return_value=_mock_ollama_response(json.dumps(FAKE_MATERIALS))):
        result = generate_materials(transcript)

    assert result["summary"] == FAKE_MATERIALS["summary"]
    assert result["mindmap"] == FAKE_MATERIALS["mindmap"]
    assert len(result["flashcards"]) == 1
    assert len(result["quiz"]) == 1


def test_generate_materials_empty_transcript():
    """Should raise ValueError for empty transcript."""
    with pytest.raises(ValueError, match="Transcript is empty"):
        generate_materials({"segments": []})


def test_generate_materials_code_fence_response():
    """Should handle LLM response with code fences."""
    transcript = {
        "segments": [{"start": 0.0, "end": 5.0, "text": "Test"}],
    }

    fenced_content = f"```json\n{json.dumps(FAKE_MATERIALS)}\n```"
    with patch("app.services.llm.httpx.post", return_value=_mock_ollama_response(fenced_content)):
        result = generate_materials(transcript)

    assert result["summary"] == FAKE_MATERIALS["summary"]


def test_generate_materials_http_error():
    """Should raise on HTTP error from Ollama."""
    transcript = {
        "segments": [{"start": 0.0, "end": 5.0, "text": "Test"}],
    }

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("Connection refused")

    with patch("app.services.llm.httpx.post", return_value=mock_resp):
        with pytest.raises(Exception, match="Connection refused"):
            generate_materials(transcript)


def test_generate_materials_invalid_json_response():
    """Should raise ValueError when LLM returns non-JSON."""
    transcript = {
        "segments": [{"start": 0.0, "end": 5.0, "text": "Test"}],
    }

    with patch("app.services.llm.httpx.post", return_value=_mock_ollama_response("This is not JSON")):
        with pytest.raises(ValueError, match="Could not extract"):
            generate_materials(transcript)


def test_generate_summary():
    """generate_summary should return just the summary string."""
    transcript = {"segments": [{"start": 0.0, "end": 5.0, "text": "Test"}]}
    with patch("app.services.llm.httpx.post", return_value=_mock_ollama_response(json.dumps(FAKE_MATERIALS))):
        result = generate_summary(transcript)
    assert result == FAKE_MATERIALS["summary"]


def test_generate_mindmap():
    """generate_mindmap should return just the mindmap string."""
    transcript = {"segments": [{"start": 0.0, "end": 5.0, "text": "Test"}]}
    with patch("app.services.llm.httpx.post", return_value=_mock_ollama_response(json.dumps(FAKE_MATERIALS))):
        result = generate_mindmap(transcript)
    assert result == FAKE_MATERIALS["mindmap"]


def test_generate_flashcards():
    """generate_flashcards should return just the flashcards list."""
    transcript = {"segments": [{"start": 0.0, "end": 5.0, "text": "Test"}]}
    with patch("app.services.llm.httpx.post", return_value=_mock_ollama_response(json.dumps(FAKE_MATERIALS))):
        result = generate_flashcards(transcript)
    assert len(result) == 1
    assert result[0]["term"] == "AI"


def test_generate_quiz():
    """generate_quiz should return just the quiz list."""
    transcript = {"segments": [{"start": 0.0, "end": 5.0, "text": "Test"}]}
    with patch("app.services.llm.httpx.post", return_value=_mock_ollama_response(json.dumps(FAKE_MATERIALS))):
        result = generate_quiz(transcript)
    assert len(result) == 1
    assert result[0]["question"] == "What is AI?"