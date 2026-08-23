#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# promote-admin.sh — Promote a user to admin (role=0)
# Usage:  bash scripts/promote-admin.sh you@gmail.com
#
# What it does (one command, four things):
#   1. Auto-detects DB path from .env (DATABASE_URL=sqlite:////Volumes/...)
#   2. Prints a full user table: who is admin / paid / free
#   3. If the email exists in users → promotes it to role=0 (ADMIN)
#   4. If NOT found → tells you to sign in first, then re-run
#
# Role reference (from app/auth/roles.py):
#   0 = ADMIN  — full access (curate catalog, manage users)
#   1 = PAID   — paid subscribers (see paid-only videos, paid chat)
#   2 = FREE   — free tier (public videos only, free chat)
#
# Why this script exists:
#   The app auto-creates new users with role=2 (FREE) on first login.
#   Admins need role=0. SQL is the only way to change it (no UI yet).
#   This wraps the SQL in a safe, idempotent command.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ok()    { echo -e "${GREEN}✅ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail()  { echo -e "${RED}❌ $1${NC}"; }
info()  { echo -e "${CYAN}ℹ️  $1${NC}"; }
hdr()   { echo -e "\n${BOLD}${CYAN}── $1 ──${NC}"; }

# ─── Locate project root (script lives in scripts/, project is one level up) ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ─── 1. Validate args ────────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
    fail "Usage:  bash scripts/promote-admin.sh you@gmail.com"
    info "No email given — promoting all admins instead."
    EMAIL=""
else
    EMAIL="$1"
fi

# ─── 2. Read DB path from .env (DATABASE_URL=sqlite:////Volumes/.../foo.db) ─
if [[ ! -f .env ]]; then
    fail ".env not found in $PROJECT_ROOT"
    exit 1
fi

DB_URL="$(grep '^DATABASE_URL=' .env | head -1 | cut -d= -f2- | tr -d '"')"
if [[ -z "$DB_URL" ]]; then
    fail "DATABASE_URL not set in .env"
    exit 1
fi

# Convert SQLAlchemy sqlite URL to filesystem path.
#
# SQLAlchemy URL format:
#   sqlite:////absolute/path   (4 slashes → absolute path on *nix)
#   sqlite:///relative/path    (3 slashes → relative path)
#
# Both forms begin with "sqlite:///" (10 chars). Strip that prefix:
#   sqlite:////abs/path  →  /abs/path   (correct, keeps leading /)
#   sqlite:///rel/path   →  rel/path    (correct, relative)
#
# No branching needed — the prefix "sqlite:///" works for both.
DB_PATH="${DB_URL#sqlite:///}"

if [[ ! -f "$DB_PATH" ]]; then
    fail "DB file not found: $DB_PATH"
    info "Has the app ever been started? Run: bash scripts/start.sh"
    exit 1
fi

hdr "📋 Current users"
info "DB: $DB_PATH"
echo ""

# Show user table — formatted with role names
sqlite3 -header -column "$DB_PATH" \
    "SELECT
        substr(user_id, 1, 24) AS user_id,
        email,
        CASE role
            WHEN 0 THEN 'ADMIN'
            WHEN 1 THEN 'PAID'
            WHEN 2 THEN 'FREE'
            ELSE 'UNKNOWN(' || role || ')'
        END AS role,
        datetime(created_at, 'localtime') AS created
     FROM users
     ORDER BY role ASC, created_at DESC;"

# Counts
ADMIN_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM users WHERE role=0;")
PAID_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM users WHERE role=1;")
FREE_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM users WHERE role=2;")
TOTAL=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM users;")

echo ""
info "Totals: $ADMIN_COUNT admin · $PAID_COUNT paid · $FREE_COUNT free · $TOTAL total"

# ─── 3. No email given → just listing, exit gracefully ───────────────────────
if [[ -z "$EMAIL" ]]; then
    exit 0
fi

hdr "🔍 Looking up: $EMAIL"

# Check if email exists
EXISTING_ROLE=$(sqlite3 "$DB_PATH" \
    "SELECT role FROM users WHERE email='$EMAIL' LIMIT 1;" 2>/dev/null || echo "")

if [[ -z "$EXISTING_ROLE" ]]; then
    fail "No user with email '$EMAIL' found in DB"
    echo ""
    info "Two possible reasons:"
    info "  1. You haven't signed into the app yet"
    info "     → Open http://localhost:8000 and sign in with this email"
    info "     → The app auto-creates a users row (role=FREE) on first login"
    info "  2. You signed in with a different email (typo, alias, etc.)"
    echo ""
    info "Available emails in DB:"
    sqlite3 "$DB_PATH" "SELECT '  - ' || email FROM users ORDER BY email;"
    echo ""
    info "Then re-run this script with the right email."
    exit 1
fi

# Found! Show current state
case "$EXISTING_ROLE" in
    0) CURRENT_NAME="ADMIN"  ;;
    1) CURRENT_NAME="PAID"   ;;
    2) CURRENT_NAME="FREE"   ;;
    *) CURRENT_NAME="UNKNOWN($EXISTING_ROLE)" ;;
esac
info "Found user — current role: $CURRENT_NAME"

if [[ "$EXISTING_ROLE" == "0" ]]; then
    ok "Already ADMIN — nothing to do"
    exit 0
fi

# ─── 4. Promote to admin ────────────────────────────────────────────────────
hdr "⬆️  Promoting to ADMIN"
info "Updating users SET role=0 WHERE email='$EMAIL' ..."

sqlite3 "$DB_PATH" "UPDATE users SET role=0 WHERE email='$EMAIL';"

# Verify
NEW_ROLE=$(sqlite3 "$DB_PATH" "SELECT role FROM users WHERE email='$EMAIL' LIMIT 1;")
if [[ "$NEW_ROLE" == "0" ]]; then
    ok "Promoted '$EMAIL' to ADMIN (role=0)"
else
    fail "Promotion failed — role is still $NEW_ROLE"
    exit 1
fi

hdr "📋 Updated user table"
sqlite3 -header -column "$DB_PATH" \
    "SELECT
        substr(user_id, 1, 24) AS user_id,
        email,
        CASE role
            WHEN 0 THEN 'ADMIN'
            WHEN 1 THEN 'PAID'
            WHEN 2 THEN 'FREE'
            ELSE 'UNKNOWN(' || role || ')'
        END AS role,
        datetime(created_at, 'localtime') AS created
     FROM users
     ORDER BY role ASC, created_at DESC;"

hdr "🎯 Next steps"
info "1. Refresh http://localhost:8000 in your browser"
info "2. The '📺 Admin Upload' link should now appear in the left sidebar"
info "3. If you're already signed in, you may need to sign out + back in"
info "   (or just refresh — the role lookup is cached for 60s)"
echo ""
ok "Done"
