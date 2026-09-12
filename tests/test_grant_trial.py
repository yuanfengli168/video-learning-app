"""Trial user management tests (2026-09-12, go-live prep).

scripts/grant_trial.sh manages the 20-user soft-launch cohort:
grant PAID for N days, revert to FREE at trial end, full audit trail
in users.notes. These tests exercise the script against a scratch DB.

The 2026-09-09 launch plan: 30 days full Pro, no card, flip back to
FREE at trial end; users keep their data.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "scripts" / "grant_trial.sh"


@pytest.fixture()
def scratch_db(tmp_path, monkeypatch):
    """A fresh users table + the script pointed at it via DATABASE_URL."""
    db = tmp_path / "trial_test.db"
    con = sqlite3.connect(db)
    con.execute(
        """
        CREATE TABLE users (
            user_id VARCHAR(128) PRIMARY KEY,
            email VARCHAR(254),
            role INTEGER NOT NULL DEFAULT 2,
            notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    con.commit()
    con.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    return db


def _run(*args, cwd=PROJECT_ROOT):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True, text=True, cwd=cwd, timeout=30,
    )


def _insert_user(db, email: str, role: int = 2, notes: str = ""):
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO users (user_id, email, role, notes) VALUES (?, ?, ?, ?)",
        (f"uid-{email}", email, role, notes),
    )
    con.commit()
    con.close()


def _get_user(db, email: str):
    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT role, notes FROM users WHERE email = ?", (email,)
    ).fetchone()
    con.close()
    return {"role": row[0], "notes": row[1] or ""}


def _cohort_file(tmp_path, emails):
    f = tmp_path / "cohort.txt"
    f.write_text("# soft launch cohort\n" + "\n".join(emails) + "\n")
    return f


# ── grant ────────────────────────────────────────────────────────────────


def test_grant_promotes_to_paid_with_audit_note(scratch_db):
    _insert_user(scratch_db, "friend@test.com")
    r = _run("grant", "friend@test.com")
    assert r.returncode == 0, r.stderr
    u = _get_user(scratch_db, "friend@test.com")
    assert u["role"] == 1
    assert "trial:granted=" in u["notes"]
    assert ";due=" in u["notes"]
    assert "cohort=soft-launch-20" in u["notes"]


def test_grant_refuses_admin_rows(scratch_db):
    _insert_user(scratch_db, "admin@test.com", role=0)
    r = _run("grant", "admin@test.com")
    assert r.returncode == 0
    assert "SKIP" in r.stdout
    assert "ADMIN" in r.stdout
    assert _get_user(scratch_db, "admin@test.com")["role"] == 0


def test_grant_unknown_email_fails_loud(scratch_db):
    r = _run("grant", "ghost@test.com")
    assert r.returncode != 0
    assert "no user row" in r.stdout + r.stderr
    assert "sign in once first" in r.stdout + r.stderr  # explains WHY


def test_grant_cohort_file(scratch_db, tmp_path):
    for i in range(3):
        _insert_user(scratch_db, f"u{i}@test.com")
    cohort = _cohort_file(tmp_path, [f"u{i}@test.com" for i in range(3)])
    r = _run("grant", "--all-cohort", str(cohort))
    assert r.returncode == 0, r.stderr
    for i in range(3):
        assert _get_user(scratch_db, f"u{i}@test.com")["role"] == 1


def test_cohort_file_ignores_comments_and_blanks(scratch_db, tmp_path):
    _insert_user(scratch_db, "real@test.com")
    cohort = tmp_path / "c.txt"
    cohort.write_text(
        "# comment line\n\n   \nreal@test.com\n# another comment\n"
    )
    r = _run("grant", "--all-cohort", str(cohort))
    assert r.returncode == 0
    assert _get_user(scratch_db, "real@test.com")["role"] == 1
    # the comments must NOT have been treated as emails (no failure)
    assert "no user row" not in r.stdout


# ── revert ───────────────────────────────────────────────────────────────


def test_revert_back_to_free_preserving_trail(scratch_db):
    _insert_user(scratch_db, "friend@test.com")
    _run("grant", "friend@test.com")
    r = _run("revert", "friend@test.com")
    assert r.returncode == 0, r.stderr
    u = _get_user(scratch_db, "friend@test.com")
    assert u["role"] == 2
    # Full lifecycle visible: granted → due → cohort → reverted
    assert "trial:granted=" in u["notes"]
    assert ";reverted=" in u["notes"]


def test_revert_protects_non_trial_users(scratch_db):
    """A real PAID subscriber (no trial note) must NOT be reverted —
    the revert only touches trial-cohort rows."""
    _insert_user(scratch_db, "realsub@test.com", role=1, notes="")
    r = _run("revert", "realsub@test.com")
    assert r.returncode == 0
    assert "SKIP" in r.stdout
    assert _get_user(scratch_db, "realsub@test.com")["role"] == 1


def test_revert_refuses_admin(scratch_db):
    _insert_user(scratch_db, "admin@test.com", role=0)
    r = _run("revert", "admin@test.com")
    assert r.returncode == 0
    assert "SKIP" in r.stdout
    assert _get_user(scratch_db, "admin@test.com")["role"] == 0


def test_revert_cohort_file(scratch_db, tmp_path):
    for i in range(3):
        _insert_user(scratch_db, f"u{i}@test.com")
    cohort = _cohort_file(tmp_path, [f"u{i}@test.com" for i in range(3)])
    _run("grant", "--all-cohort", str(cohort))
    r = _run("revert", "--all-cohort", str(cohort))
    assert r.returncode == 0, r.stderr
    for i in range(3):
        u = _get_user(scratch_db, f"u{i}@test.com")
        assert u["role"] == 2
        assert ";reverted=" in u["notes"]


# ── list / status ─────────────────────────────────────────────────────────


def test_list_shows_only_trial_rows(scratch_db):
    _insert_user(scratch_db, "trialed@test.com")
    _insert_user(scratch_db, "normal@test.com")
    _run("grant", "trialed@test.com")
    r = _run("list")
    assert r.returncode == 0
    assert "trialed@test.com" in r.stdout
    assert "normal@test.com" not in r.stdout
    assert "PAID" in r.stdout


def test_status_single_user(scratch_db):
    _insert_user(scratch_db, "friend@test.com")
    r = _run("status", "friend@test.com")
    assert r.returncode == 0
    assert "friend@test.com" in r.stdout


def test_usage_without_args(scratch_db):
    """No args → usage text, exit 0. (Needs a valid DATABASE_URL —
    the script's DB check runs before command dispatch.)"""
    r = _run()
    assert r.returncode == 0
    assert "grant" in r.stdout
    assert "revert" in r.stdout
    assert "--all-cohort" in r.stdout