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
#     "smart" (the recommended pick — currently just
#     `local-large-turbo`; the 2 distil-large-v3 smart picks
#     are commented out below per manualTodo 2.2 / MVP2.0.6)
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
    # MVP2.0.6 (2026-07-15, manualTodo 2.2): the two distil-large-v3
    # smart picks are commented out because distil-large-v3 is
    # English-biased and ignores the `language="zh"` lock. The
    # only smart pick that survives is `local-large-turbo`
    # (mlx-community/whisper-large-v3-turbo via mlx-whisper),
    # which is multilingual and is now both the default and
    # the only smart pick. Kept here as commented-out code so
    # they can be restored if needed (e.g. for an English-only
    # workload where distil-large-v3 is genuinely faster).
    #
    # "local-best-and-fast": {
    #     "label": "✨ Local best and fast (Distil-large-v3)",
    #     "model_id": "distil-large-v3",
    #     "backend": "faster-whisper",
    #     "requires_mlx": False,
    #     "group": "smart",
    # },
    # "local-best-and-extremely-fast": {
    #     "label": "⚡ Local best and extremely fast (MLX, M-series only)",
    #     "model_id": "distil-large-v3",
    #     "backend": "mlx-whisper",
    #     "requires_mlx": True,
    #     "group": "smart",
    # },
    # The default since 2026-07-14, and now the ONLY smart pick.
    # Replaces the distil-large-v3 entries as the recommended
    # pick because distil-large-v3 is English-biased and ignores
    # the `language="zh"` lock — it was producing all-English
    # hallucination loops on Chinese videos even with the
    # anti-drift kwargs. mlx-community/whisper-large-v3-turbo is
    # a strict superset for Chinese / multilingual and only
    # ~1.5-2x slower than distil-large-v3 on M-series. Apple
    # Silicon only.
    "local-large-turbo": {
        # MVP2.0.6: the user-facing label was given a proper
        # name (vs the previous "Local Large-v3 Turbo (MLX,
        # M-series, multilingual)" which was descriptive but
        # didn't name the model or signal "recommended"). The
        # new label is shorter and more useful: identifies the
        # engine (MLX), the model (Whisper Large V3 Turbo), and
        # marks it as recommended.
        "label": "🚀 MLX Whisper Large V3 Turbo (recommended)",
        "model_id": "mlx-community/whisper-large-v3-turbo",
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

# ── Language policy (MVP3.0 #2b, anti-drift) ───────────────────────────────
# On 2026-07-13 the user uploaded a 1.7 GB / 2.5h Mandarin lecture and
# the MLX whisper-large-v3-turbo produced 296 identical "Thank you."
# segments. Root cause: whisper's per-window language auto-detection
# drifted to English (the first 30s contained the title/intro), and
# `condition_on_previous_text=True` (the default) chained the drift
# across all subsequent windows. Two fixes:
#   1. LOCK the language for the whole file via `language=`. Detect
#      ONCE on the first 10 min of audio, then pass the result to
#      whisper. Allows the user to override via the language dropdown.
#   2. Set `condition_on_previous_text=False` + a lower
#      `compression_ratio_threshold=1.8` (from default 2.4) to catch
#      repetitive-text hallucination early. These two apply to every
#      MLX transcribe regardless of language.
#
# INITIAL_PROMPTS gives whisper a short example sentence in the
# target language so the decoder doesn't drift back to English when
# the audio is mostly music/silence. Only used for MLX (faster-
# whisper keeps the original behaviour for now).
INITIAL_PROMPTS: dict[str, str] = {
    "en": "The following is a conversation in English.",
    "zh": "以下是普通话的对话。",
    "ja": "以下は日本語の会話です。",
    "ko": "다음은 한국어 대화입니다.",
    "fr": "La conversation suivante est en français.",
    "de": "Das folgende Gespräch ist auf Deutsch.",
    "es": "La siguiente conversación es en español.",
    # Fallback when language is unknown — bias toward English since
    # that's the most common target. Whisper's own language detection
    # will still produce the right script for the actual audio.
    "unknown": "The following is a conversation.",
}


def get_initial_prompt(language: str | None) -> str | None:
    """Return the initial_prompt to bias the decoder for `language`.

    Returns None if the language is None/empty/unsupported AND we
    have no fallback (which lets whisper use its own default).
    Returns the matched prompt from INITIAL_PROMPTS, or the
    "unknown" fallback if the language isn't in the table.
    """
    if not language:
        return None
    if language in INITIAL_PROMPTS:
        return INITIAL_PROMPTS[language]
    # Unknown language code — use the generic English fallback
    # so the decoder has SOME bias. Better than no prompt at all.
    return INITIAL_PROMPTS["unknown"]


# Languages exposed in the UI dropdown. The first entry is "auto" —
# sentinel that means "let the backend detect from the first 10 min".
# The next 3 are the user's locked choices (Q4 in the design doc).
LANGUAGE_CHOICES: list[dict[str, str]] = [
    {"key": "auto", "label": "Auto-detect (default)"},
    {"key": "en", "label": "English"},
    {"key": "zh", "label": "中文 (简体)"},
]

# Quick lookup. Whitelist of "user-locked" language codes (everything
# except "auto" which is the sentinel for backend auto-detection).
LANGUAGE_LOCKED_CODES: frozenset[str] = frozenset(
    c["key"] for c in LANGUAGE_CHOICES if c["key"] != "auto"
)


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


def _extract_audio_clip(
    video_path: Path,
    start_seconds: float,
    duration_seconds: float,
) -> Path:
    """Extract a short audio clip from a video file to a temp WAV.

    Used by detect_audio_language() to grab the first N 30s windows
    of a video for language detection without paying the cost of
    decoding the whole thing.

    Writes to a NamedTemporaryFile so multiple concurrent calls
    (e.g. parallel uploads) don't collide. The file is closed
    after writing (Windows needs this); the caller is responsible
    for unlinking.

    Args:
        video_path: Source video (or audio) file.
        start_seconds: Where to start in the source.
        duration_seconds: Length to extract.

    Returns:
        Path to the temporary WAV file.
    """
    import subprocess
    import tempfile

    # delete=False so we can close the handle and still have a
    # path to pass to mlx_whisper (which opens by path on some
    # platforms). Caller cleans up.
    fd, name = tempfile.mkstemp(suffix=".wav", prefix="lang-detect-")
    import os as _os
    _os.close(fd)
    tmp = Path(name)
    try:
        # -ss BEFORE -i for fast keyframe seek (no decode of the
        # leading audio). -t limits duration. -ar 16000 + -ac 1
        # matches what mlx-whisper expects internally, so the
        # detection results match what a real transcribe would
        # hear. -loglevel error to keep stdout clean.
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", f"{start_seconds:.2f}",
                "-i", str(video_path),
                "-t", f"{duration_seconds:.2f}",
                "-ar", "16000", "-ac", "1",
                "-f", "wav",
                "-loglevel", "error",
                str(tmp),
            ],
            check=True,
            timeout=30,
        )
    except Exception:
        # If ffmpeg fails (corrupted file, no audio stream, etc.),
        # unlink the empty file and re-raise so the caller can
        # decide what to do (probably: fall back to "no detection").
        if tmp.exists():
            tmp.unlink()
        raise
    return tmp


