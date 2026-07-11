"""Whisper transcription service.

MVP3.0 #2: now supports two backends (faster-whisper and mlx-whisper)
plus two "smart pick" aliases that auto-pick the best (backend, model)
pair for the current Mac. The smart picks live in MODEL_REGISTRY
below and the resolve_model_choice() function does the mapping +
MLX-fallback logic.

Backend notes:
  - faster-whisper (CTranslate2): works on any Mac (Intel + Apple Silicon).
    CPU-only, ~5x realtime with `base` on M-series.
  - mlx-whisper (Apple MLX): Apple Silicon only. Uses ANE + Metal GPU.
    ~7-30x realtime with distil-large-v3 on M-series (see
    doc/BlockersOrChallengers.md §1 for speed math).
  - Both backends are auto-installed. mlx-whisper is gated by a
    platform check (is_mlx_available) so Intel Macs never see the
    MLX option in the dropdown.
"""

import json
import platform
import sys
from pathlib import Path
from typing import Any

# Model cache to avoid reloading (faster-whisper side; mlx-whisper
# manages its own cache internally)
_model_cache: dict[str, Any] = {}


# ─────────────────────────────────────────────────────────────────────────────
# MODEL REGISTRY (MVP3.0 #2)
# ─────────────────────────────────────────────────────────────────────────────
# Each entry is a (label, model_id, backend, requires_mlx, group) tuple
# where:
#   - label: what the user sees in the dropdown
#   - model_id: the model name passed to the backend (faster-whisper
#     or mlx-whisper)
#   - backend: "faster-whisper" | "mlx-whisper"
#   - requires_mlx: True if the entry needs Apple Silicon
#   - group: "manual" (the 4 original tiny/base/small/medium) or
#     "smart" (the 2 new recommended picks)
#
# Adding a new model = adding one row here. The dropdown, the
# endpoint validator, and the worker all read from this single
# source of truth.
MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    # ── Manual picks (group="manual") — the 4 originals ──
    "tiny": {
        "label": "tiny (fastest)",
        "model_id": "tiny",
        "backend": "faster-whisper",
        "requires_mlx": False,
        "group": "manual",
    },
    "base": {
        "label": "base (default)",
        "model_id": "base",
        "backend": "faster-whisper",
        "requires_mlx": False,
        "group": "manual",
    },
    "small": {
        "label": "small (better)",
        "model_id": "small",
        "backend": "faster-whisper",
        "requires_mlx": False,
        "group": "manual",
    },
    "medium": {
        "label": "medium (best small)",
        "model_id": "medium",
        "backend": "faster-whisper",
        "requires_mlx": False,
        "group": "manual",
    },
    # ── Smart picks (group="smart") — recommended defaults ──
    # Note: distil-large-v3 is a HuggingFace model name. The faster-whisper
    # backend accepts HF model IDs, so it will auto-download from
    # https://huggingface.co/distil-whisper/distil-large-v3 on first use.
    "local-best-and-fast": {
        "label": "✨ Local best and fast (Distil-large-v3)",
        "model_id": "distil-large-v3",
        "backend": "faster-whisper",
        "requires_mlx": False,
        "group": "smart",
    },
    "local-best-and-extremely-fast": {
        "label": "⚡ Local best and extremely fast (MLX, M-series only)",
        "model_id": "distil-large-v3",
        "backend": "mlx-whisper",
        "requires_mlx": True,
        "group": "smart",
    },
}

# Backwards-compat: the old AVAILABLE_MODELS list (5 strings). Many
# existing tests and the /api/videos/models endpoint import this name.
# We keep it as the manual-only list so existing UI/tests that show
# just the 4-5 originals continue to work. The smart picks are
# exposed separately via SMART_PICKS so the new optgroup UI can
# combine them.
AVAILABLE_MODELS: list[str] = [
    entry["model_id"] for entry in MODEL_REGISTRY.values()
    if entry["group"] == "manual"
]
SMART_PICKS: list[str] = [
    key for key, entry in MODEL_REGISTRY.items()
    if entry["group"] == "smart"
]
ALL_MODEL_CHOICES: list[str] = list(MODEL_REGISTRY.keys())

# Default model recommendation. The actual value is computed at call
# time via get_default_model_choice() so the answer adapts if the
# user `pip install mlx-whisper` later. We don't compute a module-
# level constant here because is_mlx_available() is defined below
# and module-level evaluation would NameError.


