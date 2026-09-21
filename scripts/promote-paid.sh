#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# promote-paid.sh — the flip-kit: set a user's tier + per-user limit overrides
# Usage:
#   bash scripts/promote-paid.sh their@gmail.com --role paid
#   bash scripts/promote-paid.sh their@gmail.com --file-limit 2GB
#   bash scripts/promote-paid.sh their@gmail.com --file-limit 2GB --quota 50GB
#   bash scripts/promote-paid.sh their@gmail.com --reset-limits
#   bash scripts/promote-paid.sh            (no args → list users + exit)
#
# What it does:
#   1. Auto-detects DB path from .env (DATABASE_URL, promote-admin.sh pattern)
#   2. Prints the user table with CURRENT overrides (GB-formatted)
#   3. --role paid|free|admin    → sets the role (same as promote-admin.sh,
#                                  but from one script during beta ops)
#   4. --file-limit 2GB / --quota 50GB
#                                → sets users.max_file_bytes /
#                                  users.storage_quota_bytes (raw bytes).
#                                  GB/MB suffixed input; the script converts.
#   5. --reset-limits            → both overrides back to NULL (= tier
#                                  default; the clean rollback)
#   6. EVERY change is written to the events table (source='admin.flip')
#      → the audit trail from doc/flip-kit.md becomes automatic
#
# The model (doc/limits-registry.md + doc/flip-kit.md):
#   effective_limit(user) = user override (if set) → else tier default (env)
#   NULL override = tier default. Changes take effect on the next request —
#   NO restart needed (the resolver reads the row per-request).
#
# Safety:
#   - One sqlite command in, out — WAL-safe on the live DB
#   - User must exist (sign-in-first flow, same as promote-admin.sh)
#   - Never INSERTs users rows
#   - Refuses to LOWER a limit without --force (flip up, never silently down)
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ─── 1. Parse args ───────────────────────────────────────────────────────────
EMAIL="${1:-}"
EMAIL="${EMAIL#--}"   # tolerate a leading -- if someone pastes oddly
shift $(( $# > 0 ? 1 : 0 )) || true

ROLE=""
FILE_LIMIT=""
QUOTA=""
RESET_LIMITS=0
FORCE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --role)
            ROLE="${2:-}"
            [[ "$ROLE" =~ ^(paid|free|admin)$ ]] || { fail "--role must be paid|free|admin"; exit 1; }
            shift 2 ;;
        --file-limit)
            FILE_LIMIT="${2:-}"
            shift 2 ;;
        --quota)
            QUOTA="${2:-}"
            shift 2 ;;
        --reset-limits)
            RESET_LIMITS=1
            shift ;;
        --force)
            FORCE=1
            shift ;;
        *)
            fail "Unknown option: $1"
            echo "Usage: bash scripts/promote-paid.sh <email> [--role paid] [--file-limit 2GB] [--quota 50GB] [--reset-limits] [--force]"
            exit 1 ;;
    esac
done

# ─── 2. Size string → bytes (1GB, 512MB, 2.5GB … binary units) ──────────────
size_to_bytes() {
    local s
    s="$(echo "$1" | tr '[:upper:]' '[:lower:]')"   # lowercase (portable — ${1,,} breaks in some bash contexts)
    s="${s// /}"       # strip spaces
    local num unit
    num="$(echo "$s" | sed -E 's/^([0-9]+(\.[0-9]+)?).*/\1/')"
    unit="$(echo "$s" | sed -E 's/^[0-9]+(\.[0-9]+)?//')"
    case "$unit" in
        gb)  echo "$num" | awk '{printf "%d", $1 * 1073741824}' ;;
        mb)  echo "$num" | awk '{printf "%d", $1 * 1048576}' ;;
        b)   echo "$num" | awk '{printf "%d", $1}' ;;
        *)   return 1 ;;
    esac
}

if [[ -n "${FILE_LIMIT:-}" ]]; then
    FILE_BYTES="$(size_to_bytes "$FILE_LIMIT")" || { fail "Bad --file-limit '$FILE_LIMIT' (use e.g. 2GB, 512MB)"; exit 1; }
    [[ "$FILE_BYTES" -gt 0 ]] || { fail "Bad --file-limit '$FILE_LIMIT' (must be > 0)"; exit 1; }
