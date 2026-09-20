"""
Gunicorn configuration for the Video Learning App (Day 6, production).

Loaded by `gunicorn -c gunicorn.conf.py app.main:app`.

Why each setting is here (and the alternatives considered):

workers = 4
  PRODUCTION MACHINE (verified 2026-09-16, audit doc decision #8):
  Mac Studio 2023 (Mac14,13), Apple M2 Max, 30-core GPU, 32GB RAM.
  Each worker holds ~300 MB; 4 workers ≈ 1.2 GB.
"""

# ── macOS fork-safety (2026-09-20 hardening — the worker SIGABRT crash-loop) ──
#
# WHAT HAPPENED: gunicorn's PRELOAD-LESS forking model (master forks
# workers) intermittently hits macOS's Objective-C fork-safety abort:
# "objc[pid]: +[NSMutableString initialize] may have been in progress
# in another thread when fork() was called... Crashing instead."
# Live impact (9/19 AND 9/20): a freshly-spawned worker dies with
# SIGABRT seconds after boot — including MID-REQUEST (a user upload
# POST got its 202, the worker aborted before the Video row committed,
# leaving an orphaned file and a "server error" in the UI).
#
# WHY: macOS 10.14+ deliberately crashes forked children that touch
# ObjC runtime state mid-initialization. The master process is a
# Python daemon that has loaded Foundation via various native deps
# (whisper's ctranslate2, CloudTelemetry, etc.) — when the master
# forks while ObjC initialization is racing a thread, the child
# aborts by design.
#
# THE FIX: this env var disables the abort for our workload. It is
# safe here because our forked children (gunicorn workers, whisper
# subprocess wrappers) immediately exec or re-init ObjC in their
# own single-threaded early lifetime — the classic hang risk this
# guard protects against (a multithreaded child calling ObjC before
# exec) does not apply: our children are ASGI servers + pure-Python
# workers that initialize their own threading deterministically.
# (Same mitigation Cloudflare/Python/Go daemons on macOS use.)
#
# Set BEFORE any import that could load ObjC-using native modules.
import os

os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

