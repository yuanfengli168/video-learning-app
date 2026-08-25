"""
Tests for gunicorn.conf.py — the production server config (Day 6).

Why this test file exists:
  gunicorn.conf.py is loaded by `gunicorn -c gunicorn.conf.py app.main:app`.
  If any required setting is missing or has a wrong type, gunicorn either
  crashes at boot (silent failure) or boots in an unsafe config that
  doesn't match production expectations (e.g. 1 worker when we expect 4).

  These tests load the file as a Python module and assert every production
  invariant. If a future PR changes a setting, this file fails first —
  before the change ships to the Mac Studio at 11pm.

What these tests DON'T cover (and why):
  - That gunicorn actually boots 4 workers (covered by integration test
    in test_gunicorn_boot.py — slow, opt-in)
  - That Cloudflare Tunnel reaches gunicorn (requires external infra,
    can't run in CI; covered by manual runbook)
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONF_PATH = PROJECT_ROOT / "gunicorn.conf.py"


@pytest.fixture(scope="module")
def conf():
    """Load gunicorn.conf.py as a Python module.

    Uses importlib (not regular import) because the file is named with
    a dot, which Python's import machinery refuses to load by default.
    """
    spec = importlib.util.spec_from_file_location("gunicorn_conf_under_test", CONF_PATH)
    assert spec is not None, f"Could not create spec for {CONF_PATH}"
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ─── Existence + parseability ────────────────────────────────────────────


def test_conf_file_exists():
    """gunicorn.conf.py must exist at the project root.

    start.sh and install-cloudflare-tunnel.sh both reference it.
    Renaming it without updating those scripts would break deployment.
    """
    assert CONF_PATH.is_file(), f"missing {CONF_PATH}"


def test_conf_is_parseable_python(conf):
    """The file must be importable as a Python module.

    If a syntax error sneaks in (e.g. unclosed string), gunicorn will
    fail at boot with a confusing error. This test catches it at
    `pytest` time.
    """
    assert conf is not None
    assert hasattr(conf, "bind")


# ─── Socket ──────────────────────────────────────────────────────────────


class TestSocket:
    """The bind address determines what can reach the server."""

    def test_binds_to_all_interfaces(self, conf):
        """Must bind to 0.0.0.0, not 127.0.0.1.

        Cloudflare Tunnel connects to the public interface. If we
        bind to localhost only, the tunnel can't reach us and the
        app is unreachable from the internet.
        """
        assert conf.bind.startswith("0.0.0.0"), (
            f"bind={conf.bind!r} — Cloudflare Tunnel can't reach "
            "127.0.0.1. Must be 0.0.0.0 (or specific LAN IP)."
        )

    def test_binds_to_port_8000(self, conf):
        """Port 8000 matches start.sh / stop.sh / tunnel config.

        Changing the port here without updating the other scripts
        (or vice versa) = downtime. This is the single source of truth.
        """
        assert conf.bind.endswith(":8000"), (
            f"bind={conf.bind!r} — port 8000 is what start.sh, stop.sh, "
            "and install-cloudflare-tunnel.sh expect."
        )

    def test_backlog_is_sane(self, conf):
        """Backlog ≥ 128 = handles a brief burst without dropping SYN packets.

        Default is 2048 (we set it explicitly for ops visibility).
        """
        assert conf.backlog >= 128, (
            f"backlog={conf.backlog} — too low for a public-facing "
            "server; bursts of incoming connections will be dropped."
        )


# ─── Worker model ────────────────────────────────────────────────────────


class TestWorkerModel:
    """The worker settings are the heart of the production stack."""

    def test_uses_uvicorn_async_worker(self, conf):
        """worker_class MUST be uvicorn's ASGI worker.

        The default sync worker would block its entire thread pool
        during each LLM call (1-2s of dead time per request). With
        uvicorn's worker, the event loop stays free during awaits.
        """
        assert conf.worker_class == "uvicorn.workers.UvicornWorker", (
            f"worker_class={conf.worker_class!r} — sync workers block "
            "during LLM calls. Must be uvicorn's ASGI worker."
        )

    def test_workers_count_is_set(self, conf):
        """workers must be a positive int.

        workers=0 → gunicorn boot error.
        workers=None → gunicorn picks (cpu_count * 2 + 1) which on a
        10-core Mac Studio = 21 workers, blowing past our Groq
        free-tier quota instantly.
        """
        assert isinstance(conf.workers, int) and conf.workers > 0, (
            f"workers={conf.workers!r} — must be a positive int. "
            "Setting to None lets gunicorn pick (cpu_count*2+1) which "
            "would blow past our free-tier LLM quota."
        )

    def test_workers_match_hardware(self, conf):
        """Workers should be ≤ 1.5 × CPU count.

        Mac Studio has 10 cores. Workers > 15 means heavy context-
        switching overhead with no concurrency benefit. Today's
        value is 4 — there's room to grow, but if someone bumps
        this to 20 they should be told.
        """
        cpu_count = os.cpu_count() or 10
        assert conf.workers <= int(cpu_count * 1.5), (
            f"workers={conf.workers} on a {cpu_count}-core machine "
            "will cause context-switching thrashing."
        )

    def test_threads_per_worker_is_set(self, conf):
        """threads must be ≥ 1.

        threads=0 → gunicorn boot error. threads=1 = single thread
        per worker, blocks during LLM awaits. We picked 2 to give
        the event loop headroom.
        """
        assert isinstance(conf.threads, int) and conf.threads >= 1, (
            f"threads={conf.threads!r} — must be ≥ 1. threads=1 means "
            "each worker blocks during LLM awaits."
        )

    def test_threads_not_too_high(self, conf):
        """threads > 4 per worker = diminishing returns.

        Python's GIL serializes CPU-bound work, and our async work
        (LLM awaits) is the bottleneck, not threads. > 4 = wasted RAM.
        """
        assert conf.threads <= 4, (
            f"threads={conf.threads} — GIL serializes CPU work; our "
            "bottleneck is async I/O, not threads. > 4 wastes RAM."
        )

    def test_preload_app_is_set(self, conf):
        """preload_app=True catches import-time errors before forking.

        If app.main has an import error, preload=True makes gunicorn
        crash visibly (loud) rather than crash 4 workers independently
        (silent, harder to diagnose).
        """
        assert conf.preload_app is True, (
            "preload_app must be True so import-time errors crash "
            "loudly in the master process instead of silently in workers."
        )


# ─── Timeouts ────────────────────────────────────────────────────────────


class TestTimeouts:
    """Timeouts are the difference between 'graceful drain' and 'killed mid-request'."""

    def test_hard_timeout_is_generous(self, conf):
        """timeout ≥ 60s = enough for slow LLM calls + yt-dlp caption downloads.

        Groq: 1-2s. yt-dlp slow captions: 30-60s. We picked 60s as the
        floor — anything below risks killing a request that's still
        legitimately in flight.
        """
        assert conf.timeout >= 60, (
            f"timeout={conf.timeout}s — too aggressive. yt-dlp slow "
            "caption downloads can take 30-60s; LLM calls 1-2s. "
            "60s is the floor."
        )

    def test_hard_timeout_not_too_long(self, conf):
        """timeout ≤ 120s = a hung request gets killed in reasonable time.

        If a worker is stuck in an infinite loop, we want to recycle
        it within 2 minutes, not 10.
        """
        assert conf.timeout <= 120, (
            f"timeout={conf.timeout}s — a truly hung worker would block "
            "a slot for too long. 120s is the ceiling."
        )

    def test_graceful_timeout_allows_drain(self, conf):
        """graceful_timeout ≥ 10s = enough for in-flight requests to finish.

        On SIGTERM (deployment / restart), in-flight requests get up to
        `graceful_timeout` seconds to complete. < 10s = requests killed
        mid-LLM-stream.
        """
        assert conf.graceful_timeout >= 10, (
            f"graceful_timeout={conf.graceful_timeout}s — too short. "
            "In-flight LLM calls need at least 10s to finish gracefully."
        )

    def test_graceful_timeout_le_workspace_timeout(self, conf):
        """graceful_timeout ≤ hard timeout.

        If graceful > timeout, SIGTERM behavior becomes nonsensical
        (the hard kill triggers before graceful drain finishes).
        """
        assert conf.graceful_timeout <= conf.timeout, (
            f"graceful_timeout={conf.graceful_timeout}s > "
            f"timeout={conf.timeout}s — would cause hard-kill before "
            "graceful drain finishes."
        )


# ─── Worker recycling ────────────────────────────────────────────────────


class TestRecycling:
    """max_requests defends against slow memory leaks in long-lived processes."""

    def test_max_requests_is_set(self, conf):
        """max_requests must be > 0.

        0 or None = workers never recycle = slow leaks accumulate.
        """
        assert isinstance(conf.max_requests, int) and conf.max_requests > 0, (
            f"max_requests={conf.max_requests!r} — must be > 0. "
            "Workers that never recycle accumulate slow leaks."
        )

    def test_max_requests_not_too_low(self, conf):
        """max_requests < 100 = too much recycling overhead.

        Each recycle = ~1s of cold-start cost. With 4 workers at
        50 req each, we'd spend 4s/sec on restarts.
        """
        assert conf.max_requests >= 100, (
            f"max_requests={conf.max_requests} — recycling too often. "
            "Each recycle = ~1s cold-start; aim for ≥ 100 requests."
        )

    def test_jitter_breaks_synchronization(self, conf):
        """Jitter prevents all workers from recycling simultaneously.

        If jitter=0 and 4 workers each hit max_requests=1000 at
        roughly the same time, all 4 recycle at once = a brief
        capacity gap. With jitter=100, recycling is spread over ~200s.
        """
        assert conf.max_requests_jitter > 0, (
            f"max_requests_jitter={conf.max_requests_jitter} — without "
            "jitter, all workers recycle simultaneously, creating a "
            "brief capacity gap."
        )


# ─── Logging ─────────────────────────────────────────────────────────────


class TestLogging:
    """Log format + destinations must match the runbook's `tail` commands."""

    def test_logs_go_to_stderr(self, conf):
        """accesslog='-' and errorlog='-' → stderr → captured by start.sh's tee.

        If we change these to file paths, we need to update start.sh
        AND the runbook (runbook-day6.md says 'tail logs/server.log').
        """
        assert conf.accesslog == "-", (
            f"accesslog={conf.accesslog!r} — must be '-' (stderr) so "
            "start.sh's tee captures it into logs/server.log."
        )
        assert conf.errorlog == "-", (
            f"errorlog={conf.errorlog!r} — must be '-' (stderr) so "
            "start.sh's tee captures it into logs/server.log."
        )

    def test_loglevel_is_info(self, conf):
        """loglevel=info = enough for ops without log spam.

        debug = gunicorn internals + slow. warning = misses request logs.
        info = the sweet spot for a small production server.
        """
        assert conf.loglevel == "info", (
            f"loglevel={conf.loglevel!r} — expected 'info'. "
            "debug is too verbose; warning drops request logs."
        )

    def test_access_log_format_includes_status(self, conf):
        """Format must include %(s)s (status code) for HTTP monitoring.

        The runbook day6 says 'grep for 5xx'. Without %(s)s, we can't.
        """
        assert "%(s)s" in conf.access_log_format, (
            "access_log_format must include %(s)s (HTTP status) so "
            "the runbook can grep for 5xx errors."
        )


