"""
Gunicorn configuration for the Video Learning App (Day 6, production).

Loaded by `gunicorn -c gunicorn.conf.py app.main:app`.

Why each setting is here (and the alternatives considered):

workers = 4
  Mac Studio has 10 CPU cores / 64 GB RAM. Each worker holds ~300 MB.
  4 workers is comfortable (1.2 GB total) and gives us enough
  headroom for Ollama (which uses the GPU/ANE), LiteLLM in-flight
  requests, and SQLite writes. 4 workers × 2 threads = 8 concurrent
  requests at once — plenty for soft-launch scale (10-20 users).

  Why not more? More workers = more memory + more LLM-call concurrency,
  which we don't need. Day 4's free-tier budget (Groq 250 req/day)
  is the real constraint, not CPU.

threads = 2
  2 threads per worker. Our LLM calls (Groq) block for 1-2s waiting
  for the API. With 2 threads per worker, the event loop can switch
  between requests in the same worker while one is waiting for the
  LLM. We don't go higher because (a) more threads = more context
  switching overhead, and (b) the per-day rate limit is the real
  constraint, not per-request concurrency.

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
threads = 2
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
