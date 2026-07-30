"""Tests for the Ollama model-capabilities probe.

Verifies that:
  - vision-capable models (llava:13b) are recognised via /api/show
  - text-only models (glm-5.2:cloud) are NOT marked as vision
  - probe failures default to is_vision=False (safe fallback)
  - cache is honoured for the 5-min TTL
  - find_available_vision_model() iterates /api/tags and finds vision ones
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.model_capabilities import (
    ModelCapabilities,
    clear_capability_cache,
    find_available_vision_model,
    probe_model_capabilities,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts with a clean cache."""
    clear_capability_cache()
    yield
    clear_capability_cache()


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = str(json_data)
    return resp


def test_probe_marks_vision_model():
    """llava-style payload returns is_vision=True."""
    with patch("app.services.model_capabilities.httpx.Client") as mock_client:
        ctx = MagicMock()
        ctx.__enter__.return_value.post.return_value = _mock_response({
            "model": "llava:13b",
            "capabilities": ["vision", "completion"],
            "details": {"family": "llama"},
            "context_length": 4096,
        })
        mock_client.return_value = ctx

        caps = probe_model_capabilities("llava:13b")

    assert caps.is_vision is True
    assert caps.family == "llama"
    assert caps.context_length == 4096
    assert caps.error is None


def test_probe_marks_text_only_model():
    """glm-5.2:cloud payload returns is_vision=False."""
    with patch("app.services.model_capabilities.httpx.Client") as mock_client:
        ctx = MagicMock()
        ctx.__enter__.return_value.post.return_value = _mock_response({
            "model": "glm-5.2:cloud",
            "capabilities": ["completion"],
            "details": {"family": "glm5.2"},
            "context_length": 1000000,
        })
        mock_client.return_value = ctx

        caps = probe_model_capabilities("glm-5.2:cloud")

    assert caps.is_vision is False
    assert caps.family == "glm5.2"
    assert caps.context_length == 1_000_000


def test_probe_failure_defaults_to_text_only():
    """When /api/show fails, is_vision must default to False.

    Rationale: tutoring the LLM with OCR'd text is always safe;
    silently treating a model as vision-capable and injecting raw
    images would be a multipart footgun.
    """
    with patch("app.services.model_capabilities.httpx.Client") as mock_client:
        ctx = MagicMock()
        ctx.__enter__.return_value.post.side_effect = ConnectionError("Ollama down")
        mock_client.return_value = ctx

        caps = probe_model_capabilities("any-model")

    assert caps.is_vision is False
    assert caps.error and "ConnectionError" in caps.error


def test_probe_http_error_is_marked():
    with patch("app.services.model_capabilities.httpx.Client") as mock_client:
        ctx = MagicMock()
        ctx.__enter__.return_value.post.return_value = _mock_response(
            {"error": "model not found"}, status_code=404
        )
        mock_client.return_value = ctx

        caps = probe_model_capabilities("nonexistent:99b")

    assert caps.is_vision is False
    assert caps.error is not None
    assert "404" in caps.error


def test_cache_hits_avoid_repeating_http_call():
    """Second call within TTL must not hit the network."""
    with patch("app.services.model_capabilities.httpx.Client") as mock_client:
        ctx = MagicMock()
        ctx.__enter__.return_value.post.return_value = _mock_response({
            "model": "llava:13b",
            "capabilities": ["vision"],
            "details": {"family": "llama"},
        })
        mock_client.return_value = ctx

        probe_model_capabilities("llava:13b")
        probe_model_capabilities("llava:13b")
        probe_model_capabilities("llava:13b")

    # Only one underlying HTTP call
    assert ctx.__enter__.return_value.post.call_count == 1


def test_force_refresh_bypasses_cache():
    with patch("app.services.model_capabilities.httpx.Client") as mock_client:
        ctx = MagicMock()
        ctx.__enter__.return_value.post.return_value = _mock_response({
            "model": "llava:13b",
            "capabilities": ["vision"],
            "details": {"family": "llama"},
        })
        mock_client.return_value = ctx

        probe_model_capabilities("llava:13b")
        probe_model_capabilities("llava:13b", force_refresh=True)
        probe_model_capabilities("llava:13b", force_refresh=True)

    assert ctx.__enter__.return_value.post.call_count == 3


def test_find_available_vision_model_returns_first_match():
    """Iterates /api/tags, returns the first vision-capable model."""
    with patch("app.services.model_capabilities.httpx.Client") as mock_client:
        ctx = MagicMock()
        ctx.__enter__.return_value.get.return_value = _mock_response({
            "models": [
                {"name": "glm-5.2:cloud"},
                {"name": "llava:13b"},
                {"name": "qwen2.5:14b"},
            ]
        })
        # /api/show probe for llava:13b (vision); the earlier
        # glm-5.2:cloud is text-only, so the function skips it.
        def _post_side_effect(url, json=None):
            payload = json.get("name") if json else ""
            if payload == "llava:13b":
                return _mock_response({
                    "model": "llava:13b",
                    "capabilities": ["vision"],
                    "details": {"family": "llama"},
                })
            return _mock_response({
                "model": payload,
                "capabilities": ["completion"],
                "details": {"family": "other"},
            })
        ctx.__enter__.return_value.post.side_effect = _post_side_effect
        mock_client.return_value = ctx

        result = find_available_vision_model()

    assert result == "llava:13b"


def test_find_available_vision_model_returns_none_when_no_vision():
    """Returns None when no vision-capable model is installed."""
    with patch("app.services.model_capabilities.httpx.Client") as mock_client:
        ctx = MagicMock()
        ctx.__enter__.return_value.get.return_value = _mock_response({
            "models": [{"name": "glm-5.2:cloud"}, {"name": "qwen2.5:14b"}]
        })
        ctx.__enter__.return_value.post.return_value = _mock_response({
            "model": "any",
            "capabilities": ["completion"],
            "details": {"family": "qwen2"},
        })
        mock_client.return_value = ctx

        result = find_available_vision_model()

    assert result is None


def test_find_available_vision_model_returns_none_when_ollama_down():
    with patch("app.services.model_capabilities.httpx.Client") as mock_client:
        ctx = MagicMock()
        ctx.__enter__.return_value.get.side_effect = ConnectionError("nope")
        mock_client.return_value = ctx

        result = find_available_vision_model()

    assert result is None


def test_capabilities_dataclass_defaults():
    """A bare ModelCapabilities has safe defaults."""
    caps = ModelCapabilities(name="x")
    assert caps.is_vision is False
    assert caps.family == ""
    assert caps.context_length == 0
    assert caps.error is None
    assert caps.detected_at == 0.0
    assert caps.is_fresh is False