fi
if [[ -n "${QUOTA:-}" ]]; then
    QUOTA_BYTES="$(size_to_bytes "$QUOTA")" || { fail "Bad --quota '$QUOTA' (use e.g. 50GB)"; exit 1; }
    [[ "$QUOTA_BYTES" -gt 0 ]] || { fail "Bad --quota '$QUOTA' (must be > 0)"; exit 1; }
fi

# ─── 3. Locate the DB (promote-admin.sh pattern) ────────────────────────────
if [[ ! -f .env ]]; then
    fail ".env not found in $PROJECT_ROOT"
    exit 1
fi
DB_URL="$(grep '^DATABASE_URL=' .env | head -1 | cut -d= -f2- | tr -d '"')"
DB_PATH="${DB_URL#sqlite:///}"
if [[ ! -f "$DB_PATH" ]]; then
    fail "DB file not found: $DB_PATH"
    exit 1
fi

# ─── 4. Show the table (with GB-formatted overrides) ─────────────────────────
hdr "📋 Current users"
info "DB: $DB_PATH"
sqlite3 -header -column "$DB_PATH" \
    "SELECT
        substr(user_id, 1, 16) AS user_id,
        email,
        CASE role
            WHEN 0 THEN 'ADMIN'
            WHEN 1 THEN 'PAID'
            WHEN 2 THEN 'FREE'
            ELSE 'UNKNOWN(' || role || ')'
        END AS role,
        CASE WHEN max_file_bytes IS NULL THEN '(tier)'
             ELSE printf('%.1f GB', max_file_bytes / 1073741824.0) END AS file_limit,
        CASE WHEN storage_quota_bytes IS NULL THEN '(tier)'
             ELSE printf('%.1f GB', storage_quota_bytes / 1073741824.0) END AS quota
     FROM users
     ORDER BY role ASC, created_at DESC;"

# No email → listing only (promote-admin.sh's convenience)
if [[ -z "$EMAIL" ]]; then
    echo ""
    info "No email given — listing only. Usage:"
    info "  bash scripts/promote-paid.sh <email> --role paid --file-limit 2GB --quota 50GB"
    exit 0
fi

# ─── 5. Look up the user (sign-in-first flow) ────────────────────────────────
hdr "🔍 Looking up: $EMAIL"
UID_ROW="$(sqlite3 "$DB_PATH" "SELECT user_id FROM users WHERE email='$EMAIL' LIMIT 1;" 2>/dev/null || echo "")"
if [[ -z "$UID_ROW" ]]; then
    fail "No user with email '$EMAIL' in DB"
    echo ""
    info "Have them sign in once first (the app auto-creates the row on first login), then re-run."
    info "Available emails:"
    sqlite3 "$DB_PATH" "SELECT '  - ' || email FROM users ORDER BY email;"
    exit 1
fi
ok "Found user"

# ─── 6. Snapshot BEFORE values (for the events audit row) ────────────────────
BEFORE_ROLE="$(sqlite3 "$DB_PATH" "SELECT role FROM users WHERE email='$EMAIL';")"
BEFORE_FILE="$(sqlite3 "$DB_PATH" "SELECT COALESCE(max_file_bytes, 'null') FROM users WHERE email='$EMAIL';")"
BEFORE_QUOTA="$(sqlite3 "$DB_PATH" "SELECT COALESCE(storage_quota_bytes, 'null') FROM users WHERE email='$EMAIL';")"

# ─── 7. Safety: refuse to LOWER a limit without --force ─────────────────────
maybe_block_lower() {
    local before="$1" new="$2" label="$3"
    [[ "$before" == "null" ]] && return 0   # tier default → an override is a raise (or equal)
    if [[ "$FORCE" -eq 0 ]] && [[ "$new" -lt "$before" ]]; then
        fail "Refusing to LOWER $label ($before → $new bytes). Use --force if this is intentional."
        exit 1
    fi
}
# Defaults so later guards never hit set -u (only used if the
# corresponding --flag was given; empty-string checks handle that).
FILE_BYTES="${FILE_BYTES:-}"
QUOTA_BYTES="${QUOTA_BYTES:-}"
[[ -n "${FILE_BYTES:-}" ]] && maybe_block_lower "$BEFORE_FILE" "$FILE_BYTES" "file limit"
[[ -n "${QUOTA_BYTES:-}" ]] && maybe_block_lower "$BEFORE_QUOTA" "$QUOTA_BYTES" "storage quota"

