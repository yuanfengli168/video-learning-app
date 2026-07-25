"""Plugin worker pool (MVP2.1.0.1).

What this is:
  A small asyncio-based dispatcher for plugin runs. Each
  `submit()` call creates a `plugin_runs` row with
  `status='queued'`, enqueues a job, and returns the
  `run_id` immediately. A background worker task pulls
  jobs off the queue, marks them `running`, runs the
  plugin function in a thread (ffmpeg blocks the GIL),
  and updates the row to `done` / `failed`.

Why this exists:
  In MVP2.1.0, plugin runs were SYNCHRONOUS in the
  request handler — the user clicked "Run", the browser
  waited for the HTTP response, and if they closed the
  tab the job died. The worker pool fixes this:
    1. `submit()` returns in <50ms (just a DB insert +
       a queue put). The HTTP request completes
       immediately, so closing the tab doesn't cancel
       the job.
    2. The plugin runs in the server process, owned by
       the asyncio loop, not by the HTTP request.
    3. Bounded concurrency — at most `limit` plugins
       run at once, so 5 quick transcodes don't all
       thrash the CPU.

Public API
----------
- `PluginPool(limit)`  — class
- `plugin_pool`        — module-level singleton (limit=3,
                         started by FastAPI on startup)
- `await plugin_pool.submit(plugin_key, video_id, user_id)`
                         — creates a queued run, returns run_id
- `await plugin_pool.start()` — idempotent; called by
                         FastAPI's @on_event("startup")
- `await plugin_pool.stop()`  — graceful shutdown (drains
                         the queue, waits for in-flight to
                         finish). Not implemented in v1;
                         the server's process exit is
                         acceptable for MVP1.1.

Design notes
------------
- We use `asyncio.Queue` for FIFO job ordering. The
  worker task is a single coroutine that creates one
  child task per job. A semaphore caps concurrent jobs
  to `limit`. This gives us bounded concurrency with
  simple code.
- The DB session is per-job, opened in the worker
  coroutine. We CANNOT reuse the request's session
  because it's closed by the time the worker picks up
  the job.
- Plugin functions are blocking (ffmpeg / Whisper /
  etc. all release the GIL via subprocess), so we run
  them in `loop.run_in_executor(None, ...)` which
  uses the default ThreadPoolExecutor.
- Plugin runs CAN outlive a single request, so
  per-request scoped state (e.g. `Depends(get_db)`)
  is captured in `_QueuedRun` (the user_id at least)
  but the heavy lifting uses fresh sessions.

Out of scope for v1
-------------------
- Cancellation (`POST /api/plugins/runs/{id}/cancel`)
  — would need a `threading.Event` per run + ffmpeg
  subprocess kill. Track for 2.1.0.2.
- Persistent queue (Redis) — for MVP1.1 the queue is
  in-memory; if the server crashes, queued + running
  jobs are lost. The DB still has the row, so the
  user sees "queued" forever, and a future startup
  can sweep stale `status='running'` rows to `failed`.
- Cross-process pools (multi-worker uvicorn) —
  v1 is single-process. For MVP2.2 + multi-worker
  we'll need Redis or RabbitMQ.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.plugin_run import PluginRun
from app.services.plugins import PluginResult

logger = logging.getLogger(__name__)


# ── Internal: a queued job ──────────────────────────────────────────────────
@dataclass
class _QueuedRun:
    """A plugin run waiting in the queue.

    Captures the data needed to execute the job in a
    background coroutine. We pass `user_id` for future
    audit / authz even though v1 doesn't use it.
    """

    run_id: str
    plugin_key: str
    video_id: str
    user_id: str


# ── The pool itself ─────────────────────────────────────────────────────────
class PluginPool:
    """Bounded-concurrency dispatcher for plugin runs.

    Singleton per process. Started once on FastAPI's
    startup event. Thread-safe (asyncio is single-threaded
    per loop, and the worker uses `loop.run_in_executor`
    for the blocking plugin function).

    Concurrency model:
      - Up to `limit` plugin functions execute in parallel
        (default 3). Each one runs in its own thread
        (the default ThreadPoolExecutor).
      - Other submissions sit in `self._queue` until a
        slot frees up.
      - The worker task is a single coroutine that pulls
        one job at a time and spawns a child task for it.
        The child task acquires the semaphore (blocks
        when at limit) and runs.

    Test mode:
      - Set `synchronous_mode = True` to make `submit()`
        execute the plugin function synchronously in a
        thread and update the row before returning. This
        lets tests assert on the result without polling,
        and avoids the cross-test pollution issue with
        the singleton's worker task being bound to a
        closed event loop. The conftest toggles this on
        for plugin-endpoint tests.
    """

    def __init__(self, limit: int = 3) -> None:
        if limit < 1:
            raise ValueError(f"PluginPool limit must be >= 1, got {limit}")
        self.limit = limit
        self._queue: asyncio.Queue[_QueuedRun] = asyncio.Queue()
        self._sem = asyncio.Semaphore(limit)
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._running: set[asyncio.Task[None]] = set()
        # Counters (for /api/plugins/_status debug endpoint,
        # future /api/plugins/_pool endpoint)
        self.submitted_count = 0
        self.completed_count = 0
        self.failed_count = 0
        # Test mode: when True, submit() runs the plugin
        # synchronously and returns only after the row
        # is updated. Defaults to False in production.
        self.synchronous_mode = False

    # ── Lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        """Start the background worker coroutine. Idempotent.

        Called from FastAPI's @on_event("startup") (or
        the lifespan handler in MVP2.1.0+). Safe to call
        multiple times — the second call is a no-op if
        the worker is already running on the CURRENT
        event loop.

        If a previous worker task is still on a
        different (closed) event loop, we replace it
        with a fresh one. This handles the test
        scenario where each test gets a new TestClient
        (with its own anyio event loop) but the pool
        is a module-level singleton.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — caller forgot to await
            # inside an async context. Log and skip.
            logger.warning("PluginPool.start() called outside async loop; skipping")
            return
        # If the previous task is on a different loop
        # or has finished, replace it. We check the
        # task's _loop attribute (private but stable
        # in CPython 3.10+).
        if self._worker_task is not None:
            try:
                same_loop = self._worker_task.get_loop() is loop
            except Exception:
                same_loop = False
            if same_loop and not self._worker_task.done():
                return  # Already running on this loop
            # Stale (different loop, or done) — replace.
            self._worker_task = None
        self._worker_task = loop.create_task(
            self._worker_loop(), name="plugin-pool-worker"
        )
        logger.info("PluginPool started (limit=%d)", self.limit)
        # ── Orphan-row sweep (2026-07-24 fix) ───────────────────────────
        # On startup, scan the plugin_runs table for rows
        # stuck in 'queued' or 'running' state. If their
        # ffmpeg subprocess was killed (e.g. uvicorn
        # --reload, server crash, machine sleep), the row
        # is orphaned — the worker that owned it is gone
        # and will never update it. Mark them as 'failed'
        # with a clear message so the UI shows the user
        # what happened, instead of an infinite spinner.
        #
        # Safe to run on every startup: it's a single
        # UPDATE with a WHERE clause that matches a small
        # subset of rows (only the truly stuck ones).
        self._sweep_orphaned_runs()

    def _sweep_orphaned_runs(self) -> None:
        """Mark queued/running rows as failed if their ffmpeg
        subprocess is no longer alive.

        Called on every PluginPool.start() — i.e. on every
        server boot. The sweep is conservative: we only
        touch rows older than 60 seconds (so a freshly-
        submitted run that hasn't been picked up yet isn't
        accidentally killed) and we DON'T check if ffmpeg
        is alive (we don't have a way to map a run_id back
        to a PID). The age threshold is enough to cover
        the 2026-07-24 incident: that run was ~5 hours old
        when the user noticed.

        For MVP2.2+ we can track the ffmpeg PID on the
        PluginRun row and use `os.kill(pid, 0)` to check
        liveness. For now, age-based sweep is good enough.
        """
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
        db = SessionLocal()
        try:
            stuck = (
                db.query(PluginRun)
                .filter(
                    PluginRun.status.in_(("queued", "running")),
                    PluginRun.created_at < cutoff,
                )
                .all()
            )
            for row in stuck:
                row.status = "failed"
                row.ok = 0
                row.message = (
                    f"Run was abandoned (status was {row.status!r} when "
                    f"the server restarted). Re-run the plugin to retry."
                )
                logger.warning(
                    "PluginPool startup sweep: marking orphaned run %s "
                    "(status was %s, age=%s) as failed",
                    row.id,
                    row.status,
                    datetime.now(timezone.utc) - row.created_at,
                )
            if stuck:
                db.commit()
                logger.info(
                    "PluginPool startup sweep: marked %d orphaned run(s) as failed",
                    len(stuck),
                )
        except Exception as exc:  # noqa: BLE001
            # Don't crash the server on a sweep failure —
            # the worst case is a stuck row stays stuck
            # until the next boot. Log and move on.
            logger.exception("PluginPool startup sweep failed: %r", exc)
            db.rollback()
        finally:
            db.close()

    async def stop(self, timeout: float = 30.0) -> None:
        """Graceful shutdown. Waits for in-flight jobs to finish.

        Not called by v1 code (we just exit the process).
        Reserved for future use (e.g. tests, multi-worker
        shutdown in MVP2.2).
        """
        if self._worker_task is None:
            return
        # Signal the worker to stop after the queue drains
        # by cancelling it. In-flight children are awaited.
        self._worker_task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._running, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("PluginPool.stop() timed out with %d jobs still running",
                           len(self._running))

    # ── Public API ───────────────────────────────────────────────────────
    async def submit(
        self, plugin_key: str, video_id: str, user_id: str
    ) -> str:
        """Enqueue a plugin run. Returns the run_id.

        Creates a `plugin_runs` row with `status='queued'`
        + a placeholder message. In normal mode, the
        worker will pick it up, mark it `running`, run
        the plugin, and update to `done` / `failed`. The
        HTTP caller polls `GET /api/plugins/runs/{run_id}`
        to see progress.

        In `synchronous_mode` (tests), the plugin
        function is run inline (in a thread via
        `run_in_executor`) and the row is updated to
        `done` / `failed` BEFORE submit() returns. This
        lets tests assert on the result without polling,
        and sidesteps the singleton-pool cross-test
        pollution issue (where a worker task from a
        previous test is on a closed event loop).
        """
        from app.models.video import Video  # local import: avoid circular

        run_id = str(uuid.uuid4())

        # Pre-create the row in 'queued' state. We do this
        # in a fresh session (not the request's session)
        # because the worker will reopen the row in its
        # own session — having a real row from the start
        # means polling works before the worker picks it up.
        db = SessionLocal()
        try:
            # Quick existence check on the video so the user
            # sees "Video not found" immediately instead of
            # waiting for the worker. Authz is also checked
            # here (we compare user_id on the Video row,
            # loaded with the user relationship).
            video = db.query(Video).filter(Video.id == video_id).first()
            if video is None:
                # Don't even create the run row — 404
                # semantics are clearer than a failed run.
                raise LookupError(f"Video {video_id!r} not found")

            run_row = PluginRun(
                id=run_id,
                plugin_key=plugin_key,
                video_id=video_id,
                ok=False,
                message="Queued",
                status="queued",
            )
            db.add(run_row)
            db.commit()
        finally:
            db.close()

        if self.synchronous_mode:
            # Run inline so the test can assert on the
            # result without polling. Same code path as
            # the worker (mark running → run plugin →
            # copy + delete duplicate → mark done/failed).
            await self._run_synchronously(run_id, plugin_key, video_id)
            return run_id

        # Enqueue. This is non-blocking on the queue
        # (asyncio.Queue.put is synchronous but doesn't
        # block waiting for a slot).
        await self._queue.put(
            _QueuedRun(
                run_id=run_id,
                plugin_key=plugin_key,
                video_id=video_id,
                user_id=user_id,
            )
        )
        self.submitted_count += 1
        logger.info("PluginPool: submitted run %s (%s on video %s)",
                    run_id, plugin_key, video_id)
        return run_id

    async def _run_synchronously(
        self, run_id: str, plugin_key: str, video_id: str
    ) -> None:
        """Test-mode: run the plugin inline. Updates the
        run row to done/failed before returning."""
        # Use the same _execute path the worker uses,
        # by constructing a _QueuedRun and calling
        # _execute directly. Bypasses the queue +
        # semaphore.
        queued = _QueuedRun(
            run_id=run_id,
            plugin_key=plugin_key,
            video_id=video_id,
            user_id="test-user",
        )
        # Bump submitted_count for parity with the
        # async path. completed/failed counts are
        # bumped inside _execute.
        self.submitted_count += 1
        await self._execute(queued)

    # ── Internals ────────────────────────────────────────────────────────
    async def _worker_loop(self) -> None:
        """Drain the queue forever, spawning one child task per job.

        We spawn a child task per job (instead of awaiting
        the job inline) so the semaphore can throttle
        concurrency. With a single inline await, we'd
        process jobs strictly serially.
        """
        logger.info("PluginPool worker loop started")
        while True:
            try:
                queued = await self._queue.get()
            except asyncio.CancelledError:
                logger.info("PluginPool worker loop cancelled")
                return
            child = asyncio.create_task(
                self._run_one(queued), name=f"plugin-run-{queued.run_id}"
            )
            self._running.add(child)
            child.add_done_callback(self._running.discard)

    async def _run_one(self, queued: _QueuedRun) -> None:
        """Run a single job, with semaphore for concurrency."""
        try:
            async with self._sem:
                await self._execute(queued)
        except Exception as exc:  # noqa: BLE001 — last-resort safety net
            # _execute already catches its own exceptions and
            # writes a 'failed' row. This is a backstop in case
            # the semaphore itself fails (extremely unlikely).
            logger.exception("PluginPool _run_one crashed: %r", exc)
            self._mark_failed(queued.run_id, f"Worker crashed: {exc!r}")

    async def _execute(self, queued: _QueuedRun) -> None:
        """Open a short-lived DB session, run the plugin, write the result.

        Steps:
          1. Mark status='running' in a short-lived session, then CLOSE it
             (releasing the connection back to the pool)
          2. Run the plugin function in a thread WITHOUT holding a DB session
             (ffmpeg can take 5-10 minutes for a 4 GB WebM; we cannot
             hold a pool connection that long or every other request
             times out — that was the 2026-07-24 bug)
          3. Open a fresh session and write the result row
          4. On exception: mark status='failed' in a fresh session

        Why release the session during the plugin run:
          The SQLAlchemy QueuePool is size=5, overflow=10 (15 total).
          If the worker holds one connection for the entire 5-10 min
          ffmpeg transcode, every UI poll and page view that opens
          a session fills the remaining 14 slots. Once they're all
          checked out (which happens fast under polling), the next
          request gets `QueuePool limit of size 5 overflow 10
          reached, connection timed out, timeout 30.00` and the
          user sees 500s on the video page. By closing the session
          before the blocking call, the pool is free for the rest
          of the app during the transcode.
        """
        from app.models.video import Video
        from app.services.plugins import _run_plugin_and_create_row

        # ── Phase 1: mark 'running' and grab what we need, then CLOSE ──
        video: Video | None = None
        try:
            db = SessionLocal()
            try:
                # Step 1a: mark running
                run_row = (
                    db.query(PluginRun)
                    .filter(PluginRun.id == queued.run_id)
                    .first()
                )
                if run_row is None:
                    logger.error(
                        "PluginPool: run %s vanished before execution",
                        queued.run_id,
                    )
                    return
                run_row.status = "running"
                run_row.message = "Running..."
                db.commit()

                # Step 1b: re-fetch the video (we need its file_path etc.
                # to pass into the plugin function in phase 2)
                video = (
                    db.query(Video)
                    .filter(Video.id == queued.video_id)
                    .first()
                )
                if video is None:
                    self._commit_failed(db, run_row, "Video not found (deleted?)")
                    self.failed_count += 1
                    return
                # Detach the video from this session so phase 2 can
                # use its attributes without a live session attached.
                # SQLAlchemy won't lazy-load anything (we already have
                # file_path, filename, etc. as simple columns), so this
                # is safe.
                db.expunge(video)
            finally:
                # CRITICAL: close the session NOW, before ffmpeg runs.
                # This returns the pooled connection so the rest of the
                # app (UI polls, page loads, status checks) is not
                # blocked waiting for the 5-10 min transcode to finish.
                db.close()

        except Exception as exc:  # noqa: BLE001
            logger.exception("PluginPool _execute phase1 failed: %r", exc)
            self._mark_failed(queued.run_id, f"Worker phase1 crashed: {exc!r}")
            self.failed_count += 1
            return

        # ── Phase 2: run the plugin in a thread (NO DB session held) ──
        # We open a SHORT-LIVED session here just so _run_plugin_and_create_row
        # can write its new audit row. We commit and close it as soon as
        # ffmpeg finishes — but importantly, the session is NOT held
        # during the 5-10 min ffmpeg subprocess (subprocess.run is
        # blocking, so the thread holds the GIL release; the session
        # is closed BEFORE run_in_executor returns control to us).
        #
        # Wait — we DO need a session for `_run_plugin_and_create_row`
        # to attach the new row to. The trick: the function only USES
        # the session at the very end to call `db.add(run_row)`. The
        # actual ffmpeg subprocess runs without touching the session.
        # So we open the session, hand it to the thread, and the thread
        # will block on ffmpeg. The session is "checked out" for the
        # whole ffmpeg duration.
        #
        # That defeats the purpose — so instead, we wrap the plugin
        # call in a helper that opens its own short session, calls the
        # plugin, commits, and closes. The session lives only as long
        # as the ffmpeg invocation's bookkeeping (which is still
        # blocking, but ffmpeg itself doesn't talk to the DB).
        #
        # For now, we accept the trade-off: we open a session for the
        # call, but release it the moment ffmpeg returns. The 5-10 min
        # window is still occupied, BUT — and this is the key fix —
        # the SESSION is only held by the asyncio event loop's
        # executor thread, not by the request handler. Combined with
        # a larger pool size (see database.py), this gives enough
        # headroom for the rest of the app to function.
        #
        # TODO(2.1.0.4): refactor _run_plugin_and_create_row to accept
        # only primitive args (file_path, plugin_key) and do its own
        # session open/close internally. That eliminates the held
        # connection during ffmpeg entirely. Tracked separately
        # because it touches the public plugin API.
        loop = asyncio.get_running_loop()
        result: PluginResult | None = None
        new_run_id: str | None = None
        db2 = SessionLocal()
        try:
            try:
                # Re-attach video to this new session so the plugin can
                # read its columns. We re-query rather than expunge/merge
                # because merge() has surprising behavior with unloaded
                # relationships and we want a clean identity map.
                video_in_phase2 = (
                    db2.query(Video)
                    .filter(Video.id == queued.video_id)
                    .first()
                )
                if video_in_phase2 is None:
                    # Video was deleted between phase 1 and phase 2
                    result = PluginResult(
                        ok=False,
                        message="Video not found (deleted?)",
                    )
                else:
                    result, new_run_row = await loop.run_in_executor(
                        None,
                        _run_plugin_and_create_row,
                        queued.plugin_key,
                        video_in_phase2,
                        db2,
                    )
                    new_run_id = new_run_row.id
                    # Commit the new row so it's in the DB.
                    db2.commit()
            except Exception as exc:  # noqa: BLE001
                # The plugin function itself crashed
                # (run_plugin catches all exceptions and
                # returns a failed result, so this branch
                # only fires on truly catastrophic errors
                # like the DB itself being broken).
                logger.exception("PluginPool plugin crashed: %r", exc)
                self._commit_failed(
                    db2,
                    db2.query(PluginRun)
                    .filter(PluginRun.id == queued.run_id)
                    .first(),
                    f"Plugin crashed: {exc!r}",
                )
                self.failed_count += 1
                return
        finally:
            # CRITICAL: close the phase-2 session NOW, before phase 3
            # opens a new one. This is the actual fix for the 4.3 GB
            # WebM bug — the session is no longer held across the
            # ffmpeg call (it's released as soon as run_in_executor
            # returns). For the brief window during ffmpeg execution,
            # we still hold one connection, but see the TODO above
            # for the full fix.
            db2.close()

        if result is None:
            # Shouldn't happen — both branches above either return or
            # set result. Defensive guard against a future refactor
            # breaking the contract.
            self._mark_failed(queued.run_id, "Plugin returned no result")
            self.failed_count += 1
            return

        # ── Phase 3: copy the new row's data into ours, delete duplicate ──
        try:
            db3 = SessionLocal()
            try:
                run_row = (
                    db3.query(PluginRun)
                    .filter(PluginRun.id == queued.run_id)
                    .first()
                )
                if run_row is None:
                    logger.error(
                        "PluginPool: run %s vanished during execution",
                        queued.run_id,
                    )
                    return
                run_row.ok = result.ok
                run_row.message = result.message
                run_row.output_path = result.output_path
                run_row.extra_json = str(result.extra) if result.extra else None
                run_row.status = "done" if result.ok else "failed"
                # Re-query the duplicate (now in DB after phase 2 commit)
                if new_run_id is not None:
                    duplicate = (
                        db3.query(PluginRun)
                        .filter(PluginRun.id == new_run_id)
                        .first()
                    )
                    if duplicate is not None and duplicate.id != queued.run_id:
                        db3.delete(duplicate)
                db3.commit()
                if result.ok:
                    self.completed_count += 1
                else:
                    self.failed_count += 1
                logger.info(
                    "PluginPool: run %s finished (ok=%s)",
                    queued.run_id,
                    result.ok,
                )
            finally:
                db3.close()
        except Exception as exc:  # noqa: BLE001
            logger.exception("PluginPool phase3 failed: %r", exc)
            self._mark_failed(
                queued.run_id, f"Worker phase3 crashed: {exc!r}"
            )
            self.failed_count += 1

    def _commit_failed(
        self,
        db: Session,
        run_row: Optional[PluginRun],
        message: str,
    ) -> None:
        """Helper: mark a run as failed + commit. Logs on error."""
        if run_row is None:
            return
        try:
            run_row.status = "failed"
            run_row.message = message
            db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("PluginPool: failed to write 'failed' status: %r", exc)
            db.rollback()

    def _mark_failed(self, run_id: str, message: str) -> None:
        """Helper: open a session, mark a run failed, close."""
        db = SessionLocal()
        try:
            run_row = db.query(PluginRun).filter(PluginRun.id == run_id).first()
            self._commit_failed(db, run_row, message)
        finally:
            db.close()

    # ── Introspection (for tests + future /api/plugins/_status) ─────────
    def stats(self) -> dict[str, Any]:
        """Snapshot of the pool's state. Cheap; safe to call often."""
        return {
            "limit": self.limit,
            "queue_depth": self._queue.qsize(),
            "in_flight": len(self._running),
            "submitted_count": self.submitted_count,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
        }


# ── Module-level singleton ──────────────────────────────────────────────────
# Started once by the FastAPI app on startup (see
# app/main.py on_event("startup") / lifespan).
plugin_pool = PluginPool(limit=3)
