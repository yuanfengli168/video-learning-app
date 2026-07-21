"""Pytest configuration and fixtures."""

import os
import tempfile
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Set test environment BEFORE importing app modules
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("UPLOAD_DIR", tempfile.mkdtemp())
os.environ.setdefault("STORAGE_DIR", tempfile.mkdtemp())

import app.database as app_database  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

# Module-level TestSessionLocal — initialized by the db_session fixture.
# Workers (BackgroundTasks) that use SessionLocal() directly need this
# to talk to the same in-memory test DB that the test client uses.
TestSessionLocal: sessionmaker | None = None


@pytest.fixture(scope="function")
def db_session() -> Generator[Session, None, None]:
    """Provide a fresh in-memory SQLite database for each test.

    Also monkey-patches app.database.SessionLocal to point at the
    test DB, so any BackgroundTask worker (which can't use the
    request-scoped FastAPI get_db dependency) still writes to the
    same DB the test client is reading from.
    """
    global TestSessionLocal
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    TestSessionLocal = testing_local

    # Monkey-patch app.database.SessionLocal so background workers
    # that call `SessionLocal()` use the test DB.
    original_session_local = app_database.SessionLocal
    app_database.SessionLocal = testing_local
    # Also patch the imports inside the routers (they import the
    # name directly, so the patch on app.database doesn't reach them).
    import app.routers.videos as videos_module
    import app.routers.generation as generation_module
    import app.workers.plugin_pool as plugin_pool_module
    original_videos_session = videos_module.SessionLocal
    original_generation_session = generation_module.SessionLocal
    original_plugin_pool_session = plugin_pool_module.SessionLocal
    videos_module.SessionLocal = testing_local
    generation_module.SessionLocal = testing_local
    plugin_pool_module.SessionLocal = testing_local

    def override_get_db():
        try:
            yield testing_local()
        finally:
            pass  # don't close — managed by fixture

    app.dependency_overrides[get_db] = override_get_db

    session = testing_local()
    try:
        yield session
    finally:
        app.dependency_overrides.clear()
        # Restore the original SessionLocal references
        app_database.SessionLocal = original_session_local
        videos_module.SessionLocal = original_videos_session
        generation_module.SessionLocal = original_generation_session
        plugin_pool_module.SessionLocal = original_plugin_pool_session
        # MVP2.1.0.1: drain the plugin worker pool so
        # the next test starts with a clean queue. The
        # pool is a module-level singleton; its worker
        # task is bound to the TestClient's anyio event
        # loop, which closes when the TestClient exits.
        # The worker task is therefore "dead" (the loop
        # it was scheduled on is gone). Reset the
        # singleton so the next test's lifespan can
        # spawn a fresh worker on a fresh loop.
        from app.workers.plugin_pool import plugin_pool as _pool
        _pool._worker_task = None
        # Drain any leftover queue items.
        try:
            while True:
                _pool._queue.get_nowait()
        except Exception:
            pass
        _pool.submitted_count = 0
        _pool.completed_count = 0
        _pool.failed_count = 0
        TestSessionLocal = None
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """Provide a FastAPI test client with the test database.

    MVP2.0.6: set a valid `fb_token` cookie by default so the
    SessionExpiryMiddleware lets the request through. Before
    MVP2.0.6, the middleware let anonymous visits to
    /video/, /course/, /chat-history through (the user saw a
    phantom page), so tests didn't need a cookie. Now those
    routes redirect to /?session=expired without a cookie, so
    every test that exercises a protected SSR route needs a
    valid cookie. We set the cookie here at the fixture level
    so individual tests don't have to remember. Tests that
    specifically want to test the no-cookie case (see
    tests/test_session_expiry_middleware.py) can pass
    `cookies={}` to client.get() to override the default.

    The `verify_token` is mocked at every namespace where it's
    bound: `app.auth.firebase_admin` (the source), and
    `app.middleware_session` + `app.auth.dependencies` (the
    two importers that do `from app.auth.firebase_admin
    import verify_token`). Python's `from X import Y` binds
    the name in the importer's namespace at import time, so
    later patches to X.Y don't reach the importer. Patching
    only the firebase_admin namespace is not enough. A dummy
    cookie value like "test-token" is sufficient — the
    middleware will see it as "valid".

    MVP2.1.0.1: also enable `plugin_pool.synchronous_mode`
    so plugin-run tests don't have to poll for the worker
    to finish (the worker is bound to a closed TestClient
    event loop between tests, which makes cross-test
    pooling unreliable). The pool is reset to async mode
    in the fixture teardown so other tests aren't affected.
    """
    from app.auth.session import COOKIE_NAME
    from app.auth import firebase_admin as fa
    from app.auth import dependencies as auth_deps
    from app import middleware_session as ms
    from app.workers.plugin_pool import plugin_pool

    fake = lambda token: {"uid": "test-uid", "email": "test@test.com"}
    original_fa = fa.verify_token
    original_ms = ms.verify_token
    original_deps = auth_deps.verify_token
    fa.verify_token = fake
    ms.verify_token = fake
    auth_deps.verify_token = fake
    # MVP2.1.0.1: switch the plugin pool to sync mode
    # for this test only. Production code never sees
    # synchronous_mode = True.
    original_sync = plugin_pool.synchronous_mode
    plugin_pool.synchronous_mode = True
    try:
        with TestClient(app) as c:
            c.cookies.set(COOKIE_NAME, "test-token")
            yield c
    finally:
        fa.verify_token = original_fa
        ms.verify_token = original_ms
        auth_deps.verify_token = original_deps
        plugin_pool.synchronous_mode = original_sync


@pytest.fixture(autouse=True)
def no_auto_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Globally mock _run_auto_pipeline to a no-op for all tests.

    MVP2.0 #1: every video upload now queues an auto-pipeline background
    task (transcribe → generate). The TestClient runs BackgroundTasks
    synchronously, so without this fixture every test that uploads a
    video would also trigger Whisper + Ollama (or fail with an import
    error) and leave the video in 'error' state.

    Tests that specifically want to exercise auto-pipeline behavior
    should use:

        with patch("app.routers.videos._run_auto_pipeline") as mock_ap:
            ...

    to override this fixture for their scope.
    """
    monkeypatch.setattr(
        "app.routers.videos._run_auto_pipeline",
        lambda video_id, model_name="base": None,
    )

@pytest.fixture(autouse=True)
def clear_whisper_model_cache() -> None:
    """Clear app.services.transcription._model_cache between tests.

    MVP3.0 #2: the cache is a module-level dict that persists across
    tests in the same process. If test A loads a fake model into
    the cache (e.g. by patching faster_whisper.WhisperModel), test
    B would get the cached fake — silently passing the wrong
    behaviour. This fixture forces every test to start with an
    empty cache, matching the real-world behaviour where each
    fresh upload loads its model.

    Yields, then clears on teardown. Runs after the db_session
    fixture (so the DB is clean first), and is independent of
    client/db_session.
    """
    from app.services import transcription
    transcription._model_cache.clear()
    yield
    transcription._model_cache.clear()