def is_mlx_available() -> bool:
    """True if the current platform can run mlx-whisper.

    mlx-whisper requires Apple Silicon (M1 or newer). On Intel Macs
    or Linux/Windows servers the import will fail. We use a soft
    check (try to import mlx.core) so the import is optional at
    startup — only required when the user actually picks an MLX
    option.
    """
    # Fast path: Apple Silicon check via the arch. This avoids
    # trying to import mlx on Intel Macs (which would raise an
    # unhelpful error).
    if platform.machine() != "arm64":
        return False
    # Second check: mlx-whisper package installed?
    try:
        import mlx_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def get_model_entry(choice: str) -> dict[str, Any]:
    """Return the MODEL_REGISTRY entry for a user-facing choice key.

    Raises ValueError if the choice is not in the registry. Callers
    should catch this and turn it into a 400 response.
    """
    if choice not in MODEL_REGISTRY:
        raise ValueError(
            f"Unknown model choice '{choice}'. "
            f"Available: {sorted(MODEL_REGISTRY.keys())}"
        )
    return MODEL_REGISTRY[choice]


def resolve_model_choice(
    choice: str,
    *,
    prefer_mlx: bool = True,
) -> dict[str, Any]:
    """Map a user-facing choice to a concrete (backend, model_id) pair.

    Applies the MVP3.0 #2 auto-fallback rule:
      1. If `choice` is unknown → raise ValueError.
      2. If the choice requires MLX but MLX isn't available on this
         Mac (Intel, or Apple Silicon without mlx-whisper installed),
         fall back to the "local-best-and-fast" entry and return
         `fallback_occurred=True` so the caller can show a warning.
      3. Otherwise return the choice's entry unchanged.

    Args:
        choice: A key from MODEL_REGISTRY (e.g. "base", "local-best-and-fast").
        prefer_mlx: When True (the default), the "local-best-and-extremely-fast"
            choice is preferred when both are available. When False,
            always use faster-whisper. Currently unused but reserved
            for a future "force CPU" toggle.

    Returns:
        A dict with keys: label, model_id, backend, requires_mlx,
        group, fallback_occurred, fallback_reason.
    """
    entry = get_model_entry(choice)
    result = dict(entry)
    result["fallback_occurred"] = False
    result["fallback_reason"] = None

    if entry["requires_mlx"] and not is_mlx_available():
        # MLX requested but not usable here. Fall back to the
        # faster-whisper smart pick (best non-MLX option).
        fallback = MODEL_REGISTRY["local-best-and-fast"]
        result = dict(fallback)
        result["fallback_occurred"] = True
        result["fallback_reason"] = (
            f"'{choice}' needs Apple Silicon and the mlx-whisper "
            f"package, which is not available on this Mac. "
            f"Falling back to '{fallback['label']}'."
        )
    return result


def get_default_model_choice() -> str:
    """Return the recommended default choice for this Mac.

    "local-best-and-extremely-fast" if MLX is available, else
    "local-best-and-fast", else the legacy "base".

    Computed at call time (not import time) so that the answer
    adapts if the user `pip install mlx-whisper` later.
    """
    if is_mlx_available():
        return "local-best-and-extremely-fast"
    # Even if MLX isn't installed, the "fast" smart pick should
    # work on any Mac (it uses faster-whisper + distil-large-v3).
    # Check that the model can actually be resolved; if not, fall
    # through to base.
    try:
        return "local-best-and-fast"
    except (KeyError, ValueError):
        return "base"



def get_model(model_name: str = "base"):
    """Load a Faster-Whisper model (cached).

    Args:
        model_name: A model_id from MODEL_REGISTRY (e.g. "base",
            "distil-large-v3") or a bare HuggingFace model name.

    Returns:
        A faster_whisper.WhisperModel instance.

    Raises:
        ValueError: if model_name is not a known model_id AND not a
            known HuggingFace whisper model. We validate against a
            small allowlist here so we get a clean error before
            faster-whisper downloads a 750 MB model file. The
            allowlist is intentionally broader than AVAILABLE_MODELS
            (which is just the manual UI list) so the smart picks
            like "distil-large-v3" are accepted.
    """
    # Allowlist of HF model_ids that we explicitly support. New
    # models go here when added to MODEL_REGISTRY.
    _SUPPORTED_FASTER_WHISPER_MODELS: set[str] = set(AVAILABLE_MODELS) | {
        # Smart-pick model_id (not in AVAILABLE_MODELS since that's
        # only the manual UI list, but faster-whisper accepts it)
        "distil-large-v3",
        # The big Whisper model — accepted for completeness even
        # though it's not in the UI (would be too slow for most users)
        "large-v3",
    }
    if model_name not in _SUPPORTED_FASTER_WHISPER_MODELS:
        raise ValueError(
            f"Unknown model '{model_name}'. "
            f"Available faster-whisper models: "
            f"{sorted(_SUPPORTED_FASTER_WHISPER_MODELS)}"
        )

    if model_name not in _model_cache:
        from faster_whisper import WhisperModel

        # device="cpu" for broad compatibility; compute_type="int8" for speed
        _model_cache[model_name] = WhisperModel(model_name, device="cpu", compute_type="int8")

    return _model_cache[model_name]


