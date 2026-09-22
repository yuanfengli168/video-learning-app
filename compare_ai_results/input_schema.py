"""input_schema.py — validate `compare-ai-results/1`, fail fast.

Every rule from doc/compare-ai-results-tool/input-contract-v1.md §2
is enforced here BEFORE any LLM spend. Errors are collected and
reported together (one pass, not death-by-a-thousand-retries).
"""

from __future__ import annotations

import json
from pathlib import Path

SUPPORTED_SCHEMAS = {"compare-ai-results/1"}
SUPPORTED_TASKS = {"materials"}
SUPPORTED_DIMENSIONS = {
    "parse_success", "structure", "topic_match", "grounding",
    "latency", "content_metrics", "thinking_contamination",
}


def validate_input(raw: dict) -> list[str]:
    """Return a list of contract violations (empty = valid)."""
    errors: list[str] = []

    schema = raw.get("schema")
    if schema not in SUPPORTED_SCHEMAS:
        errors.append(
            f"unknown schema {schema!r} — supported: {sorted(SUPPORTED_SCHEMAS)}"
        )
        return errors  # schema mismatch: nothing else is trustworthy

    transcripts = raw.get("transcripts")
    if not isinstance(transcripts, list) or len(transcripts) < 1:
        errors.append("'transcripts' must be a non-empty list")
        transcripts = transcripts if isinstance(transcripts, list) else []

    seen_ids: set[str] = set()
    for i, t in enumerate(transcripts):
        vid = t.get("video_id")
        if not vid or not isinstance(vid, str):
            errors.append(f"transcripts[{i}]: missing/empty video_id")
        elif vid in seen_ids:
            errors.append(f"transcripts[{i}]: duplicate video_id {vid!r}")
        else:
            seen_ids.add(vid)
        segs = t.get("segments")
        if not isinstance(segs, list) or len(segs) == 0:
            errors.append(f"transcripts[{i}] ({vid}): empty segments")
        else:
            for j, s in enumerate(segs):
                if not isinstance(s.get("text"), str) or not s["text"].strip():
                    errors.append(
                        f"transcripts[{i}] ({vid}): segments[{j}] empty text"
                    )
                    break  # one report per video is enough

    models = raw.get("models")
    if not isinstance(models, list) or len(models) < 2:
        errors.append("'models' must list >= 2 model names (MVP1)")
    elif any(not isinstance(m, str) or not m.strip() for m in models):
        errors.append("'models' entries must be non-empty strings")

    for key, valid in (("tasks", SUPPORTED_TASKS),
                       ("dimensions", SUPPORTED_DIMENSIONS | {"all"})):
        vals = raw.get(key)
        if vals is None:
            continue  # optional — engine defaults apply
        if not isinstance(vals, list) or any(v not in valid for v in vals):
            bad = [v for v in vals if v not in valid] if isinstance(vals, list) else vals
            errors.append(f"{key}: unknown entries {bad!r} — valid: {sorted(valid)}")

    return errors


def load_input(path: str | Path) -> tuple[dict, list[str]]:
    """Read + parse the input file. Returns (raw, errors)."""
    p = Path(path)
    if not p.exists():
        return {}, [f"input file not found: {p}"]
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        return {}, [f"input is not valid JSON: {e}"]
    return raw, validate_input(raw)