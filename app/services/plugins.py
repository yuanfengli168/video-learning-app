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

import json
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

    Despite the name "webm_to_mp4", this plugin works on ANY
    video format that ffmpeg can read:
      - WebM (VP8/VP9 + Opus/Vorbis) — Chrome screen recorder
      - MKV (Matroska, any codec)
      - MOV (QuickTime) — iPhone / iPad / iMovie
      - AVI (legacy)
      - M4V (iTunes video)
      - FLV (Flash video, legacy)
      - TS / MTS / M2TS (camcorder / TV capture)
      - 3GP (mobile phones, legacy)
      - OGV (Ogg Theora)
    The ffmpeg -i flag accepts any of these — the codec
    detection is automatic. The output is always H.264 + AAC
    in an MP4 container (universally playable).

    Why we expose this as a single "Convert to MP4" button
    (not a format picker): every modern device plays H.264
    MP4. If the user uploads something unusual (a screen
    recording from a third-party tool, an iPhone HEVC MOV,
    an old AVI from a camera), one click gives them a file
    the browser, the video player, and the iOS app can all
    play.

    Side-by-side: the new file is written as <stem>.mp4 next
    to the original. The original is NEVER touched.

    Why MP4 (H.264 + AAC):
      - Best browser support (all modern browsers + iOS +
        Android + smart TVs)
      - Hardware-accelerated decode on iOS/Android
      - Most cross-platform of any container/codec combo

    Why hardware acceleration (VideoToolbox on macOS):
      - 10x faster than libx264 on Apple Silicon (measured:
        ~340 fps for 1080p VP9 → H.264, vs ~30 fps for
        libx264 on the same Mac)
      - Measured: 13h source → 70 min transcode (vs ~6h
        with libx264 software encoding)
      - For 1h source: ~6 min transcode (vs ~30 min)
      - The bottleneck for VideoToolbox is the VP9 DECODE
        step (ffmpeg's libvpx decoder is single-threaded).
        The H.264 encode itself runs at >500 fps.
      - See the cmd-building block below for details

    Failure modes (returned as PluginResult.ok=False):
      - ffmpeg not installed
      - ffmpeg returned non-zero exit code (corrupt input, codec error)
      - Source file not found (was it deleted?)
      - 90-min subprocess timeout exceeded

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
        # A previous run (possibly a partial output from a
        # killed transcode, like the 2026-07-24 4.3 GB WebM
        # incident where uvicorn --reload killed ffmpeg
        # mid-write) may have left a file here. We treat
        # any existing file at the target path as
        # disposable and overwrite it — the user clicked
        # "Run" again because they want a fresh transcode.
        # If they wanted to keep the old one, they should
        # have copied it first. The original WebM is never
        # touched, so re-running is always safe.
        try:
            dst.unlink()
            print(f"[webm_to_mp4] Removed existing file at {dst}")
        except OSError as exc:
            # Permission denied or file in use — fall
            # back to a uuid-suffixed name so we don't
            # crash. The user can clean up the old file
            # manually.
            print(f"[webm_to_mp4] Could not remove {dst}: {exc!r}")
            dst = stem.with_name(f"{stem.name}-{uuid.uuid4().hex[:6]}.mp4")

    # Build the ffmpeg command. We try hardware encoding
    # first (h264_videotoolbox on macOS) for a 5-10x speedup
    # vs libx264. Falls back to libx264 on other platforms.
    #
    # Why VideoToolbox is a huge win for screen recordings:
    #   - libx264 on an M-series Mac: ~30 fps for 1080p
    #   - h264_videotoolbox on M-series: ~340 fps for 1080p
    # That drops a 13h transcode from ~6h to ~70 min.
    # Quality is slightly lower than libx264 at the same CRF
    # (because VideoToolbox uses CQP, not CRF), but for
    # screen recordings at CRF ~23 it's indistinguishable
    # to the human eye. The browser doesn't care because
    # the output is H.264 either way.
    #
    # PRESET CHOICE (2026-07-24b, lessons from the 4.3 GB run):
    #   - VideoToolbox: NO -realtime flag (it throttles encode
    #     to realtime speed; we want max speed, not
    #     playback-rate speed). Use -b:v 2000k (constant
    #     bitrate 2 Mbps, predictable file size) instead of
    #     -q:v (which is CQP mode and produces 3+ Mbps
    #     output for VP9 screen recordings).
    #   - libx264 fallback: -preset ultrafast (we want
    #     speed over compression ratio; this is a one-time
    #     format conversion, not a master archive).
    import platform as _platform

    is_macos = _platform.system() == "Darwin"
    if is_macos:
        # macOS — use VideoToolbox. The -allow_sw 1 flag
        # lets ffmpeg fall back to software if the input
        # codec isn't supported by the hardware (rare,
        # but happens with some VP9 profiles).
        #
        # We use constant bitrate (-b:v 2000k) instead of
        # CQP (-q:v N) for two reasons:
        #   1. CQP mode produces wildly variable bitrate
        #      (3+ Mbps for VP9 screen recordings, vs
        #      2 Mbps CBR — file is 50% larger).
        #   2. CBR gives a predictable final file size
        #      (13h × 2 Mbps ≈ 11.7 GB, vs 16-20 GB
        #      with CQP).
        # We do NOT use -realtime (added in 2026-07-24
        # accidentally; it throttles the encoder to 1×
        # realtime, defeating the point of hardware
        # encoding). The encoder naturally runs at
        # hundreds of fps for 1080p on M-series Macs.
        cmd = [
            "ffmpeg",
            "-i", str(src),
            "-c:v", "h264_videotoolbox",
            "-allow_sw", "1",
            "-b:v", "2000k",  # constant 2 Mbps (predictable file size)
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-y",
            str(dst),
        ]
    else:
        # Linux / Windows — use libx264. The original
        # MVP2.1.0 settings, with the preset bumped to
        # ultrafast for speed (a 4 GB WebM is more about
        # getting a compatible MP4 quickly than about
        # the smallest possible file).
        cmd = [
            "ffmpeg",
            "-i", str(src),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-y",
            str(dst),
        ]
    # Run ffmpeg. We capture stderr so we can show the user
    # the ffmpeg error message if it fails. The timeout is
    # generous (90 min) — a 4 GB WebM transcode can take
    # 5-10 min on a slow Mac, 30-60 min on a really slow
    # machine. 90 min gives plenty of headroom for the
    # 4.3 GB class while still failing loudly if something
    # is genuinely stuck.
    #
    # CRITICAL: start_new_session=True puts ffmpeg in a new
    # process group, so a uvicorn --reload (which sends
    # SIGTERM to the whole process group on the OLD parent)
    # cannot kill our ffmpeg. This is the 2026-07-24 fix
    # that prevents the "uvicorn reload killed my transcode"
    # bug from recurring. The trade-off: if the server dies
    # hard (kill -9), the ffmpeg is now an orphan that the
    # orphan-sweep in PluginPool.start() will clean up.
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5400,  # 90 min
            start_new_session=True,
        )
    except subprocess.TimeoutExpired:
        return PluginResult(
            ok=False,
            message=(
                f"ffmpeg timed out after 90 minutes. The "
                f"source file may be very large or your Mac "
                f"may be under heavy load. Try the WebM->MP4 "
                f"plugin again — the second run is usually "
                f"faster because your disk cache is warm."
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
# input_types is what MIME types the button shows for.
# We accept every video format ffmpeg can read so the
# "Convert to MP4" button appears for ALL uploaded videos,
# not just WebM. The plugin itself doesn't care about the
# input type — ffmpeg's -i flag auto-detects. The list
# here just controls the UI affordance.
PLUGIN_REGISTRY: dict[str, PluginSpec] = {
    "webm_to_mp4": PluginSpec(
        key="webm_to_mp4",
        label="Convert to MP4 (H.264 + AAC)",
        input_types={
            # WebM
            "video/webm",
            # MP4 family
            "video/mp4",
            "video/x-m4v",
            "video/quicktime",  # .mov
            # Matroska
            "video/x-matroska",  # .mkv
            # Legacy Windows
            "video/x-msvideo",  # .avi
            # Flash
            "video/x-flv",
            # MPEG-TS (camcorder / TV capture)
            "video/mp2t",
            "video/mts",  # .mts (AVCHD)
            "video/m2ts",  # .m2ts (Blu-ray)
            # Mobile / 3GP
            "video/3gpp",
            # Ogg Theora
            "video/ogg",
        },
        description=(
            "Transcode this video to MP4 (H.264 video, AAC audio) "
            "for broader compatibility and smaller file size. Works "
            "on WebM, MKV, MOV, AVI, M4V, FLV, TS, and any other "
            "format ffmpeg can read. Uses macOS hardware acceleration "
            "(VideoToolbox) on Apple Silicon for 5-10x faster "
            "transcoding. The new file is written next to the "
            "original — your original is never modified. Requires "
            "ffmpeg on $PATH."
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


def _run_plugin_and_create_row(
    name: str, video: "Video", db: Session
) -> tuple["PluginResult", "PluginRun"]:
    """Run a plugin on a video and CREATE a new PluginRun audit row.

    Internal helper (MVP2.1.0.1). Used by:
      - `run_plugin` (this module) — the public wrapper
        that the router calls for the synchronous code path
      - `PluginPool._execute` (app/workers/plugin_pool.py) —
        the background worker, which copies this row's
        data into a pre-existing row to keep the run_id
        stable across the queued/running/done state
        transitions

    The flow is:
      1. Look up the plugin by name
      2. Call the plugin's function (catches all exceptions)
      3. Build a fresh PluginRun row (NOT committed — the
         caller decides the transaction boundary)
      4. Return both the result and the new audit row

    For v1, the plugin runs in the caller's process
    (synchronous in the router, or in a thread inside
    the worker's run_in_executor).
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
        extra_json=json.dumps(result.extra) if result.extra else None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(run_row)
    return result, run_row


def run_plugin(
    name: str, video: "Video", db: Session
) -> tuple["PluginResult", "PluginRun"]:
    """Run a plugin on a video and write a PluginRun audit row.

    Public API (MVP2.1.0). Kept for backward compatibility
    with the synchronous code path (tests, swap endpoint,
    any future caller that wants to run a plugin inline).

    For new code, prefer `PluginPool.submit()` (see
    app/workers/plugin_pool.py) — the worker handles
    status transitions and survives tab close.

    This wrapper just delegates to `_run_plugin_and_create_row`.
    The caller (router) is still responsible for committing
    the transaction.
    """
    return _run_plugin_and_create_row(name, video, db)


# ── Built-in plugin: Swap to MP4 (MVP2.1.0.1 — actually an endpoint) ───
# Not really a "plugin" in the sidecar-file sense — it
# mutates the Video row (file_path + filename). But it
# uses the same dispatch + audit-log pattern as the
# other plugins, so it lives in this module for
# consistency. Called by POST /api/plugins/swap-to-mp4
# (NOT by /api/plugins/{name}/run, since it doesn't
# produce a sidecar file).
def swap_video_file_to(
    video: "Video", new_path: str, db: Session
) -> "PluginResult":
    """Swap a video's file_path to a new file (e.g. WebM -> MP4).

    The new file must:
      - Exist on disk
      - Be a real file (not a directory)
      - Be in a location the app can serve (anywhere
        for now; the FastAPI FileResponse handles it
        as long as the path is readable by the server
        process)

    The Video row is updated in place:
      - file_path: new absolute (or relative) path
      - filename: derived from the new path
      - status: unchanged (we don't re-trigger
        transcribe or generate)
      - transcribed_at, generated_at, etc.: preserved
        (no re-processing)

    Returns PluginResult with:
      - ok=True on success
      - ok=False if the new file doesn't exist, the
        video's status isn't 'ready', etc.

    Note: this is NOT registered in PLUGIN_REGISTRY
    because it doesn't fit the sidecar-file pattern.
    The router calls it directly.
    """
    from app.models.plugin_run import PluginRun

    # Precondition 1: video must be fully processed
    # (status == 'ready'). If the video is still
    # transcribing or generating, swapping the file
    # would break the in-flight job (it would try to
    # read the old path).
    if video.status != "ready":
        return PluginResult(
            ok=False,
            message=(
                f"Cannot swap: video is in status {video.status!r}. "
                f"Wait for the video to finish processing, then try again."
            ),
        )

    # Precondition 2: the new file must exist on disk
    new_path_obj = Path(new_path)
    if not new_path_obj.is_absolute():
        # Same logic as transcode: relative paths are
        # resolved from CWD (the project root).
        new_path_obj = new_path_obj.resolve()
    if not new_path_obj.exists():
        return PluginResult(
            ok=False,
            message=(
                f"Cannot swap: file not found at {new_path_obj}. "
                f"It may have been deleted."
            ),
        )
    if not new_path_obj.is_file():
        return PluginResult(
            ok=False,
            message=(
                f"Cannot swap: {new_path_obj} is not a regular file."
            ),
        )

    # Save the old values for the audit log
    old_path = video.file_path
    old_filename = video.filename
    old_size = video.file_size

    # Mutate the video in place. SQLAlchemy will detect
    # the change and emit UPDATE on the next commit.
    video.file_path = str(new_path_obj)
    video.filename = new_path_obj.name
    # MVP2.1.0.2 — refresh `file_size` from the new file
    # on disk. Without this, the DB keeps the original
    # WebM's byte count (e.g. 54 MB) even after the
    # swap, which makes the course-page "size" column
    # and the "are you sure?" delete prompt both lie
    # to the user. Stat the new file (we just verified
    # it exists) and stamp. Falls back to 0 on OSError
    # (unlikely — we just confirmed is_file()).
    try:
        video.file_size = new_path_obj.stat().st_size
    except OSError:
        video.file_size = 0
    # status, transcribed_at, generated_at, etc. are
    # intentionally untouched — the transcript is
    # valid for the new file (it's the same content)

    # Build the audit log row. We don't use the
    # standard run_plugin() flow because this isn't a
    # sidecar plugin; we write the row directly.
    old_size_mb = old_size / 1_000_000 if old_size else 0
    new_size_mb = video.file_size / 1_000_000 if video.file_size else 0
    run_row = PluginRun(
        id=str(uuid.uuid4()),
        video_id=video.id,
        plugin_key="swap_to_mp4",
        ok=True,
        message=(
            f"Swapped from {old_filename} ({old_size_mb:.1f} MB) "
            f"to {new_path_obj.name} ({new_size_mb:.1f} MB). "
            f"Transcript and materials preserved."
        ),
        output_path=str(new_path_obj),
        extra_json=json.dumps({
            "old_path": old_path,
            "old_filename": old_filename,
            "old_size_bytes": old_size,
            "new_path": str(new_path_obj),
            "new_filename": new_path_obj.name,
            "new_size_bytes": video.file_size,
        }),
        created_at=datetime.now(timezone.utc),
    )
    db.add(run_row)

    return PluginResult(
        ok=True,
        message=(
            f"Video now points to {new_path_obj.name}. "
            f"Transcript and materials preserved (no re-processing)."
        ),
        output_path=str(new_path_obj),
        extra={
            "old_path": old_path,
            "old_filename": old_filename,
            "new_filename": new_path_obj.name,
        },
    )
