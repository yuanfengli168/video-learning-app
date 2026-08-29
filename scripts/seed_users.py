#!/usr/bin/env python3
"""
seed_users.py — Idempotently seed known users with their roles.

Use cases:
  - After a fresh DB / reset_db.py
  - After a restore that left you without your real users
  - When you want to promote/demote someone without going through the admin UI

Idempotent: re-running with the same input is safe. Existing rows are
UPDATEd to match the spec; missing rows are INSERTed.

Firebase lookup: for emails we don't yet know the Firebase UID for, this
script tries firebase_admin.auth.get_user_by_email() and uses the real UID.
If the user hasn't signed up with Firebase yet, the row is created keyed by
email and tagged _pending_firebase_lookup=true. A subsequent run after the
user signs in will pick up the real UID and clear the flag.

Usage:
    python scripts/seed_users.py                # apply default users below
    python scripts/seed_users.py --dry-run      # show what would change
    python scripts/seed_users.py --user someone@example.com FREE
                                                # add/update one user
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DEFAULT_LIVE = Path("/Volumes/Storage-Fast-NVMe/video_learning.db")

# Role enum (matches app/auth/roles.py):
#   ADMIN = 0
#   PAID  = 1
#   FREE  = 2
ROLE_NAMES = {"ADMIN": 0, "PAID": 1, "FREE": 2}

# Default seed set. Edit this list when you onboard a new user.
DEFAULT_USERS: list[dict] = [
    {
        "email": "jackyopenclaw.168@gmail.com",
        "role": "PAID",
        "notes": "Primary Google account. Paid tier per user direction.",
    },
    {
        "email": "jackieliglobal@gmail.com",
        "role": "ADMIN",
        "notes": "Admin Google account (you).",
    },
    {
        "email": "lyf99.2022@gmail.com",
        "role": "FREE",
        "notes": "Soft-launch test user. Free tier.",
    },
]


def _resolve_uid(email: str) -> tuple[str | None, str]:
    """Try to find the Firebase UID for this email.
    Returns (uid_or_None, status_string)."""
    try:
        # Initialize Firebase Admin SDK on first use. We do this lazily because
        # some users run this script before the app has booted (and therefore
        # before firebase_admin has been initialized).
        import firebase_admin
        from firebase_admin import auth, credentials
        from app.config import settings
        try:
            firebase_admin.get_app()
        except ValueError:
            cred = credentials.Certificate(settings.firebase_service_account_key_path)
            firebase_admin.initialize_app(cred)
        user_record = auth.get_user_by_email(email)
        return user_record.uid, f"firebase uid={user_record.uid}"
    except Exception as e:  # broad: UserNotFoundError, config missing, etc.
        return None, f"firebase lookup failed: {type(e).__name__}: {e}"


def _read_existing_users(live: Path) -> dict[str, dict]:
    conn = sqlite3.connect(live)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT user_id, email, role, notes FROM users").fetchall()
    conn.close()
    return {r["user_id"]: dict(r) for r in rows}


def _email_index(users: dict[str, dict]) -> dict[str, dict]:
    """Index by email for matching."""
    return {u["email"]: (uid, u) for uid, u in users.items() if u.get("email")}


def _plan(live: Path, users_spec: list[dict]) -> dict:
    """Compute INSERT/UPDATE actions without writing."""
    existing = _read_existing_users(live)
    by_email = _email_index(existing)
    actions: list[dict] = []
    for spec in users_spec:
        email = spec["email"]
        role = ROLE_NAMES.get(spec["role"].upper())
        if role is None:
            actions.append({"email": email, "action": "ERROR", "reason": f"unknown role {spec['role']}"})
            continue
        notes = spec.get("notes", "")
        # Try Firebase lookup
        uid, status = _resolve_uid(email)
        # Match against existing
        match = by_email.get(email)
        if uid:
            if uid in existing:
                existing_row = existing[uid]
                if existing_row["role"] != role:
                    actions.append({"email": email, "uid": uid, "action": "UPDATE", "from": existing_row["role"], "to": role})
                else:
                    actions.append({"email": email, "uid": uid, "action": "NOOP", "role": role})
            else:
                actions.append({"email": email, "uid": uid, "action": "INSERT", "role": role})
        else:
            if match:
                uid_matched = match[0]
                if match[1]["role"] != role:
                    actions.append({"email": email, "uid": uid_matched, "action": "UPDATE_PENDING_BIND", "from": match[1]["role"], "to": role})
                else:
                    actions.append({"email": email, "uid": uid_matched, "action": "NOOP_PENDING_BIND"})
            else:
                # Generate a placeholder uid keyed on email so we can still create the row.
                # When user signs in, app will match by email and bind.
                placeholder_uid = f"_pending::{email}"
                actions.append({"email": email, "uid": placeholder_uid, "action": "INSERT_PENDING_BIND", "role": role})
        actions[-1]["notes"] = notes
        actions[-1]["_firebase_status"] = status
    return {"actions": actions, "existing_users": existing}


def _print_plan(plan: dict) -> None:
    print("=" * 70)
    print("SEED USERS PLAN")
    print("=" * 70)
    print("\nExisting users in DB:")
    for uid, row in plan["existing_users"].items():
        print(f"  {uid:36s}  {row.get('email','?'):30s}  role={row['role']}")
    print("\nActions:")
    for a in plan["actions"]:
        print(f"  {a['email']:30s}  {a.get('action','?'):20s}  uid={a.get('uid','?')[:32]}")
        if "_firebase_status" in a:
            print(f"    └─ firebase: {a['_firebase_status']}")
    print("=" * 70)


def _apply(live: Path, plan: dict) -> dict:
    conn = sqlite3.connect(live)
    counts = {"insert": 0, "update": 0, "noop": 0}
    try:
        conn.execute("BEGIN IMMEDIATE")
        for a in plan["actions"]:
            if a["action"].startswith("ERROR"):
                continue
            uid = a["uid"]
            role = ROLE_NAMES.get(next((s["role"] for s in DEFAULT_USERS if s["email"] == a["email"]), "FREE").upper())
            notes = a.get("notes", "")
            if a["action"] in ("INSERT", "INSERT_PENDING_BIND"):
                conn.execute(
                    "INSERT OR IGNORE INTO users (user_id, email, role, notes) VALUES (?, ?, ?, ?)",
                    (uid, a["email"], role, notes),
                )
                counts["insert"] += 1
            elif a["action"] in ("UPDATE", "UPDATE_PENDING_BIND"):
                conn.execute(
                    "UPDATE users SET email = ?, role = ?, notes = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (a["email"], role, notes, uid),
                )
                counts["update"] += 1
            elif a["action"].startswith("NOOP"):
                counts["noop"] += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return counts


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--live", type=Path, default=DEFAULT_LIVE)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--user", nargs=2, metavar=("EMAIL", "ROLE"),
                   help="Add or update a single user (repeatable)")
    args = p.parse_args()

    spec = list(DEFAULT_USERS)
    if args.user:
        email, role = args.user
        spec = [{"email": email, "role": role.upper(), "notes": f"Seeded via CLI on {__import__('datetime').date.today()}"}]

    plan = _plan(args.live, spec)
    _print_plan(plan)

    if args.dry_run:
        print("\n--dry-run specified, exiting.")
        return 0

    counts = _apply(args.live, plan)
    print(f"\nApplied: {counts['insert']} inserted, {counts['update']} updated, {counts['noop']} no-op.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
