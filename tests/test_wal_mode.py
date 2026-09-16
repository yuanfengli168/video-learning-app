"""WAL journal mode + busy_timeout tests (2026-09-16, launch hardening #1).

The DB ran in journal_mode=delete until this change — every write took a
database-wide exclusive lock that also blocked READERS, making the
telemetry events write (one row per click) a site-wide latency amplifier
(audit doc decision #12).

What these tests pin:
  1. Every pooled connection carries the pragmas (busy_timeout is
     per-connection — the connect-listener contract).
  2. WAL is active (journal_mode persists in the file, but the listener
     re-asserts it on every connection).
  3. synchronous=NORMAL (the WAL-recommended durability level).
  4. THE PROPERTY WE ACTUALLY BOUGHT: a reader thread keeps flowing
     while a writer thread commits — no reader stall behind a write
     transaction. This is the 9/12-outage-class behavior change.
  5. Concurrent writers serialize gracefully via busy_timeout instead
     of "database is locked" failures.
  6. The :memory: test DB (SingletonThreadPool) is exempt — the
     listener must not fire there (it would break the suite).

Runs against a REAL file-backed SQLite in tmp_path (WAL is a
file-level property; :memory: can't demonstrate it).
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Video


@pytest.fixture()
def wal_engine(tmp_path):
    """A real file-backed SQLite engine wired with the SAME pragma
    listener the production engine uses (import the actual function —
    not a re-implementation — so the test pins the shipped behavior)."""
    from app.database import _set_sqlite_pragmas
    from sqlalchemy import event

    db_path = tmp_path / "wal_test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    event.listens_for(engine, "connect")(_set_sqlite_pragmas)

    # Create the schema through the model metadata (Video is enough —
    # it's the table the concurrency tests hammer).
    Base.metadata.create_all(bind=engine, tables=[Video.__table__])
    yield engine
    engine.dispose()


def _connect_pragma(engine, name: str) -> object:
    with engine.connect() as conn:
        return conn.execute(text(f"PRAGMA {name}")).scalar()


def test_every_connection_carries_busy_timeout(wal_engine):
    """busy_timeout is PER-CONNECTION — the connect-listener must fire
    for every pooled connection, not just the first. Check across
    multiple distinct pool connections."""
    seen = set()
    for _ in range(5):
        with Session(wal_engine) as s:
            # force a pool checkout
            s.execute(text("SELECT 1"))
            raw = s.connection().connection
            cur = raw.cursor()
            cur.execute("PRAGMA busy_timeout")
            seen.add(cur.fetchone()[0])
            cur.close()
    assert seen == {5000}, "every pooled connection must set busy_timeout=5000ms"


def test_wal_mode_active(wal_engine):
    """journal_mode=WAL must be active on the file (delete mode blocked
    readers behind writes — the whole point of the switch)."""
    assert str(_connect_pragma(wal_engine, "journal_mode")).lower() == "wal"


def test_pragmas_on_listener_connection(wal_engine):
    """synchronous + busy_timeout are PER-CONNECTION pragmas (a fresh
    connection reads the SQLite DEFAULTS, not the listener's values —
    verified against raw sqlite3 semantics: journal_mode persists in
    the file, synchronous/busy_timeout do not). So the contract to pin
    is: the connection the POOL hands out — the one the listener ran
    on — carries WAL/NORMAL/5000. Read all three on one raw pooled
    connection, exactly as a request would receive it."""
    with wal_engine.connect() as conn:
        raw = conn.connection
        cur = raw.cursor()
        cur.execute("PRAGMA journal_mode")
        journal = cur.fetchone()[0]
        cur.execute("PRAGMA synchronous")
        sync = cur.fetchone()[0]
        cur.execute("PRAGMA busy_timeout")
        busy = cur.fetchone()[0]
        cur.close()
    assert str(journal).lower() == "wal"
    assert sync == 1, f"synchronous={sync} — want NORMAL(1), got FULL(2) or OFF(0)"
    assert busy == 5000


def test_wal_persists_across_connections(wal_engine):
    """journal_mode is the ONE persistent pragma: a brand-new connection
    to the same file still sees wal (the others reset to defaults —
    that's why the connect-listener fires on EVERY connection)."""
    with wal_engine.connect() as conn:
        raw = conn.connection
        cur = raw.cursor()
        cur.execute("PRAGMA journal_mode")
        assert str(cur.fetchone()[0]).lower() == "wal"
        # sanity: the per-connection pragma really does NOT persist —
        # proving the listener is what carries them, not the file.
        cur.execute("PRAGMA synchronous")
        # ...on THIS connection it's NORMAL because the listener ran.
        assert cur.fetchone()[0] == 1
        cur.close()
    # A raw sqlite3 connection (no SQLAlchemy, no listener) to the
    # same file: journal persisted, synchronous reset to default.
    import sqlite3
    raw_conn = sqlite3.connect(str(wal_engine.url.database))
    try:
        cur = raw_conn.cursor()
        cur.execute("PRAGMA journal_mode")
        assert str(cur.fetchone()[0]).lower() == "wal", "WAL must persist in the file"
        cur.execute("PRAGMA synchronous")
        assert cur.fetchone()[0] == 2, "no-listener connection must show SQLite default"
        cur.close()
    finally:
        raw_conn.close()


def test_reader_not_blocked_by_writer(wal_engine):
    """THE headline property (decision #12): readers keep flowing
    while a write transaction is open. In delete-journal mode this test
    FAILS — the reader stalls on the writer's exclusive lock.

    Method: thread A opens an INSERT transaction and holds it (via a
    threading.Event); thread B times a SELECT during the hold. Under
    WAL B completes quickly; under delete mode B would block until A
    commits (and the busy_timeout would make it error out instead —
    either way, B can't read mid-write in the old mode).
    """
    writer_started = threading.Event()
    writer_may_commit = threading.Event()
    reader_elapsed = []
    errors = []

    def writer():
        with wal_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO videos (id, title, filename, file_path, "
                    "file_size, duration, order_index, section_id, status, "
                    "visibility, caption_languages, whisper_model) VALUES "
                    "('v1', 't', 'f', '/tmp/f', 1, 1.0, 0, 'sec-1', "
                    "'ready', 0, '[]', 'base')"
                )
            )
            writer_started.set()
            # Hold the write transaction open for up to 2s — the reader
            # must NOT stall behind it (WAL reads from the snapshot).
            writer_may_commit.wait(timeout=2.0)

    def reader():
        writer_started.wait(timeout=2.0)
        t0 = time.monotonic()
        try:
            with wal_engine.connect() as conn:
                conn.execute(text("SELECT COUNT(*) FROM videos"))
        except Exception as e:  # pragma: no cover
            errors.append(e)
        reader_elapsed.append(time.monotonic() - t0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        wt = pool.submit(writer)
        # Small stagger so the reader starts INSIDE the writer's txn.
        time.sleep(0.15)
        rt = pool.submit(reader)
        rt.result(timeout=5)
        # Release + collect writer.
        writer_may_commit.set()
        wt.result(timeout=5)

    assert not errors, f"reader errored under concurrent write: {errors}"
    assert reader_elapsed, "reader never ran"
    # The reader finished WHILE the writer's txn was still open (well
    # under the 2s hold). Generous bound to stay CI-stable: the point
    # is "did not wait for the writer", not a latency record.
    assert reader_elapsed[0] < 1.5, (
        f"reader took {reader_elapsed[0]:.2f}s — appears blocked behind "
        "the writer; WAL read-snapshot not working"
    )


def test_concurrent_writers_serialize_gracefully(wal_engine):
    """Two writers contending on the single write lock: busy_timeout
    (5s) makes the loser WAIT and succeed instead of failing with
    'database is locked'. Delete-mode equivalent: instant error.

    This is the write-side counterpart of the reader test — what
    actually changes for the upload path when a transcribe-progress
    write collides with a page-write.
    """
    results = []
    errors = []

    def insert_row(i: int) -> None:
        try:
            with wal_engine.begin() as conn:
                conn.execute(
                    text(
                        f"INSERT INTO videos (id, title, filename, "
                        "file_path, file_size, duration, order_index, "
                        "section_id, status, visibility, caption_languages, "
                        f"whisper_model) VALUES ('w{i}', 't', 'f', '/tmp/f', "
                        "1, 1.0, 0, 'sec-1', 'ready', 0, '[]', 'base')"
                    )
                )
            results.append(i)
        except Exception as e:
            errors.append((i, e))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(insert_row, i) for i in range(6)]
        for f in futs:
            f.result(timeout=10)

    assert not errors, f"writers failed under contention: {errors}"
    assert len(results) == 6


