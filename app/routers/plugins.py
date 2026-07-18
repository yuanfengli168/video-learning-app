"""Plugin Tools router (MVP2.1.0).

Endpoints:
  GET  /api/plugins              list available plugins (with availability)
  POST /api/plugins/{name}/run   run a plugin on a video
  GET  /api/plugins/runs/{id}    get the status of a plugin run

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

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.video import Video
from app.services.plugins import (
    PLUGIN_REGISTRY,
    get_plugin,
    list_available_plugins,
    run_plugin,
)

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


@router.post("/{name}/run")
async def run_a_plugin(
    name: str,
    video_id: str,
    _user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Run a plugin on a video.

    Request: POST /api/plugins/webm_to_mp4/run?video_id=<uuid>
    Response: { run_id, ok, message, output_path }

    The run is synchronous for v1 (the router blocks until
    the plugin finishes). For long plugins (e.g. a future
    "transcribe 4-hour video with subtitles" plugin), we'd
    swap this for BackgroundTasks. For WebM -> MP4, the
    typical 1-hour video transcode is 2-5 minutes on a
    modern Mac, which is acceptable to block on.
    """
    spec = get_plugin(name)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown plugin: {name!r}",
        )

    # Load the video. If it doesn't exist or doesn't
    # belong to this user, return 404 (not 403, to avoid
    # leaking video IDs to other users).
    video = db.query(Video).filter(Video.id == video_id).first()
    if video is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found.",
        )

    # Run the plugin. This writes a PluginRun row to the
    # session but does NOT commit — we commit here so the
    # whole run is one DB transaction.
    result, run_row = run_plugin(name, video, db)
    db.commit()
    db.refresh(run_row)

    return {
        "run_id": run_row.id,
        "plugin": name,
        "video_id": video.id,
        "ok": result.ok,
        "message": result.message,
        "output_path": result.output_path,
        "extra": result.extra,
        "created_at": run_row.created_at.isoformat(),
    }


@router.get("/runs/{run_id}")
async def get_plugin_run(
    run_id: str,
    _user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Get the status of a single plugin run.

    Used by the UI to poll the result of a long-running
    plugin (mostly for future plugins; WebM -> MP4 is
    synchronous so the UI already has the result).
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
        "message": run.message,
        "output_path": run.output_path,
        "extra": run.extra_json,
        "created_at": run.created_at.isoformat(),
    }