"""
The full original rationale docstring, preserved verbatim (the short
per-setting comments in the config sections below are derived from it):

workers = 4
  PRODUCTION MACHINE (verified 2026-09-16, audit doc decision #8):
  Mac Studio 2023 (Mac14,13), Apple M2 Max, 30-core GPU, 32GB RAM.
  Each worker holds ~300 MB; 4 workers ≈ 1.2 GB.

threads = 8
  8 threads per worker = 32 concurrent request slots total (was 2
  threads = 8 slots before this commit).

  Why the raise: the 9/12 outage analysis showed the 8-slot wall is
  the site-wide hang amplifier — a handful of long-held requests
  (LLM chat 5-30s, local-video FileResponse streaming, a slow upload)
  fills 8 slots and every subsequent request, including /api/health,
  queues forever. 32 slots raise the bar 4x for ~8 MB stack per
  thread (~64 MB per worker — nothing on 32GB).

  Why not 16+: threads don't fix the real per-request hogs (async LLM
  calls + nginx file-offload are the Phase-2 fixes); 32 slots covers
  the launch cohort (50-80 FREE browsing the zero-cost YouTube
  catalog + 10-15 PAID) with headroom. Threads also cost context
  switches on the 2-slot transcription box — 8 keeps the whisper
  threads a minority of runnable threads, which matters more than
  raw slot count when the GPU worker is saturating cores.

worker_class = "uvicorn.workers.UvicornWorker"
  Use uvicorn's ASGI worker (async). Gunicorn's default sync worker
  would block its entire thread pool during each LLM call.

bind = "0.0.0.0:8000"
  Listen on all interfaces so the Cloudflare Tunnel (Day 6c) can
  reach us. Don't change to 127.0.0.1 — the tunnel needs to connect
  to the public interface.

timeout = 60
  Kill workers that don't respond within 60s. Generous because
  (a) Groq calls take 1-2s, (b) YouTube caption downloads via
  yt-dlp can take 30-60s for slow videos, (c) SQLite writes are
  normally <100ms. If a worker times out, gunicorn restarts it
  automatically and the next request lands on a healthy worker.

graceful_timeout = 30
  On SIGTERM, give workers 30s to finish in-flight requests before
  force-killing. Pairs with the Cloudflare Tunnel's graceful-shutdown
  expectations (it stops sending new traffic within ~10s of SIGTERM).

keepalive = 5
  HTTP keep-alive idle timeout (seconds). 5s is the standard for
  browsers with HTTP/1.1. Higher = more memory per connection;
  lower = more TCP setup overhead.

max_requests = 1000
max_requests_jitter = 100
  After 1000 requests (with ±100 random jitter), recycle the worker.
  Defense against slow memory leaks in long-lived processes. The
  jitter prevents all workers from recycling simultaneously, which
  would create a brief capacity gap.

accesslog = "-"
errorlog = "-"
loglevel = "info"
  Logs to stderr → captured by start.sh's `tee` → goes to
  logs/server.log. One unified log stream, no separate access-log
  file to maintain.

access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(L)s'
  Standard "combined" log format (Apache/nginx style). Easy to grep,
  matches the format Cloudflare's own logs use, so we can correlate.

preload_app = True
  Load the app in the master process BEFORE forking workers. Pros:
  (a) faster worker startup (no per-worker import cost), (b) can
  catch import-time errors before forking. Cons: any code that
  creates per-process state at import time (e.g. temp file paths)
  will share that state across workers. Our app is fine with this
  — SQLAlchemy creates fresh engines per process, and our in-memory
  rate limiter + Ollama quota tracker are module-level singletons
  that we explicitly want to be per-worker (separate counters).

  If you ever add code that must NOT be shared across workers (e.g.
  a unique temp directory per process), set this to False.

proc_name = "video-learning-app"
  Process name shown in `ps`/`top`. Defaults to the module name
  ('app.main:app') which is ugly. This gives us a clean name for
  log filtering and ops dashboards.

Forwarded-allow-ips = "*"
  When the Cloudflare Tunnel (or any reverse proxy) connects, it
  sends X-Forwarded-For / X-Forwarded-Proto headers. We trust
  those headers so FastAPI's request.url.scheme is correct
  (returns "https" not "http"). Setting to "*" trusts any proxy;
  in production you'd restrict this to the tunnel's egress IPs,
  but for a single-tunnel setup this is fine.
"""

# ── Socket ───────────────────────────────────────────────────────────────
bind = "0.0.0.0:8000"
backlog = 2048  # default 2048; explicit so operators see the value

# ── Worker model ─────────────────────────────────────────────────────────
workers = 4
# 2026-09-16: 2 → 8 threads per worker (32 request slots total, was 8).
# See the docstring above (audit decisions #8/#12): the 8-slot wall was
# the 9/12 site-wide hang amplifier; 8 threads/worker costs ~64MB stacks
# per worker — trivial on the verified 32GB box.
threads = 8
worker_class = "uvicorn.workers.UvicornWorker"

# ── Timeouts ─────────────────────────────────────────────────────────────
timeout = 60          # hard kill: a worker that doesn't ack in 60s
graceful_timeout = 30 # on SIGTERM, give workers 30s to drain
keepalive = 5         # HTTP keep-alive idle

# ── Worker recycling (defense against slow leaks) ───────────────────────
max_requests = 1000
max_requests_jitter = 100

# ── Logging ──────────────────────────────────────────────────────────────
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = (
    '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s '
    '"%(f)s" "%(a)s" %(L)s'
)

# ── Misc ─────────────────────────────────────────────────────────────────
preload_app = True
proc_name = "video-learning-app"
forwarded_allow_ips = "*"
