#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# install-backup-launchdaemon.sh — Install backup LaunchDaemons in
# /Library/LaunchDaemons/ (system context, runs as root) so backup jobs
# can read external volumes.
#
# Why this exists:
#   macOS TCC blocks sqlite3 from reading /Volumes/Storage-Fast-NVMe/
#   when launchd runs scripts under user context (gui/501). The 4 backup
#   jobs + probe were silently failing with exit 126 since Aug 22.
#
#   Moving to system launchd (root) bypasses TCC. Trade-off: runs as root,
#   which we accept because the script only does sqlite3 .backup and
#   integrity_check — no user-data writes outside the RAID.
#
# Idempotent: safe to re-run. Unloads old version, copies new, reloads.
#
# Usage:
#   bash scripts/install-backup-launchdaemon.sh                  # install (asks sudo)
#   bash scripts/install-backup-launchdaemon.sh --uninstall      # remove only
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TEMPLATE_DIR="${PROJECT_DIR}/scripts/launchdaemons"
RUNTIME_DIR="$HOME/Library/Application Support/VideoApp/scripts/backup"
SYSTEM_PLIST_DIR="/Library/LaunchDaemons"

# Backup-related plists we manage in the system domain.
SYSTEM_PLISTS=(
    "com.videoapp.backup-db"
    "com.videoapp.backup-daily"
    "com.videoapp.backup-monthly"
    "com.videoapp.backup-verify"
    "com.videoapp.backup-probe"
)

# Same labels that we previously installed in the user domain — we unload
# these on install so they don't run alongside the system versions.
USER_PLISTS=(
    "com.videoapp.backup-db"
    "com.videoapp.backup-daily"
    "com.videoapp.backup-monthly"
    "com.videoapp.backup-verify"
    "com.videoapp.backup-probe"
)

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; }

usage() {
    cat <<EOF
Usage: bash scripts/install-backup-launchdaemon.sh [OPTIONS]

Options:
  --uninstall       Remove all system-domain plists
  --skip-user-clean Don't unload user-domain LaunchAgents (leave them alone)
  --dry-run         Show what would be done without doing it
EOF
}

UNINSTALL=0
SKIP_USER_CLEAN=0
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --uninstall) UNINSTALL=1 ;;
        --skip-user-clean) SKIP_USER_CLEAN=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help) usage; exit 0 ;;
        *) warn "Unknown option: $arg"; usage; exit 1 ;;
    esac
done

# ── Pre-flight ────────────────────────────────────────────────────────────
if [[ ! -d "$TEMPLATE_DIR" ]]; then
    fail "Template dir missing: $TEMPLATE_DIR"
    exit 1
fi
if [[ ! -d "$PROJECT_DIR/scripts/backup" ]]; then
    fail "Source backup scripts missing: $PROJECT_DIR/scripts/backup"
    exit 1
fi

