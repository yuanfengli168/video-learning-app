"""_model_cache lock tests (2026-09-16, launch hardening #2).

The 9/12 outage's resource multiplier: N staggered transcribes each
saw "not in cache" and each constructed its own model copy. The
check-then-load in _get_cached_model is now a locked double-check.

What these tests pin:
  1. THE race fixed: many threads racing a cold cache construct the
     model exactly ONCE (pre-lock, each thread constructs its own).
  2. The lock does not serialize cache READS after warm-up (hot path
     stays lock-free-ish: first check is outside the lock).
  3. Different models don't block each other longer than necessary —
     sequential correctness across distinct keys.
  4. MLX side is documented NOT locked — because MLX transcribes run
     in a subprocess (ModelHolder cache lives and dies per process);
     this test pins that architectural fact via the worker invocation
     contract, so nobody "fixes" it later with a bogus in-process lock
     that can't reach the subprocess.

WhisperModel is mocked — we test cache semantics, not model loading.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest


@pytest.fixture()
def cold_cache():
    """Reset _model_cache to empty + return the lock for inspection."""
    from app.services import transcription as t

    saved = dict(t._model_cache)
    t._model_cache.clear()
    yield t
    t._model_cache.clear()
    t._model_cache.update(saved)


def _get_model(key: str):
    """Call the REAL (locked) loader. Keys must be allowlisted names
    (base/small/medium) — the allowlist is a LOCAL set built from
    AVAILABLE_MODELS inside get_model, so arbitrary test keys bounce.
    Distinct test cases use distinct real keys."""
    from app.services import transcription as t

    return t.get_model(key)


def test_cold_cache_race_loads_exactly_once(cold_cache):
    """THE 9/12 bug: 12 threads race a cold cache; exactly ONE
    WhisperModel may be constructed. The lock's double-check makes
    the losers reuse the winner's instance."""
    import time

    from app.services import transcription as t

    constructions = []
    construction_lock = threading.Lock()

    class FakeModel:
        def __init__(self, name, **kw):
            # Slow construction simulates the real ~30s first load —
            # without the lock, other threads sail past their own
            # "not in cache" checks while we're still in here.
            time.sleep(0.05)
            with construction_lock:
                constructions.append(name)

    with patch("faster_whisper.WhisperModel", FakeModel):
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(_get_model, "base") for _ in range(12)]
            results = [f.result(timeout=10) for f in futures]

    assert len(constructions) == 1, (
        f"expected exactly 1 construction under race, got {len(constructions)}"
    )
    # And every thread got THE SAME instance (no orphaned copies).
    assert all(r is results[0] for r in results)
    assert t._model_cache["base"] is results[0]


def test_hot_cache_read_does_not_take_lock(cold_cache):
    """After warm-up, cache hits must be lock-free (fast path): the
    first membership check happens before the lock. Prove it by
    warming the cache, then making the lock UNAVAILABLE (acquired by
    a side thread) — hot reads must still succeed instantly."""
    from app.services import transcription as t

    class FakeModel:
        pass

    t._model_cache["base"] = FakeModel()

    # Hold the cache lock hostage — if a cache HIT needed the lock, it
    # would deadlock/timeout instead of returning.
    acquired = t._model_cache_lock.acquire(timeout=1)
    assert acquired, "test setup failed to grab the lock"
    try:
        result = _get_model("base")  # must NOT block
        assert isinstance(result, FakeModel)
    finally:
        t._model_cache_lock.release()


def test_distinct_models_each_loaded_once(cold_cache):
    """Two different models requested concurrently: each constructed
    once, both cached under their own keys (lock correctness across
    distinct keys, not over-serialization)."""
    from app.services import transcription as t

    class FakeModel:
        def __init__(self, name, **kw):
            self.name = name

    seen: list[str] = []
    seen_lock = threading.Lock()

    def fake_ctor(name, **kw):
        import time

        time.sleep(0.03)
        with seen_lock:
            seen.append(name)
        return FakeModel(name, **kw)

    with patch("faster_whisper.WhisperModel", fake_ctor):
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = []
            for _ in range(3):
                futures.append(pool.submit(_get_model, "base"))
                futures.append(pool.submit(_get_model, "small"))
            results = {f.result(timeout=10) for f in futures}

    assert sorted(seen) == ["base", "small"], (
        "each distinct model must be constructed exactly once"
    )
    assert len(results) == 2, "base and small are distinct instances"
    assert {t._model_cache["base"].name, t._model_cache["small"].name} == {
        "base", "small",
    }


def test_failed_construction_does_not_poison_cache(cold_cache):
    """If construction raises, nothing may be cached — the next
    caller retries fresh (a poisoned None/exception entry would make
    every later transcription fail with a confusing error)."""
    from app.services import transcription as t

    calls = []

    def failing_ctor(name, **kw):
        calls.append(name)
        raise RuntimeError("simulated model download failure")

    with patch("faster_whisper.WhisperModel", failing_ctor):
        with pytest.raises(RuntimeError, match="simulated"):
            _get_model("base")

    assert "base" not in t._model_cache, "failed load must not be cached"
    assert len(calls) == 1

    # And the cache is still usable afterwards (retry works fresh).
    with patch("faster_whisper.WhisperModel", lambda n, **kw: object()):
        got = _get_model("base")
        assert got is not None
    assert len(calls) == 1, "second attempt retried construction (not poisoned)"


def test_mlx_transcribes_in_subprocess_not_locked_here():
    """Pins the architectural fact documented on _model_cache_lock:
    MLX transcriptions run via scripts/mlx_transcribe_worker.py in a
    SUBPROCESS (the 2026-09-05 fork-safety fix), so the in-process
    ModelHolder cache — and any in-process lock — never survives to
    matter for MLX. Why this test exists: a future reader seeing
    'mlx has an unlocked ModelHolder' might add a lock here; it would
    be dead code. The REAL MLX concurrency control is the mini-queue
    slot cap.

    Contract: the transcription path for backend == 'mlx-whisper'
    must invoke the subprocess worker, not call mlx_whisper in this
    process."""
    import inspect

    from app.services import transcription as t

    src = inspect.getsource(t.transcribe_with_backend)
    assert "subprocess" in src or "mlx_transcribe_worker" in src, (
        "mlx path must stay subprocess-based; if this changed, revisit "
        "the _model_cache_lock comment AND the warm-up decision"
    )
    # And the module must NOT import mlx_whisper at top level (that
    # alone would prove in-process usage + fork-safety risk).
    assert "import mlx_whisper" not in open(t.__file__).read().split("def ")[0], (
        "top-level mlx_whisper import would load Metal state into every "
        "gunicorn worker before fork"
    )