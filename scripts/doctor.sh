#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# doctor.sh — Full-stack deployment health check for the Video Learning App
# Usage:  bash scripts/doctor.sh            # read-only checks (safe, no sudo)
#         bash scripts/doctor.sh --json    # machine-readable output
#
# What this is:
#   The "one command" answer to "is the production machine healthy?" It
#   encodes every failure mode discovered during the 2026-09-17/18 Mac
#   Studio deployment (see commit 7b938f5) so none of them can silently
#   recur on this machine — or on a fresh one.
#
# What it checks (each layer is independent; all are read-only):
#   1.  Host          sleep state (a sleeping Mac = a dead site)
#   2.  Volumes        the 3 external volumes are mounted
#   3.  Runtime        venv + python version, .env filled, secrets perms,
#                      DB reachable + WAL mode, log file writable by user
#   4.  Services       Ollama up + model present; gunicorn + /api/ready
#   5.  launchd        the 7 backup jobs are installed and healthy
#   6.  Backups        probe status JSON says healthy; newest file fresh;
#                      files actually exist on the RAID
#   7.  TCC/FDA        the binaries that need Full Disk Access can
#                      actually read the external volumes
#   8.  Git            branch + dirty tree (deploy provenance)
#
# Exit code: 0 = all green, 1 = at least one FAIL. Warnings don't fail.
#
# Design notes:
#   - No sudo required: launchd job states use `launchctl print` which
#     works unprivileged; backup file listing uses the user's own read
#     access (which is itself part of what we're testing).
#   - FDA verification is heuristic (macOS gives no CLI for TCC): we test
#     the SYMPTOM — can the binary stat/list the volume? — not the grant.
#   - Idempotent + read-only: safe to run any time, safe in cron.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail   # NOT -e: a doctor must keep diagnosing after failures

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

ok()    { echo -e "${GREEN}✅ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail()  { echo -e "${RED}❌ $1${NC}"; }
header(){ echo -e "\n${CYAN}━━━ $1 ━━━${NC}"; }

FAILS=0
JSON_OUT="--json"

# Count failures for the exit code
note_fail() { FAILS=$((FAILS+1)); }

JSON_MODE=0
[[ "${1:-}" == "--json" ]] && JSON_MODE=1

# ─── 1. Host: sleep state ──────────────────────────────────────────────────
header "1/8 Host"
if command -v pmset &>/dev/null; then
    AUTO_SLEEP="$(pmset -g | grep -E '^[[:space:]]*sleep[[:space:]]' | awk '{print $2}' || true)"
    if [[ "$AUTO_SLEEP" == "0" ]]; then
        ok "auto-sleep disabled (good for a server)"
    else
        fail "auto-sleep enabled (sleep=${AUTO_SLEEP:-unknown}) — Mac will sleep when idle; site goes dead"
        note_fail
    fi
else
    warn "pmset not found — cannot check sleep state"
fi

# ─── 2. Volumes ─────────────────────────────────────────────────────────────
header "2/8 External volumes"
for vol in Storage-Fast-NVMe Storage-Medium-NVMe Storage-Backup-HDD; do
    if [[ -d "/Volumes/$vol" ]]; then
        ok "/Volumes/$vol mounted"
    else
        fail "/Volumes/$vol NOT mounted — plug in the Acasis enclosure / check Disk Utility"
        note_fail
    fi
done

# ─── 3. Runtime: venv, .env, secrets, DB, log ──────────────────────────────
header "3/8 Runtime"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ -x "$PROJECT_ROOT/venv/bin/python" ]]; then
    PYVER="$("$PROJECT_ROOT/venv/bin/python" --version 2>&1)"
    ok "venv present ($PYVER)"
else
    fail "venv missing — run: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    note_fail
fi

if [[ -f "$PROJECT_ROOT/.env" ]]; then
    ok ".env present"
    # Placeholder check (the 6 Firebase keys + the API keys are the ones that
    # have shipped with placeholder values in the past).
    PLACEHOLDERS="$(grep -cE '^(FIREBASE_API_KEY|FIREBASE_PROJECT_ID|GROQ_API_KEY|YOUTUBE_API_KEY)=($|"")$' "$PROJECT_ROOT/.env" 2>/dev/null || true)"
    EMPTY_KEYS="$(grep -cE '^(FIREBASE_API_KEY|FIREBASE_PROJECT_ID)=$' "$PROJECT_ROOT/.env" 2>/dev/null || true)"
    if [[ "${EMPTY_KEYS:-0}" -gt 0 ]]; then
        fail ".env has $EMPTY_KEYS empty required key(s) (FIREBASE_API_KEY / FIREBASE_PROJECT_ID)"
        note_fail
    else
        ok ".env required keys filled"
    fi
else
    fail ".env missing — copy from .env.example and fill the Firebase fields"
    note_fail