def detect_audio_language(
    video_path: str | Path,
    *,
    sample_windows: int | None = None,
    speech_threshold: float | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    """Auto-detect the primary language of a video.

    Samples the first N windows of 30s (default 20 = 10 min) and
    picks the language with the highest total probability across
    windows that have actual speech (no_speech_prob < threshold).
    Designed to handle the common case of an English song intro
    followed by Chinese lecture — the song window is filtered as
    "no speech" and the Chinese window wins the tally.

    MVP3.0 #2b (jul 13 2026). Replaces the "first 30s only" detection
    that caused the 2.5h Mandarin file to be wrongly tagged as
    English (the first 30s contained the title/intro, drift
    chain then took over for the remaining 2.5h).

    Args:
        video_path: Path to the source video (or audio) file.
        sample_windows: How many 30s windows to sample (default:
            from `settings.language_detect_sample_windows`).
        speech_threshold: no_speech_prob above this counts as
            "not speech" and is skipped (default: from
            `settings.language_detect_speech_threshold`).
        model_id: Which whisper model to use for detection (default:
            `mlx-community/whisper-large-v3-turbo` if MLX is
            available, else `base` via faster-whisper).

    Returns:
        Dict with:
          - "language": ISO 639-1 code (e.g. "zh", "en", "ja"),
            or "unknown" if no speechy windows were found.
          - "confidence": 0.0-1.0, the total probability sum of
            the winning language / total probability sum of all
            speechy windows. Lower = less certain.
          - "windows_sampled": total windows tried.
          - "windows_speechy": windows that passed the
            no_speech_prob filter.
          - "model_id": which model was actually used.
          - "error": set to a string if detection failed (e.g. ffmpeg
            not available). `language` will be "unknown" in that case.

    Cost: ~6-12s for 20 windows of 30s each on M1 Max mlx-whisper
    (very cheap relative to a full 2.5h transcribe). Failure to
    detect does NOT raise — returns "unknown" so the caller can
    fall back to whisper's own per-window detection (the old
    behaviour, with all its drift risks).
    """
    import os

    # Lazy defaults from config (avoids import cycle)
    from app.config import settings

    if sample_windows is None:
        sample_windows = settings.language_detect_sample_windows
    if speech_threshold is None:
        speech_threshold = settings.language_detect_speech_threshold

    video_path = Path(video_path)
    if not video_path.exists():
        return {
            "language": "unknown",
            "confidence": 0.0,
            "windows_sampled": 0,
            "windows_speechy": 0,
            "model_id": model_id or "(unavailable)",
            "error": f"file not found: {video_path}",
        }

    # Pick the detection model. Use the same one the user would
    # transcribe with, so the detection and the real run are
    # consistent. MLX turbo is the default smart pick.
    if model_id is None:
        if is_mlx_available():
            model_id = "mlx-community/whisper-large-v3-turbo"
            backend = "mlx-whisper"
        else:
            model_id = "base"
            backend = "faster-whisper"
    else:
        backend = "mlx-whisper" if model_id.startswith("mlx-") else "faster-whisper"

    # Tally: language -> total probability
    tallies: dict[str, float] = {}
    windows_sampled = 0
    windows_speechy = 0
    last_error: str | None = None
    tmp_files: list[Path] = []

    try:
        for i in range(sample_windows):
            start = i * 30.0
            try:
                clip = _extract_audio_clip(video_path, start, 30.0)
            except Exception as e:
                # ffmpeg failed (e.g. past the end of the file).
                # Stop trying — the file is shorter than we thought.
                last_error = f"ffmpeg: {e}"
                break
            tmp_files.append(clip)
            windows_sampled += 1

            try:
                if backend == "mlx-whisper":
                    import mlx_whisper
                    r = mlx_whisper.transcribe(
                        str(clip),
                        path_or_hf_repo=model_id,
                        # Use temperature=0 + a single greedy decode so
                        # each window is fast (~50-100ms). We only
                        # care about the language token, not the
                        # transcript.
                        temperature=0.0,
                    )
                    lang = r.get("language", "unknown")
                    # mlx-whisper doesn't return per-segment probs,
                    # so we use 1.0 / total_speechy_windows as the
                    # tally weight (each speechy window votes 1.0
                    # for its detected language). The user can still
                    # override manually if detection is wrong.
                    prob = 1.0
                    no_speech_prob = 0.0  # mlx doesn't return this
                else:
                    m = get_model(model_id)
                    segs_iter, info = m.transcribe(
                        str(clip),
                        beam_size=1,  # fastest path
                        temperature=0.0,
                        # Don't condition on previous text during
                        # detection — each window is independent.
                        condition_on_previous_text=False,
                    )
                    # faster-whisper is a generator — drain it to
                    # trigger the per-segment no_speech_prob decode.
                    segs = list(segs_iter)
                    lang = info.language
                    prob = float(info.language_probability)
                    # Average the per-segment no_speech_prob across
                    # the window. If the window is mostly silence,
                    # the average is high and we skip it.
                    if segs:
                        no_speech_prob = sum(
                            getattr(s, "no_speech_prob", 0.0)
                            for s in segs
                        ) / len(segs)
                    else:
                        no_speech_prob = 1.0
            except Exception as e:
                last_error = f"whisper on window {i}: {e}"
                continue

            if no_speech_prob >= speech_threshold:
                # Mostly silence/music/noise — skip, don't vote.
                continue

            windows_speechy += 1
            tallies[lang] = tallies.get(lang, 0.0) + prob

        if not tallies:
            return {
                "language": "unknown",
                "confidence": 0.0,
                "windows_sampled": windows_sampled,
                "windows_speechy": 0,
                "model_id": model_id,
                "error": last_error or "no speechy windows found",
            }

        winner = max(tallies.items(), key=lambda kv: kv[1])
        total = sum(tallies.values())
        confidence = winner[1] / total if total > 0 else 0.0
        return {
            "language": winner[0],
            "confidence": round(confidence, 3),
            "windows_sampled": windows_sampled,
            "windows_speechy": windows_speechy,
            "model_id": model_id,
        }
    finally:
        # Best-effort cleanup of the temp WAVs. On error we
        # still want to clean up.
        for f in tmp_files:
            try:
                f.unlink()
            except OSError:
                pass


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
         fall back to "base" (the recommended manual pick) and
         return `fallback_occurred=True` so the caller can show a
         warning.
      3. Otherwise return the choice's entry unchanged.

    Args:
        choice: A key from MODEL_REGISTRY (e.g. "base", "local-large-turbo").
        prefer_mlx: When True (the default), the MLX choice is
            preferred when available. When False, always use
            faster-whisper. Currently unused but reserved for a
            future "force CPU" toggle.

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
        # recommended manual pick ("base"). The previous fallback
        # was "local-best-and-fast" (distil-large-v3 via
        # faster-whisper), but that's commented out per
        # manualTodo 2.2 because distil-large-v3 is
        # English-biased. "base" is the new recommended
        # non-MLX default.
        fallback = MODEL_REGISTRY["base"]
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

    As of MVP2.0.6 (2026-07-15), the default is
    "local-large-turbo" (mlx-community/whisper-large-v3-turbo
    via mlx-whisper) on Apple Silicon, and "base" on
    x86 / arm64 without MLX. Before MVP2.0.6, the
    non-MLX fallback was "local-best-and-fast" (distil-
    large-v3 via faster-whisper), but those distil entries
    are now commented out per manualTodo 2.2 because
    distil-large-v3 is English-biased and ignores
    `language="zh"` (it produced all-English hallucination
    loops on Chinese videos even with the Part A
    anti-drift kwargs).

    The fallback chain is:
      1. MLX on Apple Silicon → "local-large-turbo"
      2. Otherwise → "base" (the recommended default
         manual pick — small enough to be fast, large
         enough to be accurate for most content)
      3. Defensive last resort → "base"

    Computed at call time (not import time) so that the answer
    adapts if the user `pip install mlx-whisper` later.

    The registry is module-level and immutable at runtime,
    so the keys are always present (the distil entries are
    commented out, not deleted).
    """
    if is_mlx_available() and "local-large-turbo" in MODEL_REGISTRY:
        return "local-large-turbo"
    # MVP2.0.6: the distil-large-v3 smart pick is no longer the
    # default fallback (it's commented out in MODEL_REGISTRY
    # per manualTodo 2.2). On non-MLX Macs, fall back to "base"
    # — the recommended manual pick.
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
    *,
    language: str | None = None,
) -> dict[str, Any]:
    """Transcribe a video file and return timestamped segments.

    Args:
        video_path: Path to the video file.
        model_name: Whisper model to use (base, small, medium, etc.).
        language: Optional ISO 639-1 code to LOCK whisper's
            language for the whole file (MVP3.0 #2b, jul 13
            2026 — see detect_audio_language for the anti-drift
            rationale). If None, faster-whisper auto-detects per
            window (legacy behaviour).

    Returns:
        Dict with:
            - segments: list of {start, end, text}
            - language: detected (or locked) language
            - duration: total duration in seconds
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    model = get_model(model_name)

    # Anti-drift params for faster-whisper too (same idea as
    # the MLX branch — kill the per-window drift chain on long
    # audio). These are no-ops for short audio.
    transcribe_kwargs: dict[str, Any] = {
        "beam_size": 5,
        "condition_on_previous_text": False,
        "compression_ratio_threshold": 1.8,
    }
    if language:
        transcribe_kwargs["language"] = language
        transcribe_kwargs["initial_prompt"] = get_initial_prompt(language)

    segments_iter, info = model.transcribe(str(video_path), **transcribe_kwargs)

    segments = []
    for seg in segments_iter:
        segments.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
        })

    return {
        "segments": segments,
        "language": language or info.language,
        "duration": round(info.duration, 2),
    }


