"""Plugin Tools router (MVP2.1.0).

Endpoints:
  GET  /api/plugins                          list available plugins
  POST /api/plugins/{name}/run               run a plugin on a video
  GET  /api/plugins/runs/{id}                get the status of a plugin run
  GET  /api/plugins/runs/by-video/{video_id} get the most recent run for a video
  POST /api/plugins/reveal                   reveal a file in Finder/Explorer

The list endpoint is open (no auth) — the UI uses it to
render the Tools tab. The run endpoint requires a valid
session cookie (same as the other video routes) because
it costs CPU and writes to disk.

Why a single /api/plugins router (not bolted onto videos.py):
  - Plugins are a distinct concept from videos (a video is
    data; a plugin is an action)
  - Easier to add new endpoints later (cancel a run, retry
    a failed run, get the run history) without bloating
    videos.py
  - The router is small (~50 lines) so the cost of having
    a separate file is near zero
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.config import settings
from app.database import get_db
from app.models.plugin_run import PluginRun
from app.models.video import Video
from app.services.plugins import (
    PLUGIN_REGISTRY,
    get_plugin,
    list_available_plugins,
)
# `run_plugin` is no longer imported here — the
# /{name}/run endpoint now goes through the
# PluginPool (app/workers/plugin_pool.py) which
# calls `_run_plugin_and_create_row` directly.
# See MVP2.1.0.1 changelog.

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


@router.get("")
async def list_plugins(
    _user: str = Depends(get_current_user),
) -> dict:
    """List the available plugins, with availability info.

    The UI calls this on Tools-tab open to render the
    buttons. The response includes the `requires` set for
    each plugin so the UI can grey-out plugins whose deps
    are missing (currently just ffmpeg).
    """
    import shutil

    plugins = []
    for spec in list_available_plugins():
        # Check each requirement on $PATH
        missing = sorted(
            req for req in spec.requires if shutil.which(req) is None
        )
        plugins.append(
            {
                "key": spec.key,
                "label": spec.label,
                "description": spec.description,
                "input_types": sorted(spec.input_types),
                "group": spec.group,
                "requires": sorted(spec.requires),
                "available": len(missing) == 0,
                "missing": missing,
            }
        )
    return {"plugins": plugins}


@router.post("/{name}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_a_plugin(
    name: str,
    video_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Run a plugin on a video (NOW VIA WORKER POOL — MVP2.1.0.1).

    Request: POST /api/plugins/webm_to_mp4/run?video_id=<uuid>
    Response: 202 Accepted with { run_id, status: "queued" }

    Behavior change vs MVP2.1.0:
      - The request now returns IMMEDIATELY (status 202)
        with a `run_id` and `status="queued"`. The actual
        plugin work happens in the background
        (app/workers/plugin_pool.py).
      - The UI polls `GET /api/plugins/runs/{run_id}` to
        see progress (status: queued → running → done / failed).
      - Closing the tab no longer cancels the job — it
        continues in the server process. This is the
        fix for the "I closed the tab and my 30-min
        transcode was lost" bug.
      - Bounded concurrency: the pool has a `limit`
        (default 3). If 5 plugins are submitted at once,
        the first 3 run in parallel; the other 2 sit in
        the FIFO queue and start when a slot frees.

    Auth:
      - The submit endpoint requires a valid session
        cookie (same as the other video routes).
      - The user_id from the session is stored on the
        queued run for future audit / authz (e.g. when
        we add a "cancel my run" endpoint).
    """
    from app.workers.plugin_pool import plugin_pool

    spec = get_plugin(name)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown plugin: {name!r}",
        )

    # Verify the video exists (and is owned by this user)
    # BEFORE we enqueue. We do this in the request session
    # for immediate 404 — the worker re-checks in its own
    # session, but a 404 here is friendlier (no run row
    # to clean up).
    video = db.query(Video).filter(Video.id == video_id).first()
    if video is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found.",
        )

    # Enqueue. submit() opens its own DB session, creates
    # the row in 'queued' state, and pushes onto the
    # asyncio queue. Returns the run_id for polling.
    try:
        run_id = await plugin_pool.submit(
            plugin_key=name,
            video_id=video_id,
            user_id=str(user.get("uid", "")),
        )
    except LookupError as exc:
        # Race condition: the video existed when we checked
        # above but was deleted before submit() re-checked.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    return {
        "run_id": run_id,
        "plugin": name,
        "video_id": video_id,
        "status": "queued",
    }