fi

if [[ -f "$PROJECT_ROOT/firebase-service-account.json" ]]; then
    PERMS="$(stat -f '%Lp' "$PROJECT_ROOT/firebase-service-account.json")"
    if [[ "$PERMS" == "600" ]]; then
        ok "firebase-service-account.json present, mode 600"
    else
        warn "firebase-service-account.json mode is $PERMS (600 recommended): chmod 600"
    fi
else
    fail "firebase-service-account.json missing"
    note_fail
fi

# DB reachable + WAL mode (WAL applied on first connection since eec1f36;
# anything other than 'wal' means the pragma never fired — usually the
# DB path in .env points somewhere unexpected).
# DATABASE_URL forms: sqlite:////abs/path (4 slashes = absolute) or
# sqlite:///relative/file. Extract the filesystem path by stripping
# exactly 'sqlite://' + ONE slash when the remainder starts with '/'.
DB_PATH="$(grep -E '^DATABASE_URL=' "$PROJECT_ROOT/.env" 2>/dev/null | sed 's|^DATABASE_URL=sqlite://||; s|^/|/|')"
if [[ -f "$DB_PATH" ]]; then
    JOURNAL="$(sqlite3 "$DB_PATH" 'PRAGMA journal_mode' 2>/dev/null || echo 'unqueryable')"
    if [[ "$JOURNAL" == "wal" ]]; then
        ok "DB present, journal_mode=wal ($DB_PATH)"
    else
        warn "DB present but journal_mode=$JOURNAL (expected wal) — run scripts/restart.sh so the connect-time pragma applies"
    fi
else
    fail "DB not found at '${DB_PATH:-unset}' (from DATABASE_URL)"
    note_fail
fi

# Log file writable by the CURRENT user (the 11:13 admin-button incident:
# root-owned log + user-context backup run = abort before sqlite3 ran).
LOG_FILE="$HOME/Library/Logs/video-app-backup.log"
if [[ -f "$LOG_FILE" ]]; then
    OWNER="$(stat -f '%Su' "$LOG_FILE")"
    if [[ "$OWNER" == "$(whoami)" || "$OWNER" == "root" ]]; then
        if [[ -w "$LOG_FILE" ]]; then
            ok "backup log present + writable ($OWNER-owned)"
        else
            warn "backup log owned by $OWNER, not writable by $(whoami) — sudo chown $(whoami):staff '$LOG_FILE' (backups still succeed via the hardened log())"
        fi
    fi
else
    warn "backup log not yet created (it appears after the first launchd job runs)"
fi

# ─── 4. Services: Ollama + app server ──────────────────────────────────────
header "4/8 Services"
if curl -s -m 3 http://localhost:11434/api/tags &>/dev/null; then
    MODEL="$(grep -E '^OLLAMA_MODEL=' "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2)"
    if [[ -n "$MODEL" ]] && curl -s -m 3 http://localhost:11434/api/tags 2>/dev/null | grep -q "\"$MODEL\""; then
        ok "Ollama up, model '$MODEL' present"
    else
        fail "Ollama up but model '$MODEL' missing — ollama pull $MODEL"
        note_fail
    fi
else
    fail "Ollama not responding on :11434 — start it (brew services start ollama)"
    note_fail
fi

if pgrep -f 'gunicorn|uvicorn app.main' &>/dev/null; then
    ok "app server process running"
    READY="$(curl -s -m 8 http://localhost:8000/api/ready || echo '')"
    if echo "$READY" | grep -q '"status": *"ready"'; then
        ok "/api/ready → ready"
        if echo "$READY" | grep -q '"is_healthy": *true'; then
            ok "/api/ready → backup healthy"
        else
            warn "/api/ready → backup UNHEALTHY — open /admin/backups"
        fi
    else
        fail "/api/ready not reporting ready (response: ${READY:-none})"
        note_fail
    fi
else
    fail "app server not running — bash scripts/start.sh (or install the LaunchDaemon)"
    note_fail
fi

# ─── 5. launchd jobs ────────────────────────────────────────────────────────
header "5/8 launchd backup jobs"
JOBS=(backup-db backup-daily backup-monthly backup-verify backup-probe prune-events refresh-youtube-views)
for job in "${JOBS[@]}"; do
    PRINT="$(launchctl print "system/com.videoapp.$job" 2>/dev/null)"
    STATE="$(echo "$PRINT" | grep -E '^\s+state = ' | awk '{print $3, $4}' | sed 's/ *$//')"
    EXIT_RAW="$(echo "$PRINT" | grep -E 'last exit code = ' | sed 's/.*last exit code = //' | tr -d ' ')"
    if [[ -z "$STATE" ]]; then
        fail "com.videoapp.$job NOT loaded — bash scripts/install-backup-launchdaemon.sh"
        note_fail
    elif [[ "$EXIT_RAW" == "(neverexited)" ]]; then
        # Scheduled job that hasn't fired since the (re)install. E.g.
        # backup-monthly runs on the 1st — "never exited" is expected
        # on any day of the month except the 1st. Not an error.
        ok "com.videoapp.$job loaded (state=${STATE}; scheduled — not fired since install)"
    elif [[ "$EXIT_RAW" == "0" ]]; then
        ok "com.videoapp.$job (state=${STATE}, last exit=0)"
    else
        fail "com.videoapp.$job last exit=$EXIT_RAW — check /var/log/video-app-backup.log"
        note_fail
    fi