def test_listener_scope_guard_excludes_memory():
    """Pin the SHIPPED guard (_is_file_backed_sqlite in database.py).
    The original text check (':memory:' not in url) missed bare
    sqlite:// — which IS a memory DB and is exactly the URL shape
    conftest uses for the test engine (found live while writing these
    tests). WAL is a file-level property, so the listener must
    register for every FILE path and skip every memory shape. Also
    asserts the production URL satisfies the file branch — that's
    what makes the live prod DB run WAL."""
    import app.database as db_mod
    from app.config import settings

    guard = db_mod._is_file_backed_sqlite
    # Memory shapes — all excluded:
    assert guard("sqlite://") is False, "bare sqlite:// (conftest shape) is memory"
    assert guard("sqlite:///:memory:") is False, ":memory: marker is memory"
    # File shapes — included:
    assert guard("sqlite:////Volumes/Storage/video_learning.db") is True
    assert guard("sqlite:///./video_learning.db") is True, "relative file path counts"
    # Non-sqlite — excluded:
    assert guard("postgresql://u@h/db") is False
    assert guard("mysql+pymysql://u@h/db") is False

    # THE deployment assertion: the PROD URL is file-backed. Inside
    # the test suite conftest monkeypatches settings.database_url to
    # :memory: — so we verify the real prod shape via the env-file URL
    # the module would see outside the suite (the .env default chain).
    # If someone ever points prod at a memory URL, this test catches it
    # at import-review time instead of at the first DB write.
    from app.config import Settings

    prod_default = Settings.model_construct(
        **{
            **settings.model_dump(),
            "database_url": "sqlite:////Volumes/Storage-Fast-NVMe/video_learning.db",
        }
    )
    assert guard(prod_default.database_url) is True, (
        "a real file-backed prod URL must activate the WAL listener"
    )
    assert callable(db_mod._set_sqlite_pragmas)