def transcribe_with_backend(
    video_path: str | Path,
    choice: str,
    *,
    on_progress: Any = None,
    language: str | None = None,
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
        language: Optional ISO 639-1 code (e.g. "zh", "en") to LOCK
            whisper's language for the whole file. If None or
            "unknown", whisper auto-detects per window (old
            behaviour — susceptible to drift on long audio). If
            set, this is the primary anti-drift fix (MVP3.0 #2b,
            jul 13 2026 — see doc/BlockersOrChallengers.md §2.4
            for the rationale). The caller is expected to have
            already run detect_audio_language() or otherwise
            decided on a language; we don't re-validate here.

    Returns:
        Dict with `segments`, `language`, `duration`, plus
        `_meta: {backend, model_id, choice, fallback_occurred,
        fallback_reason, language_locked}`.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    resolved = resolve_model_choice(choice)
    backend = resolved["backend"]
    model_id = resolved["model_id"]

    # Normalise the language arg. None / "auto" / "unknown" all
    # mean "let whisper do its own per-window detection" (the
    # legacy behaviour). A concrete code (zh, en, ja, ...) means
    # LOCK it for the whole file.
    locked_language: str | None = None
    if language and language not in ("auto", "unknown"):
        locked_language = language

    if backend == "faster-whisper":
        # Reuse the existing transcribe_video() — it already
        # handles the faster-whisper model loading, caching, and
        # segment streaming. We just call it with model_id and
        # pass the language lock through.
        result = transcribe_video(video_path, model_id, language=locked_language)
    elif backend == "mlx-whisper":
        # MLX Whisper — Apple Silicon only. Uses the user's choice
        # of `model_id` (e.g. `mlx-community/whisper-large-v3-turbo`
        # or `distil-large-v3`) by passing it as `path_or_hf_repo`
        # to mlx_whisper.transcribe(). The library auto-downloads
        # and caches the model weights on first use.
        #
        # MVP3.0 #2b (jul 13 2026): the long-audio drift fix. The
        # 1.7 GB / 2.5h Mandarin file (jul 13 2026) produced 296
        # identical "Thank you." segments because whisper's per-
        # window language detection drifted to English, and
        # `condition_on_previous_text=True` chained the drift across
        # all subsequent windows. Three changes here:
        #   1. Pass `language=locked_language` so the model is
        #      LOCKED to the user/auto-detected language for the
        #      whole file (no per-window re-detection).
        #   2. Set `condition_on_previous_text=False` — each 30s
        #      window is decoded independently, so a single bad
        #      window can't poison the rest of the file.
        #   3. Tighten `compression_ratio_threshold` from 2.4 to
        #      1.8 — catches repetitive-text hallucination
        #      ("Thank you. You're going to do you.") early by
        #      falling back to higher temperature.
        #   4. Set `initial_prompt` from INITIAL_PROMPTS to bias
        #      the decoder toward the target language (e.g.
        #      "以下是普通话的对话。" for zh).
        #
        # The mlx-whisper API (v0.4+) returns a SINGLE DICT with
        # keys "text", "segments", "language" (verified against
        # mlx-whisper 0.4.3 — older versions returned a tuple,
        # but the current stable API is the dict). Note: the dict
        # does NOT include "duration" — we compute it from the
        # last segment's end time so the output shape matches
        # faster-whisper (which does include duration).
        #
        # Segment timestamps in mlx-whisper are in **seconds**
        # (verified against the lib's docs: "timestamps are in
        # seconds"), so no HH.MM conversion is needed. We just
        # strip whitespace and round to 2 decimals (matches
        # faster-whisper output).
        import mlx_whisper  # local import — only required when used
        kwargs: dict[str, Any] = {
            "path_or_hf_repo": model_id,
            # Anti-drift params (always on for MLX — even with
            # a locked language, these prevent a different class
            # of "the model is fine but it loops" bug).
            "condition_on_previous_text": False,
            "compression_ratio_threshold": 1.8,
        }
        if locked_language:
            kwargs["language"] = locked_language
            kwargs["initial_prompt"] = get_initial_prompt(locked_language)
        result_dict = mlx_whisper.transcribe(
            str(video_path),
            **kwargs,
        )
        segments: list[dict[str, Any]] = []
        for seg in result_dict.get("segments", []):
            segments.append({
                "start": round(float(seg.get("start", 0.0)), 2),
                "end": round(float(seg.get("end", 0.0)), 2),
                "text": str(seg.get("text", "")).strip(),
            })
        # mlx-whisper 0.4+ doesn't return duration in the dict
        # (unlike faster-whisper's info.duration). Compute it from
        # the last segment's end time. Fall back to 0 if there
        # are no segments.
        duration = round(
            float(segments[-1]["end"]) if segments else 0.0, 2
        )
        # If we locked the language, trust the lock and use it as
        # the canonical language in the result (rather than
        # whatever whisper's per-window detection came up with,
        # which is the whole point of locking).
        result = {
            "segments": segments,
            "language": locked_language or result_dict.get("language", "unknown"),
            "duration": duration,
        }
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
        "language_locked": locked_language,
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