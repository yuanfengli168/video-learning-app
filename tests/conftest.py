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
    original_videos_session = videos_module.SessionLocal
    original_generation_session = generation_module.SessionLocal
    videos_module.SessionLocal = testing_local
    generation_module.SessionLocal = testing_local

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
        TestSessionLocal = None
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """Provide a FastAPI test client with the test database."""
    with TestClient(app) as c:
        yield c


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
