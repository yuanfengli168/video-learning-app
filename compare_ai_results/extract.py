"""extract.py — the 4-strategy JSON extraction, ported from production.

Pinned port of app/services/llm.py::_extract_json
(mvp2-production-patches @ bf97c71). Zero app/ imports — this is a
verbatim copy of the proven logic so the tool's parse_success
dimension IS the production parser, not an approximation.
"""

from __future__ import annotations

import json
import re


def extract_json(text: str) -> dict:
    """Extract JSON from a text response, handling markdown code fences
    and the LLM's habit of adding prose around the JSON object.

    Four strategies in order:
      1. Direct parse
      2. Code fence (```json ... ```)
      3. Strip a leading prose preamble ("Sure! Here is the JSON:")
      4. Brace match — outermost { ... }

    Raises ValueError with the raw response included when all four
    fail (debuggable from the log alone, no re-run needed).
    """
    # Strategy 1: direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Strategy 2: code fence
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        try:
            parsed = json.loads(fence_match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Strategy 3: strip a leading prose preamble
    stripped = re.sub(
        r"^\s*(sure|here is|here's|certainly|of course)[^\n]*\n+",
        "", text, flags=re.IGNORECASE,
    )
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Strategy 4: brace match — outermost { ... }
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last > first:
        try:
            parsed = json.loads(stripped[first : last + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    raise ValueError(f"no JSON found in response: {text[:500]!r}")