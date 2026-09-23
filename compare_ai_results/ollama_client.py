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
         timeout: float = 600.0,
         retries: int = 2) -> tuple[str, float, str]:
    """Call Ollama chat. Returns (content, latency_s, thinking_field).

    thinking_field: the message's 'thinking' content if the model
    emitted one (separate field for thinking models — recorded for
    the report; must never appear inside content for our parser).

    2026-09-22 (round-2 lessons): cloud models throw transient 500s
    (~1 in 170 generations) AND read-timeouts on the largest
    transcripts (the 106k-char class — thinking models can exceed
    a 300s cap mid-generation). Retry up to `retries` extra times
    with a backoff on 5xx + timeout exceptions; timeout raised to
    600s for the tail videos. The raw cache means even a hard
    failure never loses completed work."""
    import time

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
    }
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            r = httpx.post(f"{base_url}/api/chat", json=body, timeout=timeout)
        except httpx.TimeoutException as e:
            if attempt < retries:
                last_exc = e
                time.sleep(3 * (attempt + 1))
                continue
            raise
        dt = time.time() - t0
        if r.status_code >= 500 and attempt < retries:
            last_exc = httpx.HTTPStatusError(
                f"Server error '{r.status_code}' (attempt {attempt + 1})",
                request=r.request, response=r,
            )
            time.sleep(3 * (attempt + 1))  # backoff: 3s, 6s
            continue
        r.raise_for_status()
        data = r.json()
        msg = data.get("message", {})
        return msg.get("content", ""), dt, msg.get("thinking", "") or ""
    raise last_exc  # type: ignore[misc]