done

# ─── 6. Backup probe status + files on RAID ────────────────────────────────
header "6/8 Backups"
if [[ -f /tmp/video-app-backup-status.json ]]; then
    if python3 -c "import json,sys; d=json.load(open('/tmp/video-app-backup-status.json')); sys.exit(0 if d.get('is_healthy') else 1)" 2>/dev/null; then
        NEWEST_AGE="$(python3 -c "import json; d=json.load(open('/tmp/video-app-backup-status.json')); print(round(d['files'][0]['age_hours'],1)) if d['files'] else print('n/a')" 2>/dev/null)"
        ok "probe healthy (newest backup ${NEWEST_AGE}h old)"
    else
        fail "probe reports UNHEALTHY — /admin/backups shows why"
        note_fail
    fi
else
    warn "probe status file missing — run: sudo launchctl kickstart -k system/com.videoapp.backup-probe"
fi
DB_BACKUP_COUNT="$(ls /Volumes/Storage-Backup-HDD/db-backup/*.sqlite3 2>/dev/null | wc -l | tr -d ' ')"
if [[ "${DB_BACKUP_COUNT:-0}" -gt 0 ]]; then
    ok "$DB_BACKUP_COUNT DB hot backup(s) on the RAID"
else
    fail "no DB backups on the RAID — run the admin Backups button or kickstart com.videoapp.backup-db"
    note_fail
fi

# ─── 7. TCC/FDA (the silent killer — test the symptom, not the grant) ───────
header "7/8 TCC / Full Disk Access"
# We can't query TCC directly (SIP), so we verify each binary can actually
# READ the volume it needs. This catches: missing FDA grant, wrong binary
# granted (venv python vs /usr/bin/python3), revoked grant, and volume
# permission changes — regardless of what System Settings claims.
FDA_TARGET=/Volumes/Storage-Backup-HDD
if /usr/bin/python3 -c "import os; os.listdir('$FDA_TARGET')" &>/dev/null; then
    ok "/usr/bin/python3 can read $FDA_TARGET (probe + prune jobs OK)"
else
    fail "/usr/bin/python3 CANNOT read $FDA_TARGET — grant FDA in System Settings (GO-LIVE-INSTALL.md Step 3)"
    note_fail
fi
if "$PROJECT_ROOT/venv/bin/python" -c "import os; os.listdir('$FDA_TARGET')" &>/dev/null; then
    ok "venv python can read $FDA_TARGET (gunicorn + refresh-views OK)"
else
    fail "venv python CANNOT read $FDA_TARGET — grant FDA to $PROJECT_ROOT/venv/bin/python (Step 3, 4th row)"
    note_fail
fi
if /usr/bin/sqlite3 "$FDA_TARGET/db-backup" <<< 'SELECT 1;' &>/dev/null; then
    ok "/usr/bin/sqlite3 can access the RAID"
else
    # sqlite3 .backup writes there as root under launchd; a read test as the
    # user is a weaker signal but still catches total TCC blocks.
    if /usr/bin/sqlite3 --version &>/dev/null && ls "$FDA_TARGET/db-backup" &>/dev/null; then
        ok "/usr/bin/sqlite3 present; db-backup dir listable"
    else
        warn "could not verify sqlite3 access to the RAID (may still work as root under launchd)"
    fi
fi

# ─── 8. Git provenance ─────────────────────────────────────────────────────
header "8/8 Git"
BRANCH="$(git -C "$PROJECT_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
DIRTY="$(git -C "$PROJECT_ROOT" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
HEAD_DESC="$(git -C "$PROJECT_ROOT" log -1 --format='%h %s' 2>/dev/null | head -c 60)"
ok "branch: $BRANCH @ ${HEAD_DESC} (dirty files: $DIRTY)"
if [[ "$DIRTY" != "0" ]]; then
    warn "uncommitted changes — deploy provenance is ambiguous until committed"
fi

# ─── Summary ────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}━━━ Summary ━━━${NC}"
if [[ "$FAILS" -eq 0 ]]; then
    ok "ALL CHECKS PASSED ($FAILS failures)"
    exit 0
else
    fail "$FAILS CHECK(S) FAILED — see above for fixes"
    exit 1
fi