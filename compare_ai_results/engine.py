"""engine.py — orchestrates: validate → probe → run → score.

Flow per mvp1-spec §2. Sequential (MVP1 — Ollama queueing would
distort latency readings). Caching: per (video, model) raw response —
re-runs after a crash are free (matches the old A/B script's
idempotent behavior, which made the 23-video run recoverable).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from compare_ai_results import dimensions
from compare_ai_results import ollama_client
from compare_ai_results.extract import extract_json
from compare_ai_results.input_schema import load_input
from compare_ai_results.prompt import BUNDLED_SYSTEM_PROMPT, prompt_sha256


def transcript_to_text(segments: list[dict]) -> str:
    """Replicate production's transcript formatting exactly
    (app/services/llm.py's json_to_transcript + join)."""
    lines = []
    for seg in segments:
        lines.append(
            f"[{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text']}"
        )
    return "\n".join(lines)


def run(input_path: str | Path, out_root: str | Path | None = None) -> Path:
    """Run the comparison. Returns the run directory."""
    raw, errors = load_input(input_path)
    if errors:
        raise SystemExit("INPUT INVALID:\n  - " + "\n  - ".join(errors))

    models: list[str] = raw["models"]
    transcripts: list[dict] = raw["transcripts"]

    # Fail-fast: models must be installed BEFORE any LLM spend.
    # Late-bound (module attribute lookup at call time) so tests can
    # patch ollama_client without also patching this module's imports.
    missing = ollama_client.check_models_available(models)
    if missing:
        raise SystemExit(
            f"MODELS NOT INSTALLED: {missing} — run `ollama pull <name>` "
            f"for each, then retry. No LLM calls were made."
        )

    system_prompt = raw.get("system_prompt") or BUNDLED_SYSTEM_PROMPT
    prompt_id = prompt_sha256(system_prompt)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    run_dir = Path(out_root or f"comparison-runs") / ts
    run_dir.mkdir(parents=True, exist_ok=True)

    # inputs.json — the exact input that ran (reproducibility)
    (run_dir / "inputs.json").write_text(json.dumps(raw, indent=2))

    print(f"[run] {len(transcripts)} video(s) × {len(models)} model(s)")
    print(f"[run] prompt: {'bundled ' + prompt_id if not raw.get('system_prompt') else 'override ' + prompt_id}")

    all_metrics: dict[str, list[dict]] = {m: [] for m in models}
    per_video_dir = run_dir / "per-video"
    per_video_dir.mkdir(exist_ok=True)

    for t in transcripts:
        vid = t["video_id"]
        title = t.get("title", vid)
        ttext = transcript_to_text(t["segments"])
        if not ttext.strip():
            print(f"  SKIP {title[:40]!r} (empty transcript)")
            continue
        vdir = per_video_dir / vid
        vdir.mkdir(exist_ok=True)

        for model in models:
            raw_path = vdir / f"{model.replace(':', '_')}.raw.txt"
            # Cache: idempotent re-runs
            if raw_path.exists():
                content = raw_path.read_text()
                latency_s = None
                thinking = ""
                try:
                    meta = json.loads((vdir / f"{model.replace(':', '_')}.meta.json").read_text())
                    latency_s = meta.get("latency_s")
                    thinking = meta.get("thinking", "")
                except Exception:
                    pass
                print(f"  CACHED  {model:18s} {title[:40]!r}")
            else:
                print(f"  RUN     {model:18s} {title[:40]!r} ({len(ttext)} chars)")
                user_content = f"Transcript:\n\n{ttext}"
                content, latency_s, thinking = ollama_client.chat(
                    model, system_prompt, user_content
                )
                raw_path.write_text(content)
                (vdir / f"{model.replace(':', '_')}.meta.json").write_text(
                    json.dumps({"latency_s": latency_s, "thinking": thinking})
                )

            # Score
            m: dict[str, Any] = {
                "video_id": vid, "title": title, "model": model,
                "language": t.get("language"),
            }
            m["parse_success"] = dimensions.parse_success(content)
            m["thinking_contamination"] = dimensions.thinking_contamination(content)
            if m["parse_success"]["ok"]:
                try:
                    parsed = extract_json(content)
                    m["structure"] = dimensions.structure(parsed)
                    m["topic_match"] = dimensions.topic_match(parsed)
                    m["grounding"] = dimensions.grounding(parsed, t["segments"])
                    m["content_metrics"] = dimensions.content_metrics(parsed)
                except Exception as e:  # scoring bug must not kill the run
                    m["structure"] = {"ok": False,
                                      "problems": [f"scorer error: {e!r}"]}
            if latency_s is not None:
                m["latency"] = dimensions.latency(latency_s)
            all_metrics[model].append(m)

            (vdir / f"{model.replace(':', '_')}.metrics.json").write_text(
                json.dumps(m, indent=2)
            )

    # run_index.json — the run's fingerprint
    index = {
        "schema": raw["schema"],
        "models": models,
        "prompt_id": prompt_id,
        "prompt_is_override": bool(raw.get("system_prompt")),
        "videos": len([t for t in transcripts if t["segments"]]),
        "ts_utc": ts,
        "tool_version": "mvp1-lite",
        "labels": raw.get("labels", {}),
    }
    (run_dir / "run_index.json").write_text(json.dumps(index, indent=2))

    # Full metrics dump for the report step
    (run_dir / "metrics.json").write_text(json.dumps(all_metrics, indent=2))

    print(f"[run] done → {run_dir}")
    return run_dir