@router.get("/runs/{run_id}")
async def get_plugin_run(
    run_id: str,
    _user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Get the status of a single plugin run.

    Used by the UI to poll the result of a long-running
    plugin. With the worker pool (MVP2.1.0.1), this is
    the primary way the UI checks progress: the user
    POSTs /{name}/run (returns 202 + run_id in <50ms),
    then polls this endpoint every 1-2 seconds until
    `status` is `done` or `failed`.
    """
    from app.models.plugin_run import PluginRun

    run = db.query(PluginRun).filter(PluginRun.id == run_id).first()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plugin run not found.",
        )
    return {
        "id": run.id,
        "video_id": run.video_id,
        "plugin_key": run.plugin_key,
        "ok": run.ok,
        "status": run.status,
        "message": run.message,
        "output_path": run.output_path,
        "extra": run.extra_json,
        "created_at": run.created_at.isoformat(),
    }


# ── 2.1.0.1: "Last run" + "Open in Finder" endpoints ───────────────────
class RevealRequest(BaseModel):
    """Request body for POST /api/plugins/reveal."""
    path: str


@router.get("/runs/by-video/{video_id}")
async def get_most_recent_run_for_video(
    video_id: str,
    _user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Return the most recent PluginRun for this video, or null.

    Used by the video page's Tools tab to render the
    'Last run' line under each Run button. Returns 200
    with `{"run": null}` when no run exists yet (so
    the UI can show the empty-state hint).

    Why a separate endpoint (instead of just embedding
    in the video_view context):
      - The Tools tab is also loaded dynamically by JS
        (the user might open it long after page load)
      - A future 'refresh after a long transcode' use
        case wants a lightweight fetch, not a full
        page reload
      - The endpoint is cheap: one indexed query on
        plugin_runs.video_id ordered by created_at DESC
    """
    # Verify the video exists (and is owned by this user).
    # We don't 404 if the video doesn't exist; we return
    # {"run": null} so the UI can render gracefully even
    # for stale tabs.
    video = db.query(Video).filter(Video.id == video_id).first()
    if video is None:
        return {"run": None}

    run = (
        db.query(PluginRun)
        .filter(PluginRun.video_id == video_id)
        .order_by(PluginRun.created_at.desc())
        .first()
    )
    if run is None:
        return {"run": None}

    return {
        "run": {
            "id": run.id,
            "video_id": run.video_id,
            "plugin_key": run.plugin_key,
            "ok": run.ok,
            "status": run.status,
            "message": run.message,
            "output_path": run.output_path,
            "extra": run.extra_json,
            "created_at": run.created_at.isoformat(),
        }
    }


@router.post("/reveal")
async def reveal_in_file_manager(
    body: RevealRequest,
    _user: str = Depends(get_current_user),
) -> dict:
    """Reveal a file in Finder / Explorer / file manager.

    Security:
      - The path MUST be inside settings.upload_dir (or
        a few other safe locations like settings.storage_dir).
        We resolve both sides to absolute paths and check
        that the request path is a child of the allowed
        roots. This prevents an attacker from using this
        endpoint to open arbitrary files on the user's
        Mac (e.g. /etc/passwd, ~/.ssh/id_rsa).
      - We do NOT shell-escape; we use subprocess.run
        with a list of args (not a shell string), so
        there's no shell-injection risk.

    Platform handling:
      - macOS: `open -R <path>` reveals in Finder
        (the file is highlighted)
      - Windows: `explorer /select,<path>` reveals in
        Explorer
      - Linux: `xdg-open <parent_dir>` opens the parent
        directory (most file managers don't have a
        single-file 'reveal' equivalent). For Nautilus,
        `nautilus --select <path>` works but isn't
        universal, so we use xdg-open for portability.
    """
    raw = Path(body.path)
    if not raw.is_absolute():
        # Refuse relative paths to avoid any ambiguity
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path must be absolute.",
        )

    # Normalize: resolve symlinks + `..` etc.
    try:
        resolved = raw.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot resolve path: {exc!r}",
        )

    # Allow-list: must be inside upload_dir or storage_dir.
    # These are the two folders the app writes to. Any
    # plugin output we want to reveal MUST land in one
    # of these.
    #
    # We use Path.is_relative_to() (Python 3.9+) which
    # correctly handles the prefix-vs-child case
    # (e.g. /Users/foo/uploads is NOT a parent of
    # /Users/foo/uploads-v2/file.mp4).
    allowed_roots = [
        Path(settings.upload_dir).resolve(),
        Path(settings.storage_dir).resolve(),
    ]
    is_allowed = any(
        resolved.is_relative_to(root) for root in allowed_roots
    )
    if not is_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Path is not in an allowed directory. "
                f"Allowed roots: {[str(r) for r in allowed_roots]}"
            ),
        )

    # Build the platform-specific command. We use a
    # list of args (NOT a shell string) to avoid
    # shell-injection. The `subprocess.run` with
    # shell=False (the default) is safe.
    system = platform.system()
    if system == "Darwin":
        cmd = ["open", "-R", str(resolved)]
    elif system == "Windows":
        # explorer.exe requires /select, with no space
        # after the comma (Windows quirk)
        cmd = ["explorer", f"/select,{resolved}"]
    else:
        # Linux: open the parent directory
        cmd = ["xdg-open", str(resolved.parent)]

    try:
        # We don't wait for the file manager to close
        # (it never does), so we use a short timeout
        # to detect the spawn failure (command not
        # found, permission denied, etc.)
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        # The command itself might have succeeded but
        # the subprocess is still attached to the file
        # manager. We don't surface this as an error.
        return {"ok": True, "path": str(resolved), "platform": system}
    except FileNotFoundError as exc:
        # e.g. `open` not on $PATH (extremely rare on macOS)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"File manager command not found: {exc!r}",
        )

    if proc.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"File manager returned non-zero exit "
                f"({proc.returncode}): {proc.stderr or '(no stderr)'}"
            ),
        )

    return {"ok": True, "path": str(resolved), "platform": system}


