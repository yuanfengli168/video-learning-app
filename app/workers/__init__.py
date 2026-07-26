"""Background worker pools (MVP2.1.0.1).

Each pool is a small asyncio-based dispatcher that:
  - Bounded concurrency (e.g. 3 plugins at once)
  - Persistent state (rows in the DB, not in-memory)
  - Survives tab close (jobs continue in the server process)
  - Reuses the existing `app.jobs` status dict for the
    transcribe / generate pipelines (NOT plugins — plugins
    have their own `plugin_runs` table)

Public API
----------
- `plugin_pool`  — module-level singleton of `PluginPool`
                   (limit=3, started by the FastAPI app
                   on startup)

Why not reuse `app.jobs` (the in-memory status tracker)?
  `app.jobs` is a dict keyed by (video_id, job_type) and
  only tracks ONE job per (video, type) at a time. Plugin
  runs are user-invoked side actions (transcode, etc.) that
  don't have a 1:1 mapping with the video — the same user
  can run multiple plugins on the same video (e.g. transcode
  + extract subtitles + future "normalize audio"). Each
  plugin run gets its own `plugin_runs` row, so the pool
  tracks runs by `run_id`, not by (video_id, plugin_key).
"""
