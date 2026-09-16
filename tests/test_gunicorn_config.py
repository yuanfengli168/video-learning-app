"""Gunicorn config contract tests (2026-09-16, launch hardening #3).

gunicorn.conf.py was rewritten for the VERIFIED production machine
(Mac Studio 2023, M2 Max, 30-core GPU, 32GB RAM — audit decision #8).
This file had been carrying a "64 GB / 10-core" capacity story for a
machine that never existed (the 9/12 audit confused the dev MacBook
with the prod Studio).

The config is plain Python imported by gunicorn (-c gunicorn.conf.py),
so these tests import it directly and pin the deployment contract:

  1. The 32GB-derived worker/thread budget: 4 workers x 8 threads =
     32 request slots (the 9/12 hang showed the old 8-slot wall was
     the site-wide amplifier; audit decisions #8/#12).
  2. The comment block must describe the REAL machine (guard against
     the phantom-spec class of bug recurring: if someone rewrites the
     budget for wrong hardware, the test reads the comment's own
     claims against the verified spec).
  3. The comment must NOT resurrect the phantom "64 GB" figure.
  4. Stability settings from the 9/12 lesson remain: timeout stays
     60s (gunicorn restarts hung workers), graceful_timeout >0.
  5. max_requests recycling stays enabled (leak defense) with jitter
     (no simultaneous-recycle capacity gap).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture()
def conf():
    """Import gunicorn.conf.py as a module (it's not a package file —
    gunicorn -c loads it raw, so we load it the same way)."""
    path = Path(__file__).resolve().parents[1] / "gunicorn.conf.py"
    spec = importlib.util.spec_from_file_location("gunicorn_conf", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # noqa: SLF001 — stdlib loader
    return module


def test_worker_thread_budget(conf):
    """4 workers x 8 threads = 32 slots. The budget derived from the
    VERIFIED 32GB spec (audit #12): each worker ~300MB, threads ~8MB
    stack — total well under the machine's headroom after macOS,
    the MLX model copy, and 2 transcribe slots."""
    assert conf.workers == 4
    assert conf.threads == 8
    assert conf.workers * conf.threads == 32


def test_worker_class_is_async(conf):
    """Uvicorn ASGI worker — the sync worker would block its thread
    pool on every LLM call (the 9/12 starvation shape)."""
    assert conf.worker_class == "uvicorn.workers.UvicornWorker"


def test_comment_describes_verified_machine(conf):
    """The config's capacity docstring must carry the VERIFIED spec —
    Mac Studio / M2 Max / 30-core GPU / 32GB — and must not resurrect
    the phantom '64 GB' machine as a live claim (the 9/12 audit's
    machine-fact error: comments asserted a machine that never
    existed, so every capacity decision built on them was fiction).
    Quoting the phantom inside a NOTE about the correction is allowed."""
    src = (Path(__file__).resolve().parents[1] / "gunicorn.conf.py").read_text()

    for needle in ("Mac Studio", "M2 Max", "30-core GPU", "32GB"):
        assert needle in src, (
            f"config docstring must mention verified spec element {needle!r}"
        )
    # The phantom figures must not appear OUTSIDE an explicit
    # 'previously described' correction note. Strip NOTE lines first.
    non_note_lines = [
        ln for ln in src.splitlines()
        if not (
            "NOTE: this comment block previously" in ln
            or ln.strip().startswith("cores / 64 GB RAM")
        )
    ]
    non_note = "\n".join(non_note_lines)
    assert "64 GB" not in non_note, (
        "phantom '64 GB' spec is back as a live claim — that machine "
        "does not exist (see audit doc decision #8)"
    )
    assert "10 CPU cores" not in non_note, (
        "phantom '10 CPU cores' figure is back as a live claim"
    )


def test_hang_recovery_settings(conf):
    """The 9/12 lesson in settings form: a wedged worker must get
    restarted (timeout), and a restart must not drop in-flight
    requests on the floor (graceful_timeout). These existed before;
    the rewrite must not lose them."""
    assert conf.timeout == 60
    assert conf.graceful_timeout == 30


def test_worker_recycling_stays_on(conf):
    """max_requests + jitter = leak defense without a capacity gap
    (all workers recycling at once would briefly serve nothing —
    worse than a leak on launch day)."""
    assert conf.max_requests == 1000
    assert conf.max_requests_jitter == 100


def test_bind_all_interfaces(conf):
    """0.0.0.0 — the Cloudflare Tunnel must reach gunicorn on the
    public interface (Day 6c contract; 127.0.0.1 would break the
    tunnel)."""
    assert conf.bind == "0.0.0.0:8000"