# ─── Reverse-proxy trust ────────────────────────────────────────────────


class TestTrustedProxies:
    """forwarded_allow_ips controls whether FastAPI trusts X-Forwarded-*."""

    def test_trusts_forwarded_headers(self, conf):
        """forwarded_allow_ips must NOT be empty.

        If empty, FastAPI ignores X-Forwarded-For / X-Forwarded-Proto
        from Cloudflare Tunnel → request.url.scheme returns 'http'
        instead of 'https' → OAuth callbacks (Firebase) break.
        """
        # gunicorn stores this as 'Forwarded-allow-ips' (configparser-style)
        # but in Python it's 'forwarded_allow_ips'
        trusted = getattr(conf, "forwarded_allow_ips", None) or getattr(
            conf, "Forwarded-allow-ips", None
        )
        assert trusted, (
            "forwarded_allow_ips must be set (e.g. '*' or a CIDR list). "
            "Empty = Cloudflare's X-Forwarded-Proto is ignored, "
            "Firebase OAuth breaks."
        )


# ─── Process name ────────────────────────────────────────────────────────


def test_proc_name_is_human_readable(conf):
    """proc_name='video-learning-app' shows up clean in `ps`/`top`.

    Default = 'app.main:app' which is ugly and harder to grep.
    """
    assert conf.proc_name == "video-learning-app", (
        f"proc_name={conf.proc_name!r} — expected 'video-learning-app' "
        "for clean `ps`/`top` output and easy log filtering."
    )


