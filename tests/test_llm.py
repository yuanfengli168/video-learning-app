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


def test_extract_json_with_sure_preamble():
    """LLMs sometimes add a 'Sure! Here is the JSON:' preamble before
    the JSON object. The new strategy-3 (strip preamble) should handle
    that. This was the actual failure mode in the 4-video bulk upload
    on july 9 2026 (see doc/Blockers.md)."""
    text = 'Sure! Here is the JSON:\n\n{"summary": "ok", "mindmap": "ok"}'
    result = _extract_json(text)
    assert result == {"summary": "ok", "mindmap": "ok"}


def test_extract_json_with_certainly_preamble():
    """Another common preamble variation."""
    text = 'Certainly! Here you go:\n\n{"key": "value"}'
    result = _extract_json(text)
    assert result == {"key": "value"}


def test_extract_json_with_of_course_preamble():
    """Yet another variation, lower-case."""
    text = 'of course,\n{"answer": 42}'
    result = _extract_json(text)
    assert result == {"answer": 42}


def test_extract_json_failure_includes_raw_response():
    """When all strategies fail, the error message should include
    a preview of the raw response so the job log is self-explanatory.
    Without this, debugging requires re-running with extra logging."""
    text = "This response has no JSON at all, just prose about cats."
    with pytest.raises(ValueError) as exc_info:
        _extract_json(text)
    msg = str(exc_info.value)
    assert "Could not extract" in msg
    assert "cats" in msg, f"Error message should include raw response: {msg!r}"
    assert "len=" in msg, f"Error message should include response length: {msg!r}"


def test_extract_json_failure_truncates_long_response():
    """If the raw response is huge, the error preview should be
    truncated to keep job logs readable."""
    text = "x" * 1000  # 1000 chars of garbage
    with pytest.raises(ValueError) as exc_info:
        _extract_json(text)
    msg = str(exc_info.value)
    # Should mention truncation indicator
    assert "..." in msg or len(msg) < 2000


# ── generate_materials tests ──


FAKE_MATERIALS = {
    "summary": "# Summary\nKey points here.",
    "mindmap": "# Topic\n## Branch 1\n## Branch 2",
    "flashcards": [{"term": "AI", "definition": "Artificial Intelligence"}],
    "quiz": [{"question": "What is AI?", "options": ["A", "B", "C", "D"], "answer": "A", "answer_index": 0}],
    "topic_timestamps": [
        {"topic": "Topic", "start": 0, "end": 60},
        {"topic": "Branch 1", "start": 60, "end": 120},
    ],
}


def _mock_litellm_response(content: str) -> MagicMock:
    """Create a mock litellm.ModelResponse with the given content.

    Matches the shape the LiteLLM wrapper expects
    (response.choices[0].message.content).
    """
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = content
    return mock_resp


def test_generate_materials_success():
    """Should generate materials from a transcript."""
    transcript = {
        "segments": [{"start": 0.0, "end": 5.0, "text": "Welcome to the lecture"}],
        "language": "en",
    }

    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_litellm_response(json.dumps(FAKE_MATERIALS)),
    ):
        result = generate_materials(
            transcript, user_role=0, user_id="uid-admin"
        )

    assert result["summary"] == FAKE_MATERIALS["summary"]
    assert result["mindmap"] == FAKE_MATERIALS["mindmap"]
    assert len(result["flashcards"]) == 1
    assert len(result["quiz"]) == 1


def test_generate_materials_empty_transcript():
    """Should raise ValueError for empty transcript."""
    with pytest.raises(ValueError, match="Transcript is empty"):
        generate_materials({"segments": []}, user_role=0, user_id="u")


def test_generate_materials_code_fence_response():
    """Should handle LLM response with code fences."""
    transcript = {
        "segments": [{"start": 0.0, "end": 5.0, "text": "Test"}],
    }

    fenced_content = f"```json\n{json.dumps(FAKE_MATERIALS)}\n```"
    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_litellm_response(fenced_content),
    ):
        result = generate_materials(
            transcript, user_role=0, user_id="u"
        )

    assert result["summary"] == FAKE_MATERIALS["summary"]


def test_generate_materials_provider_error_propagates():
    """When LiteLLM raises, generate_materials raises LlmCallError(503)."""
    from app.services.llm import LlmCallError

    transcript = {"segments": [{"start": 0.0, "end": 5.0, "text": "Test"}]}

    with patch(
        "app.services.llm_providers.litellm.completion",
        side_effect=Exception("connection refused"),
    ):
        with pytest.raises(LlmCallError) as exc_info:
            generate_materials(transcript, user_role=0, user_id="u")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "provider_unavailable"


