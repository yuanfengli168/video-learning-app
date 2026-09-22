"""ollama_client.py — thin HTTP wrapper (no branch deps).

Fail-fast model availability check per input-contract-v1 §2 rule 2:
checked against /api/tags BEFORE any transcript is sent.
"""

from __future__ import annotations

import httpx

DEFAULT_BASE_URL = "http://localhost:11434"


def list_models(base_url: str = DEFAULT_BASE_URL) -> list[str]:
    r = httpx.get(f"{base_url}/api/tags", timeout=10)
    r.raise_for_status()
    return [m["name"] for m in r.json().get("models", [])]


def check_models_available(models: list[str],
                           base_url: str = DEFAULT_BASE_URL) -> list[str]:
    """Return the list of MISSING model names (empty = all available)."""
    installed = list_models(base_url)
    # Ollama tags are often 'name:tag' forms; accept prefix matches
    # for ':cloud' suffixed entries (e.g. installed 'minimax-m3:cloud'
    # vs requested 'minimax-m3:cloud' — exact — or 'glm-5.2:cloud')."""
    missing = []
    for m in models:
        if m in installed:
            continue
        if any(inst == m or inst.split(":")[0] == m for inst in installed):
            continue
        missing.append(m)
    return missing


def chat(model: str, system_prompt: str, user_content: str,
         base_url: str = DEFAULT_BASE_URL,
         timeout: float = 300.0) -> tuple[str, float, str]:
    """Call Ollama chat. Returns (content, latency_s, thinking_field).

    thinking_field: the message's 'thinking' content if the model
    emitted one (separate field for thinking models — recorded for
    the report; must never appear inside content for our parser)."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
    }
    import time
    t0 = time.time()
    r = httpx.post(f"{base_url}/api/chat", json=body, timeout=timeout)
    dt = time.time() - t0
    r.raise_for_status()
    data = r.json()
    msg = data.get("message", {})
    return msg.get("content", ""), dt, msg.get("thinking", "") or ""