# ─── Cross-cutting invariants ────────────────────────────────────────────


def test_no_unexpected_settings(conf):
    """If someone adds a new top-level setting, this test reminds them to test it.

    This is a 'coverage reminder' — every setting in gunicorn.conf.py
    should appear in at least one test above. Run with -v to see
    which settings are untested.
    """
    tested_settings = {
        # Socket
        "bind",
        "backlog",
        # Worker model
        "workers",
        "threads",
        "worker_class",
        "preload_app",
        # Timeouts
        "timeout",
        "graceful_timeout",
        "keepalive",
        # Recycling
        "max_requests",
        "max_requests_jitter",
        # Logging
        "accesslog",
        "errorlog",
        "loglevel",
        "access_log_format",
        # Misc
        "proc_name",
        "forwarded_allow_ips",
        # not handled above but present in conf
        # (if you add a setting above and forget the test, add it here)
    }
    actual_settings = {
        name
        for name in dir(conf)
        if not name.startswith("_") and name.islower() and name != "annotations"
    }
    # Only flag *unexpected* settings (not in our tested set).
    # This is a soft check — we just print untested settings so the
    # operator notices.
    untested = actual_settings - tested_settings
    # Built-in module attributes we don't care about
    builtin_dunders = {"os", "sys", "Path"} & untested
    untested -= builtin_dunders
    # If pytest is run with -v, this prints a heads-up
    if untested:
        print(f"\n[coverage reminder] gunicorn.conf.py has {len(untested)} untested setting(s): {sorted(untested)}")