# ── Sync scripts to TCC-clean runtime location ───────────────────────────
mkdir -p "$RUNTIME_DIR"
echo ""
echo "→ Syncing scripts → $RUNTIME_DIR"
if [[ $DRY_RUN -eq 0 ]]; then
    for f in "$PROJECT_DIR"/scripts/backup/*.sh; do
        [ -e "$f" ] || continue
        cp -f "$f" "$RUNTIME_DIR/"
        chmod +x "$RUNTIME_DIR/$(basename "$f")"
    done
    ok "Synced $(/bin/ls "$PROJECT_DIR/scripts/backup/"*.sh | /usr/bin/wc -l | /usr/bin/tr -d ' ') scripts"

    # Also sync the probe's python module. Root-launched launchd can't read
    # anything under ~/Desktop/ (TCC), so the probe script can't import
    # from PROJECT_ROOT/app/services/. We keep a standalone copy at the
    # TCC-clean runtime path so root can exec it directly.
    PROBE_RUNTIME_DIR="$HOME/Library/Application Support/VideoApp/scripts/backup/probe"
    mkdir -p "$PROBE_RUNTIME_DIR"
    cp -f "$PROJECT_DIR/app/services/backup_monitor.py" "$PROBE_RUNTIME_DIR/"
    ok "Synced probe module → $PROBE_RUNTIME_DIR"
else
    echo "  (dry-run, no copy)"
fi

# ── Ensure /var/log/video-app-backup.log exists ───────────────────────────
if [[ $DRY_RUN -eq 0 ]]; then
    sudo /bin/touch /var/log/video-app-backup.log 2>/dev/null \
        && sudo /bin/chmod 644 /var/log/video-app-backup.log \
        && ok "Log file ready: /var/log/video-app-backup.log" \
        || warn "Could not pre-create /var/log/video-app-backup.log (launchd will create it)"
fi

# ── Uninstall path ────────────────────────────────────────────────────────
if [[ $UNINSTALL -eq 1 ]]; then
    echo ""
    echo "→ Uninstalling system-domain LaunchDaemons"
    for label in "${SYSTEM_PLISTS[@]}"; do
        plist="${SYSTEM_PLIST_DIR}/${label}.plist"
        if [[ -f "$plist" ]]; then
            if [[ $DRY_RUN -eq 0 ]]; then
                sudo /bin/launchctl bootout "system/${label}" 2>/dev/null || true
                sudo /bin/rm -f "$plist"
            fi
            ok "Removed $plist"
        fi
    done
    echo ""
    ok "Uninstall complete. Reinstall with:"
    echo "  bash scripts/install-backup-launchdaemon.sh"
    echo "  bash scripts/setup-backups.sh   # restore user-domain as fallback"
    exit 0
fi

# ── Install path ──────────────────────────────────────────────────────────
echo ""
echo "→ Installing system-domain LaunchDaemons (requires sudo)"

# Step 1: unload user-domain LaunchAgents with same labels (avoid duplicates)
if [[ $SKIP_USER_CLEAN -eq 0 ]]; then
    echo ""
    echo "→ Step 1: unload user-domain LaunchAgents (same labels)"
    for label in "${USER_PLISTS[@]}"; do
        user_plist="$HOME/Library/LaunchAgents/${label}.plist"
        if [[ -f "$user_plist" ]]; then
            if [[ $DRY_RUN -eq 0 ]]; then
                /bin/launchctl unload "$user_plist" 2>/dev/null || true
            fi
            ok "Unloaded user-domain: $label"
        fi
    done
fi

# Step 2: substitute, write, chown, bootstrap
echo ""
echo "→ Step 2: bootstrap system-domain LaunchDaemons"
for label in "${SYSTEM_PLISTS[@]}"; do
    template="${TEMPLATE_DIR}/${label}.plist"
    target="${SYSTEM_PLIST_DIR}/${label}.plist"

    if [[ ! -f "$template" ]]; then
        warn "Template missing: $template (skipping)"
        continue
    fi

    if [[ $DRY_RUN -eq 0 ]]; then
        # Build the rendered plist in /tmp (user-writable), then move into
        # /Library/LaunchDaemons/ with sudo. Writing directly to the system
        # plist dir fails because that path is root-only.
        rendered="/tmp/${label}.rendered.plist"
        /usr/bin/sed \
            -e "s|__RUNTIME_DIR__|${RUNTIME_DIR}|g" \
            -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
            "$template" > "$rendered"
        sudo /bin/mv "$rendered" "$target"
        sudo /usr/sbin/chown root:wheel "$target"
        sudo /bin/chmod 644 "$target"

        # Unload if already loaded (idempotent), then bootstrap into system.
        sudo /bin/launchctl bootout "system/${label}" 2>/dev/null || true
        sudo /bin/launchctl bootstrap system "$target"
    fi
    ok "Installed $label"
done

echo ""
ok "All LaunchDaemons installed. Verify with:"
echo "  sudo launchctl print system/com.videoapp.backup-db | grep -E 'state|last exit'"
echo "  curl -s http://localhost:8000/api/ready | python3 -m json.tool"
echo ""
echo "Uninstall with:"
echo "  bash scripts/install-backup-launchdaemon.sh --uninstall"