# ─── 8. Apply the changes ───────────────────────────────────────────────────
hdr "⚡ Applying"

if [[ -n "$ROLE" ]]; then
    case "$ROLE" in
        paid)  ROLE_INT=1 ;;
        admin) ROLE_INT=0 ;;
        free)  ROLE_INT=2 ;;
    esac
    if [[ "$ROLE" == "free" && "$FORCE" -eq 0 ]]; then
        # Downgrading to free is allowed (it's the beta exit), but warn loudly.
        warn "Downgrading '$EMAIL' to FREE — their uploads stop working. Continuing (--role free)."
    fi
    sqlite3 "$DB_PATH" "UPDATE users SET role=$ROLE_INT WHERE email='$EMAIL';"
    ok "role → $ROLE"
fi

if [[ "$RESET_LIMITS" -eq 1 ]]; then
    sqlite3 "$DB_PATH" "UPDATE users SET max_file_bytes = NULL, storage_quota_bytes = NULL WHERE email='$EMAIL';"
    ok "limits → NULL (tier defaults)"
    FILE_BYTES="NULL"; QUOTA_BYTES="NULL"   # for the audit row
fi

if [[ -n "${FILE_BYTES:-}" && "$RESET_LIMITS" -eq 0 ]]; then
    sqlite3 "$DB_PATH" "UPDATE users SET max_file_bytes = $FILE_BYTES WHERE email='$EMAIL';"
    ok "max_file_bytes → $FILE_BYTES ($FILE_LIMIT)"
fi

if [[ -n "${QUOTA_BYTES:-}" && "$RESET_LIMITS" -eq 0 ]]; then
    sqlite3 "$DB_PATH" "UPDATE users SET storage_quota_bytes = $QUOTA_BYTES WHERE email='$EMAIL';"
    ok "storage_quota_bytes → $QUOTA_BYTES ($QUOTA)"
fi

# ─── 9. The events-table audit row (the automated flip-kit.md trail) ────────
# Same shape the app's log_event writes (uuid id, ts default, level/source/
# message, user_id, context_json). One row per flip invocation, BEFORE→AFTER.
AFTER_ROLE="$(sqlite3 "$DB_PATH" "SELECT role FROM users WHERE email='$EMAIL';")"
AFTER_FILE="$(sqlite3 "$DB_PATH" "SELECT COALESCE(max_file_bytes, 'null') FROM users WHERE email='$EMAIL';")"
AFTER_QUOTA="$(sqlite3 "$DB_PATH" "SELECT COALESCE(storage_quota_bytes, 'null') FROM users WHERE email='$EMAIL';")"

CTX="{\"email\": \"$EMAIL\", \"role\": [$BEFORE_ROLE, $AFTER_ROLE], \"file_bytes\": [$BEFORE_FILE, $AFTER_FILE], \"quota_bytes\": [$BEFORE_QUOTA, $AFTER_QUOTA], \"via\": \"promote-paid.sh\"}"
sqlite3 "$DB_PATH" "INSERT INTO events (id, ts, level, source, message, user_id, context_json) VALUES (lower(hex(randomblob(16))), CURRENT_TIMESTAMP, 'INFO', 'admin.flip', 'limit override applied', '$UID_ROW', '$CTX');"
ok "audit row written to events (source=admin.flip)"

# ─── 10. Verify + show ───────────────────────────────────────────────────────
hdr "📋 Result"
sqlite3 -header -column "$DB_PATH" \
    "SELECT
        email,
        CASE role
            WHEN 0 THEN 'ADMIN' WHEN 1 THEN 'PAID' WHEN 2 THEN 'FREE'
            ELSE 'UNKNOWN(' || role || ')'
        END AS role,
        CASE WHEN max_file_bytes IS NULL THEN '(tier)'
             ELSE printf('%.1f GB', max_file_bytes / 1073741824.0) END AS file_limit,
        CASE WHEN storage_quota_bytes IS NULL THEN '(tier)'
             ELSE printf('%.1f GB', storage_quota_bytes / 1073741824.0) END AS quota
     FROM users WHERE email='$EMAIL';"

echo ""
info "No restart needed — takes effect on the user's next request."
info "Their /usage page (hard-refresh) shows the new numbers immediately."
hdr "Done"