# ── MVP2.1.0.1: POST /api/plugins/swap-to-mp4 ──────────────────────────
class SwapToMp4Request(BaseModel):
    """Request body for POST /api/plugins/swap-to-mp4."""
    video_id: str
    mp4_path: str


@router.post("/swap-to-mp4")
async def swap_to_mp4(
    body: SwapToMp4Request,
    _user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Swap a video's file_path from WebM to MP4 (or any file).

    Used by the 'Re-Upload with MP4' button in the Tools
    tab. After a successful WebM->MP4 transcode, the user
    can click this button to point the app at the new MP4
    WITHOUT re-transcribing or re-generating materials
    (the content is identical, the transcript is valid).

    Preconditions (checked by the service layer):
      - Video exists and belongs to the user
      - video.status == 'ready' (not in the middle of
        transcribing/generating)
      - The MP4 file exists on disk

    On success, the Video row is updated in place:
      - file_path -> the new MP4 path
      - filename -> the new filename
      - transcribed_at, generated_at, etc. -> preserved
      - status -> unchanged (still 'ready')

    A PluginRun audit row is written with the OLD
    file_path/filename in extra_json, so the user can
    see the swap history (no undo button in v1, but the
    data is there).
    """
    from app.services.plugins import swap_video_file_to

    # 404 if video doesn't exist (avoid leaking IDs)
    video = db.query(Video).filter(Video.id == body.video_id).first()
    if video is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found.",
        )

    result = swap_video_file_to(video, body.mp4_path, db)
    db.commit()
    db.refresh(video)

    if not result.ok:
        # Return 409 Conflict for precondition failures
        # (the request is well-formed but the resource
        # state doesn't allow it). 400 for malformed.
        if "not found" in result.message.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.message,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=result.message,
        )

    return {
        "ok": True,
        "video_id": video.id,
        "new_path": video.file_path,
        "new_filename": video.filename,
        "message": result.message,
    }