def transcribe_video(
    video_path: str | Path,
    model_name: str = "base",
) -> dict[str, Any]:
    """Transcribe a video file and return timestamped segments.

    Args:
        video_path: Path to the video file.
        model_name: Whisper model to use (base, small, medium, etc.).

    Returns:
        Dict with:
            - segments: list of {start, end, text}
            - language: detected language
            - duration: total duration in seconds
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    model = get_model(model_name)

    segments_iter, info = model.transcribe(str(video_path), beam_size=5)

    segments = []
    for seg in segments_iter:
        segments.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
        })

    return {
        "segments": segments,
        "language": info.language,
        "duration": round(info.duration, 2),
    }


def transcribe_with_backend(
    video_path: str | Path,
    choice: str,
    *,
    on_progress: Any = None,
) -> dict[str, Any]:
    """Transcribe a video using the user-facing model choice.

    MVP3.0 #2: this is the new entry point that the upload +
    transcribe endpoints should use. It:
      1. Resolves `choice` to a (backend, model_id) pair via
         resolve_model_choice() (with MLX auto-fallback).
      2. Dispatches to the right backend (faster-whisper today,
         mlx-whisper in a follow-up commit).
      3. Returns the same shape as transcribe_video(), so the
         existing worker code (which reads .segments / .language /
         .duration) doesn't have to change.

    The returned dict has a top-level `_meta` key with the resolved
    backend, model_id, and any fallback info, so the worker can
    persist it to the video row for display.

    Args:
        video_path: Path to the video file.
        choice: One of MODEL_REGISTRY keys (e.g. "base",
            "local-best-and-fast").
        on_progress: Optional callable(done, total, message) for
            progress reporting. Currently only used by mlx-whisper
            (faster-whisper streams segments, not progress %).

    Returns:
        Dict with `segments`, `language`, `duration`, plus
        `_meta: {backend, model_id, choice, fallback_occurred,
        fallback_reason}`.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    resolved = resolve_model_choice(choice)
    backend = resolved["backend"]
    model_id = resolved["model_id"]

    if backend == "faster-whisper":
        # Reuse the existing transcribe_video() — it already
        # handles the faster-whisper model loading, caching, and
        # segment streaming. We just call it with model_id.
        result = transcribe_video(video_path, model_id)
    elif backend == "mlx-whisper":
        # MLX Whisper is a follow-up commit (Part B). For now,
        # raise a clear error so a user who picks this on a Mac
        # without mlx-whisper installed gets a useful message
        # instead of a generic 500. The resolve_model_choice()
        # fallback above should usually prevent reaching this
        # line, but it's a defense-in-depth check.
        raise NotImplementedError(
            "mlx-whisper backend is not yet wired up. This is a "
            "follow-up to the MVP3.0 #2 implementation. To enable "
            "it now, run: pip install mlx-whisper  (Apple Silicon "
            "only)."
        )
    else:
        raise ValueError(f"Unknown backend: {backend!r}")

    # Attach metadata so the worker can persist the resolved
    # (backend, model_id) for later display. The `_` prefix
    # signals "not part of the transcript itself".
    result = dict(result)
    result["_meta"] = {
        "choice": choice,
        "backend": backend,
        "model_id": model_id,
        "fallback_occurred": resolved["fallback_occurred"],
        "fallback_reason": resolved["fallback_reason"],
    }
    return result


def transcript_to_json(transcript: dict[str, Any]) -> str:
    """Serialize a transcript dict to JSON string for DB storage."""
    return json.dumps(transcript, ensure_ascii=False)


def json_to_transcript(json_str: str) -> dict[str, Any]:
    """Deserialize a JSON transcript string back to dict."""
    return json.loads(json_str)


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS for display."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"