def test_generate_materials_rate_limited_raises_429():
    """Rate limit hit → LlmCallError(429) with retry_after."""
    from app.services.llm import LlmCallError
    from app.services import llm_quota

    transcript = {"segments": [{"start": 0.0, "end": 5.0, "text": "Test"}]}
    # FREE tier = 5/min. Make 5 calls, then the 6th raises.
    llm_quota.rate_limiter.reset_all()
    for _ in range(5):
        with patch(
            "app.services.llm_providers.litellm.completion",
            return_value=_mock_litellm_response(json.dumps(FAKE_MATERIALS)),
        ):
            generate_materials(transcript, user_role=2, user_id="rate-limited")

    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_litellm_response(json.dumps(FAKE_MATERIALS)),
    ) as mock:
        with pytest.raises(LlmCallError) as exc_info:
            generate_materials(transcript, user_role=2, user_id="rate-limited")

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["error"] == "rate_limited"
    assert exc_info.value.detail["retry_after_seconds"] > 0
    # Provider NOT called for the rejected attempt
    assert mock.call_count == 0
    llm_quota.rate_limiter.reset_all()


def test_generate_materials_invalid_json_response():
    """Should raise ValueError when LLM returns non-JSON."""
    transcript = {"segments": [{"start": 0.0, "end": 5.0, "text": "Test"}]}
    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_litellm_response("This is not JSON"),
    ):
        with pytest.raises(ValueError, match="Could not extract"):
            generate_materials(transcript, user_role=0, user_id="u")


def test_generate_summary():
    """generate_summary should return just the summary string."""
    transcript = {"segments": [{"start": 0.0, "end": 5.0, "text": "Test"}]}
    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_litellm_response(json.dumps(FAKE_MATERIALS)),
    ):
        result = generate_summary(
            transcript, user_role=0, user_id="u"
        )
    assert result == FAKE_MATERIALS["summary"]


def test_generate_mindmap():
    """generate_mindmap should return just the mindmap string."""
    transcript = {"segments": [{"start": 0.0, "end": 5.0, "text": "Test"}]}
    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_litellm_response(json.dumps(FAKE_MATERIALS)),
    ):
        result = generate_mindmap(
            transcript, user_role=0, user_id="u"
        )
    assert result == FAKE_MATERIALS["mindmap"]


def test_generate_flashcards():
    """generate_flashcards should return just the flashcards list."""
    transcript = {"segments": [{"start": 0.0, "end": 5.0, "text": "Test"}]}
    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_litellm_response(json.dumps(FAKE_MATERIALS)),
    ):
        result = generate_flashcards(
            transcript, user_role=0, user_id="u"
        )
    assert len(result) == 1
    assert result[0]["term"] == "AI"


def test_generate_quiz():
    """generate_quiz should return just the quiz list."""
    transcript = {"segments": [{"start": 0.0, "end": 5.0, "text": "Test"}]}
    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_litellm_response(json.dumps(FAKE_MATERIALS)),
    ):
        result = generate_quiz(
            transcript, user_role=0, user_id="u"
        )
    assert len(result) == 1
    assert result[0]["question"] == "What is AI?"


def test_generate_topic_timestamps():
    """generate_materials should return topic_timestamps when present."""
    transcript = {"segments": [{"start": 0.0, "end": 5.0, "text": "Test"}]}
    with patch(
        "app.services.llm_providers.litellm.completion",
        return_value=_mock_litellm_response(json.dumps(FAKE_MATERIALS)),
    ):
        result = generate_materials(
            transcript, user_role=0, user_id="u"
        )
    assert "topic_timestamps" in result
    assert len(result["topic_timestamps"]) == 2
    assert result["topic_timestamps"][0]["topic"] == "Topic"
    assert result["topic_timestamps"][0]["start"] == 0
    assert result["topic_timestamps"][0]["end"] == 60


def test_llm_prompt_includes_topic_timestamps_instruction():
    """The LLM system prompt should mention topic_timestamps."""
    from app.services.llm import GENERATION_SYSTEM_PROMPT
    assert "topic_timestamps" in GENERATION_SYSTEM_PROMPT
    assert "start" in GENERATION_SYSTEM_PROMPT
    assert "end" in GENERATION_SYSTEM_PROMPT
