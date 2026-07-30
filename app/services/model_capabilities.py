"""MVP0.2 followup: probe Ollama model capabilities.

We need to know whether a given Ollama model can accept image inputs
(vision) so the tutor prompt can either:
  - inject extracted text directly (text-only tutor)
  - inject rendered PDF page images (vision-capable tutor)
  - skip with a yellow "this material needs a vision tutor" warning

Ollama exposes this via `POST /api/show` which returns a JSON
document with a `capabilities` list. We cache the result for 5
minutes per model (capabilities don't change during a server run).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_CAPABILITY_CACHE_TTL_SECONDS = 300  # 5 min


@dataclass
class ModelCapabilities:
    """What we know about one Ollama model."""
    name: str
    is_vision: bool = False         # accepts image inputs
    family: str = ""
    context_length: int = 0
    detected_at: float = 0.0        # time.monotonic() at fetch
    error: str | None = None        # probe failure reason

    @property
    def is_fresh(self) -> bool:
        return (time.monotonic() - self.detected_at) < _CAPABILITY_CACHE_TTL_SECONDS


_cache: dict[str, ModelCapabilities] = {}


def probe_model_capabilities(model_name: str, *, force_refresh: bool = False) -> ModelCapabilities:
    """Ask Ollama what the model can do. Cached per model name.

    Args:
        model_name: e.g. "glm-5.2:cloud", "llava:13b"
        force_refresh: bypass cache (used after user installs a new model)

    Returns:
        ModelCapabilities with `is_vision=True` for llava, qwen2-vl,
        etc. Defaults to is_vision=False on any probe failure (safe
        fallback — we treat unknown as text-only).
    """
    cached = _cache.get(model_name)
    if cached is not None and not force_refresh and cached.is_fresh and cached.error is None:
        return cached

    base = settings.ollama_base_url.rstrip("/")
    url = f"{base}/api/show"
    caps = ModelCapabilities(name=model_name, detected_at=time.monotonic())

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(url, json={"name": model_name})
        if resp.status_code != 200:
            caps.error = f"Ollama /api/show returned {resp.status_code}: {resp.text[:100]}"
            _cache[model_name] = caps
            return caps

        data = resp.json()
        caps.is_vision = "vision" in (data.get("capabilities") or [])
        details = data.get("details") or {}
        caps.family = details.get("family", "") or ""
        # context_length may be missing for cloud models
        caps.context_length = int(data.get("context_length") or 0)
    except Exception as exc:
        caps.error = f"Ollama /api/show failed: {type(exc).__name__}: {exc}"

    _cache[model_name] = caps
    log.info(
        "Probed Ollama model %s: is_vision=%s family=%s error=%s",
        model_name, caps.is_vision, caps.family or "(unknown)", caps.error,
    )
    return caps


def find_available_vision_model() -> str | None:
    """Return the name of a vision-capable model on the Ollama server, if any.

    Iterates the user's pulled models (cheap, ~5ms) and returns the first
    one with "vision" capability. Returns None if nothing matches.

    Used by the OCR chain (pdf_ocr.py) as a final fallback when the
    primary tutor LLM is text-only and the user uploaded an image-only
    PDF. In that case we use the vision model JUST to extract text from
    the PDF pages, then feed the extracted text back to the primary tutor.
    """
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
        if resp.status_code != 200:
            return None
        for entry in resp.json().get("models", []):
            name = entry.get("name") or ""
            if not name:
                continue
            caps = probe_model_capabilities(name)
            if caps.is_vision:
                return name
    except Exception as exc:
        log.warning("find_available_vision_model failed: %s", exc)
    return None


def clear_capability_cache() -> None:
    """Clear the cache. Used in tests."""
    _cache.clear()