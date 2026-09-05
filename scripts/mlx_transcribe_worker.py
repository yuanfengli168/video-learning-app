#!/usr/bin/env python3
"""Standalone MLX Whisper transcriber — subprocess worker.

Why this exists (2026-09-05):
  Running `mlx_whisper.transcribe()` inside a gunicorn worker crashes
  with SIGABRT ("crashed on child side of fork pre-exec"). The worker
  is itself a forked child (gunicorn preload_app=True), and when the
  BackgroundTask thread forks ffmpeg (language detection) while Metal
  / MLX thread-pool state is live, macOS's fork-safety aborts the
  process. Verified 2026-09-05: identical transcription in a FRESH
  process works flawlessly on Python 3.14 + mlx 0.32.2.

  The fix: never let MLX run inside the gunicorn worker. The app's
  transcribe_with_backend() spawns THIS script as a subprocess, the
  Metal/GPU state lives and dies entirely inside this fresh process,
  and the worker parses the JSON result from stdout.

Usage (by the app; also runnable by hand for debugging):
  python scripts/mlx_transcribe_worker.py <audio_or_video_path> \
      [--model mlx-community/whisper-large-v3-turbo] \
      [--language zh]            # omit for per-window auto-detect
      [--initial-prompt "..."]   # anti-drift decoder bias

Output: a single JSON object on stdout with the SAME shape the app
expects from the in-process call:
  {"segments": [{"start": 0.0, "end": 2.1, "text": "..."}, ...],
   "language": "zh",
   "duration": 123.45}

Exit codes: 0 = success, 1 = transcription failure (message on
stderr, no stdout JSON), 2 = bad usage.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="MLX Whisper subprocess worker")
    parser.add_argument("path", help="Path to the audio/video file to transcribe")
    parser.add_argument(
        "--model",
        default="mlx-community/whisper-large-v3-turbo",
        help="MLX model id (HF repo) to use",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="ISO 639-1 language lock (omit for auto-detect)",
    )
    parser.add_argument(
        "--initial-prompt",
        default=None,
        help="Initial prompt to bias the decoder toward the language",
    )
    args = parser.parse_args()

    src = Path(args.path)
    if not src.exists():
        print(f"ERROR: file not found: {src}", file=sys.stderr)
        return 2

    # Import here (not at module top) so --help / arg errors never
    # pay the MLX import + Metal init cost.
    import mlx_whisper

    kwargs = {
        "path_or_hf_repo": args.model,
        # Anti-drift params — mirror the in-process call exactly so
        # transcription behaviour is unchanged from the original path.
        "condition_on_previous_text": False,
        "compression_ratio_threshold": 1.8,
    }
    if args.language:
        kwargs["language"] = args.language
    if args.initial_prompt:
        kwargs["initial_prompt"] = args.initial_prompt

    result_dict = mlx_whisper.transcribe(str(src), **kwargs)

    segments = []
    for seg in result_dict.get("segments", []):
        segments.append(
            {
                "start": round(float(seg.get("start", 0.0)), 2),
                "end": round(float(seg.get("end", 0.0)), 2),
                "text": str(seg.get("text", "")).strip(),
            }
        )
    # mlx-whisper's dict has no duration (unlike faster-whisper) —
    # compute from the last segment end, 0 if empty. Mirrors the
    # in-process code so the result shape is identical.
    duration = round(float(segments[-1]["end"]) if segments else 0.0, 2)

    payload = {
        "segments": segments,
        "language": result_dict.get("language", "unknown"),
        "duration": duration,
    }
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())