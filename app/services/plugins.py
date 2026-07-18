"""Plugin Tools registry and built-in plugins (MVP2.1.0).

What this is:
  The Plugin Tools system from doc/MVP2.1-all.md §2. A
  registry of named, runnable actions that operate on a
  video. v1 ships with one plugin: WebM -> MP4 transcoding
  via ffmpeg (side-by-side, never overwrites the original).

Design:
  - A plugin is a function in this module, registered in
    `PLUGIN_REGISTRY` with a `PluginSpec` (label, input type,
    description, requires, group). Plugins are NOT loaded
    from disk dynamically — adding a new plugin = adding one
    entry to the dict. This is intentional:
      * No install/upgrade flow needed (plugins ship with
        the app)
      * No security audit needed (no third-party code at
        runtime)
      * No path-traversal risk
      * Easy to grep: every plugin is in this one file
  - `run_plugin(name, video, db)` is the single entry point
    the router calls. It dispatches to the registered
    function and writes a `PluginRun` row for the audit log.
  - `is_ffmpeg_available()` is checked at module load AND
    per-run, so the UI can show "ffmpeg not found" without
    crashing the app.
  - The transcode function is a *side-by-side* write: it
    produces `<stem>.mp4` next to the original WebM, and
    the user can decide whether to delete the WebM
    afterwards (a future plugin can add a "delete original"
    action). The original is NEVER touched.

Adding a new plugin (for the future):
  1. Write the function: `def extract_audio(video, db) -> PluginResult: ...`
  2. Add an entry to PLUGIN_REGISTRY:
       "extract_audio": PluginSpec(
           label="Extract audio (MP3)",
           input_types={"video/webm", "video/mp4", ...},
           description="Save the audio track as MP3 (smaller file).",
           requires={"ffmpeg"},
           group="media",
           function=extract_audio,
       )
  3. Restart the app (Python module reload picks it up).
  4. The Tools tab auto-shows the new plugin. No frontend
     changes needed.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.models.video import Video
    from app.models.plugin_run import PluginRun


# ── Plugin spec ────────────────────────────────────────────────────────
@dataclass
class PluginSpec:
    """Metadata + function pointer for a single plugin.

    Fields:
      key:        the stable id used in the URL/DB (e.g. "webm_to_mp4")
      label:      the human-readable name shown in the Tools tab
      input_types: set of MIME types this plugin accepts; empty = all
      description: 1-2 sentence explanation shown under the label
      requires:   set of system binaries the plugin needs; the plugin
                  is shown greyed-out in the UI if any are missing
      group:      "media" | "metadata" | "export" — used for the
                  future "optgroup" UI in the Tools tab. v1 only
                  has media plugins so this is unused for now.
      function:   the actual implementation. Signature:
                    (video: Video, db: Session) -> PluginResult
                    Not frozen=True so tests can mock the
                    function pointer without rebuilding the
                    registry.
    """

    key: str
    label: str
    input_types: set[str] = field(default_factory=set)
    description: str = ""
    requires: set[str] = field(default_factory=set)
    group: str = "media"
    function: Callable[["Video", Session], "PluginResult"] | None = None


@dataclass
class PluginResult:
    """Return value from a plugin function.

    Fields:
      ok:        True if the plugin ran successfully
      message:   human-readable summary (e.g. "Wrote 1.2 GB MP4 to
                 /Users/.../uploads/abc/lesson1.mp4")
      output_path: path to the new file (None if no file was written,
                 e.g. for a future "export metadata" plugin)
      extra:     optional dict for plugin-specific data (e.g.
                 {"duration_s": 720, "size_bytes": 1234567})
    """

    ok: bool
    message: str
    output_path: str | None = None
    extra: dict = field(default_factory=dict)


# ── ffmpeg detection ───────────────────────────────────────────────────
def is_ffmpeg_available() -> bool:
    """True if the `ffmpeg` binary is on $PATH and runnable.

    Called at module load (to compute the initial UI state) and
    per-run (in case the user installs ffmpeg between page loads).
    Uses `shutil.which` (no subprocess), so it's cheap to call.
    """
    return shutil.which("ffmpeg") is not None


# ── Built-in plugin: WebM -> MP4 ───────────────────────────────────────
def transcode_webm_to_mp4(video: "Video", db: Session) -> PluginResult:
    """Transcode a video to MP4 (H.264 + AAC) using ffmpeg.

    Side-by-side: the new file is written as <stem>.mp4 next
    to the original. The original is NEVER touched. The user
    can decide to delete the original via a future plugin
    (out of scope for v1).

    Why MP4 (H.264 + AAC):
      - Best browser support (all modern browsers + iOS +
        Android + smart TVs)
      - Smaller files than WebM at equivalent quality (in
        most cases)
      - Hardware-accelerated decode on iOS/Android

    Failure modes (returned as PluginResult.ok=False):
      - ffmpeg not installed (we caught this earlier but
        double-check)
      - ffmpeg returned non-zero exit code (corrupt
        input, codec error)
      - Source file not found (was it deleted?)

    Success: returns PluginResult with the new file path and
    size in bytes (for the UI to show "Saved X MB").

    Note on path handling: `video.file_path` is the
    absolute path written at upload time. We use it
    directly; we do NOT prepend upload_dir.
    """
    if not is_ffmpeg_available():
        return PluginResult(
            ok=False,
            message=(
                "ffmpeg not found on $PATH. Install it with "
                "`brew install ffmpeg` (macOS) or "
                "`apt install ffmpeg` (Linux), then reload "
                "this page."
            ),
        )

    # Resolve the source path. The videos.file_path column
    # is written at upload time as `settings.upload_path /
    # saved_filename` (see app/routers/videos.py). If
    # settings.upload_dir is the default "./uploads", the
    # stored value is the relative path "uploads/<uuid>.<ext>"
    # (e.g. "uploads/5b79e2c1-...mp4") because Path("./uploads")
    # normalized to "uploads" and str(Path) gives "uploads/...".
    # If settings.upload_dir is an absolute path, the stored
    # value is absolute (e.g. /Users/foo/uploads/<uuid>.mp4).
    #
    # So:
    #   - If absolute: use as-is
    #   - If relative: resolve from the project root (CWD).
    #     Do NOT prepend upload_dir — the relative path
    #     already starts with "uploads/". This was the
    #     2.1.0 double-prefix bug (fixed 2026-07-18 after
    #     the user reported "/uploads/uploads/...").
    src = Path(video.file_path)
    if not src.is_absolute():
        # Resolve from the project root (CWD when the
        # server was started). The relative path
        # "uploads/abc.mp4" is already relative to CWD.
        src = src.resolve()
    if not src.exists():
        return PluginResult(
            ok=False,
            message=(
                f"Source file not found: {src}. It may have "
                f"been deleted. Reload the page to refresh."
            ),
        )

    # The new file goes next to the original, with the
    # extension swapped. .webm -> .mp4, .mov -> .mp4, etc.
    # If a file with the target name already exists, we
    # append a short uuid suffix to avoid clobbering.
    stem = src.with_suffix("")  # /uploads/abc/lesson1
    dst = stem.with_suffix(".mp4")
    if dst.exists():
        dst = stem.with_name(f"{stem.name}-{uuid.uuid4().hex[:6]}.mp4")

    # Run ffmpeg. We use the simple "stream copy" -> "libx264"
    # pipeline:
    #   -c:v libx264      H.264 video codec (universal support)
    #   -preset medium    good speed/quality tradeoff
    #   -crf 23           good quality (~ visually lossless)
    #   -c:a aac          AAC audio (universal support)
    #   -movflags +faststart   put the moov atom at the front
    #                        so the file starts playing in
    #                        browsers without a full download
    #   -y                overwrite the (non-existent) dst
    # We capture stderr so we can show the user the ffmpeg
    # error message if it fails. The timeout is generous
    # (30 min) — a 4 GB WebM transcode can take 5-10 min
    # on a slow Mac.
    cmd = [
        "ffmpeg",
        "-i", str(src),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "aac",
        "-movflags", "+faststart",
        "-y",
        str(dst),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,  # 30 min
        )
    except subprocess.TimeoutExpired:
        return PluginResult(
            ok=False,
            message=(
                f"ffmpeg timed out after 30 minutes. The "
                f"source file may be very large or your Mac "
                f"may be under heavy load."
            ),
        )

    if proc.returncode != 0:
        # ffmpeg exits non-zero on any error. We surface
        # the last 500 chars of stderr so the user can see
        # what went wrong (codec not supported, corrupt
        # file, etc.) without dumping the entire log.
        err_tail = (proc.stderr or "").strip().splitlines()[-10:]
        err_msg = "\n".join(err_tail)
        return PluginResult(
            ok=False,
            message=(
                f"ffmpeg failed (exit {proc.returncode}):\n"
                f"{err_msg}"
            ),
        )

    # Success — compute the new file size for the UI
    size_bytes = dst.stat().st_size if dst.exists() else 0
    return PluginResult(
        ok=True,
        message=(
            f"Transcoded to MP4 ({size_bytes / 1_000_000:.1f} MB). "
            f"Original WebM is untouched. You can find the new file at: "
            f"{dst}"
        ),
        output_path=str(dst),
        extra={"size_bytes": size_bytes},
    )


# ── Registry ───────────────────────────────────────────────────────────
PLUGIN_REGISTRY: dict[str, PluginSpec] = {
    "webm_to_mp4": PluginSpec(
        key="webm_to_mp4",
        label="Convert to MP4 (H.264 + AAC)",
        input_types={"video/webm", "video/quicktime", "video/x-matroska"},
        description=(
            "Transcode this video to MP4 (H.264 video, AAC audio) "
            "for broader compatibility and smaller file size. The new "
            "file is written next to the original — your original is "
            "never modified. Requires ffmpeg on $PATH."
        ),
        requires={"ffmpeg"},
        group="media",
        function=transcode_webm_to_mp4,
    ),
}


def list_available_plugins(db: Session | None = None) -> list[PluginSpec]:
    """Return the list of registered plugins, with availability info.

    Used by the UI to render the Tools tab. Each plugin's
    `requires` set is checked at call time; if a dependency
    is missing, the plugin is returned with a sentinel that
    the template can use to render it as disabled.

    Currently we just return the raw list — the UI does the
    `is_ffmpeg_available()` check itself. This keeps the
    function pure (no I/O) and easy to test.
    """
    return list(PLUGIN_REGISTRY.values())


def get_plugin(name: str) -> PluginSpec | None:
    """Look up a plugin by its key, or None if not registered."""
    return PLUGIN_REGISTRY.get(name)


def run_plugin(
    name: str, video: "Video", db: Session
) -> tuple["PluginResult", "PluginRun"]:
    """Run a plugin on a video and write a PluginRun audit row.

    The flow is:
      1. Look up the plugin by name (404 if not found)
      2. Call the plugin's function
      3. Write a PluginRun row with the result
      4. Return both the result and the audit row

    The PluginRun row is committed by the caller (the
    router), not here. We use add() and let the router
    commit so the whole thing is one DB transaction.

    For v1, the plugin runs in the request-handling
    process (synchronous). For long-running plugins, the
    router can swap in BackgroundTasks without changing
    this function's signature.
    """
    from app.models.plugin_run import PluginRun  # local import

    spec = get_plugin(name)
    if spec is None or spec.function is None:
        # Unknown plugin key — this is a 404 in the router
        # but we return a result here so the audit log is
        # still written (helps debug 404s).
        result = PluginResult(
            ok=False,
            message=f"Unknown plugin: {name!r}",
        )
    else:
        # Run the actual plugin function
        try:
            result = spec.function(video, db)
        except Exception as exc:  # noqa: BLE001 — intentional broad
            # Catch any uncaught exception so the audit log
            # still gets a "failed" row. Re-raise after
            # logging? No — surface as a failed result so
            # the UI can show a friendly message.
            result = PluginResult(
                ok=False,
                message=f"Plugin {name!r} crashed: {exc!r}",
            )

    # Build the audit row
    run_row = PluginRun(
        id=str(uuid.uuid4()),
        video_id=video.id,
        plugin_key=name,
        ok=result.ok,
        message=result.message,
        output_path=result.output_path,
        extra_json=str(result.extra) if result.extra else None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(run_row)
    # The router commits; we don't commit here so the
    # router controls the transaction boundary.
    return result, run_row
